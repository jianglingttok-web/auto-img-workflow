from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from uuid import uuid4

from .feishu_bitable import FeishuBitableClient

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class FeishuNotifier:
    def __init__(self, client: FeishuBitableClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls) -> "FeishuNotifier":
        return cls(FeishuBitableClient.from_env())

    def send_stage_notification(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        receive_id = str(payload.get("receiver_open_id", "") or "")
        if not receive_id:
            raise ValueError(f"{stage} notification missing receiver_open_id")
        if stage == "image_review":
            return self.notify_image_review(receive_id=receive_id, payload=payload)
        if stage == "image_delivery":
            return self.notify_image_delivery(receive_id=receive_id, payload=payload)
        raise ValueError(f"unsupported Feishu notification stage: {stage}")

    def notify_text(self, *, receive_id: str, text: str, receive_id_type: str = "open_id") -> dict[str, Any]:
        rendered = str(text or "").strip()
        if not receive_id:
            raise ValueError("text notification missing receive_id")
        if not rendered:
            raise ValueError("text notification missing text")
        message = self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content={"text": rendered},
        )
        return {
            "ok": True,
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "text": rendered,
            "message": message,
        }

    def build_image_review_payload(
        self,
        *,
        task_id: str,
        product_name: str,
        round_number: int,
        task_dir: Path,
        bundle_link: str = "",
        review_status: str = "待审核",
        workflow_status: str = "待审核裂变图",
        table_link: str = "",
        receiver_open_id: str = "",
        receiver_name: str = "",
        review_stage: str = "main",
    ) -> dict[str, Any]:
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        main_preview = self._pick_first_file(round_dir / "preview", prefixes=("main_preview",))
        if not main_preview:
            main_preview = self._pick_first_file(round_dir / "main")
        sub_contact_sheet = self._pick_first_file(round_dir / "preview", prefixes=("sub_contact_sheet", "sub_grid", "sub_preview"))
        return {
            "task_id": task_id,
            "product_name": product_name,
            "current_round": round_number,
            "main_preview": main_preview,
            "sub_contact_sheet_preview": sub_contact_sheet,
            "bundle_link": bundle_link,
            "review_status": review_status,
            "workflow_status": workflow_status,
            "table_link": table_link,
            "receiver_open_id": receiver_open_id,
            "receiver_name": receiver_name,
            "review_stage": review_stage,
            "actions": ["通过", "重做"],
        }

    def build_image_delivery_payload(
        self,
        *,
        task_id: str,
        product_name: str,
        round_number: int,
        task_dir: Path,
        bundle_link: str = "",
        bundle_path: str = "",
        delivery_status: str = "已交付",
        workflow_status: str = "completed",
        receiver_open_id: str = "",
        receiver_name: str = "",
        include_images: bool = False,
        include_previews: bool = True,
    ) -> dict[str, Any]:
        image_files = self._collect_round_images(task_dir, round_number)
        if not image_files:
            raise FileNotFoundError(f"no generated images found for round {round_number}: {task_dir}")
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        main_preview = self._pick_first_file(round_dir / "preview", prefixes=("main_preview",))
        sub_contact_sheet = self._pick_first_file(round_dir / "preview", prefixes=("sub_contact_sheet", "sub_grid", "sub_preview"))
        main_images = [path for path in image_files if path.parent.name == "main"]
        sub_images = [path for path in image_files if path.parent.name == "sub"]
        return {
            "task_id": task_id,
            "product_name": product_name,
            "current_round": round_number,
            "bundle_link": bundle_link,
            "bundle_path": bundle_path,
            "delivery_status": delivery_status,
            "workflow_status": workflow_status,
            "receiver_open_id": receiver_open_id,
            "receiver_name": receiver_name,
            "image_files": [str(path) for path in image_files] if include_images else [],
            "main_preview": main_preview if include_previews else "",
            "sub_contact_sheet_preview": sub_contact_sheet if include_previews else "",
            "main_count": len(main_images),
            "sub_count": len(sub_images),
        }

    def notify_image_review(self, *, receive_id: str, payload: dict[str, Any], receive_id_type: str = "open_id") -> dict[str, Any]:
        card = self._build_image_review_card(payload)
        card_result = self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content={"card": json.dumps(card, ensure_ascii=False)},
        )
        image_results = self._send_image_files(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            image_paths=[
                Path(str(payload.get("main_preview", "") or "")),
                Path(str(payload.get("sub_contact_sheet_preview", "") or "")),
            ],
        )
        return {
            "ok": True,
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "card_message": card_result,
            "image_messages": image_results,
            "payload": payload,
        }

    def notify_image_delivery(self, *, receive_id: str, payload: dict[str, Any], receive_id_type: str = "open_id") -> dict[str, Any]:
        text = self._build_image_delivery_text(payload)
        text_result = self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content={"text": text},
        )
        file_message: dict[str, Any] | None = None
        bundle_path = Path(str(payload.get("bundle_path", "") or ""))
        if bundle_path.is_file():
            upload = self._upload_file(bundle_path)
            file_message = {
                "file_path": str(bundle_path),
                "upload": upload,
                "message": self._send_message(
                    receive_id=receive_id,
                    receive_id_type=receive_id_type,
                    msg_type="file",
                    content={"file_key": upload["file_key"]},
                ),
            }
        image_results = self._send_image_files(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            image_paths=[
                Path(str(payload.get("main_preview", "") or "")),
                Path(str(payload.get("sub_contact_sheet_preview", "") or "")),
                *[Path(path) for path in payload.get("image_files", [])],
            ],
        )
        return {
            "ok": True,
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "text_message": text_result,
            "file_message": file_message,
            "image_messages": image_results,
            "payload": payload,
        }

    def _build_image_review_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", "") or "")
        product_name = str(payload.get("product_name", "") or "")
        current_round = int(payload.get("current_round", 1) or 1)
        review_stage = str(payload.get("review_stage", "main") or "main").strip().lower()
        workflow_status = str(payload.get("workflow_status", "") or "")
        review_status = str(payload.get("review_status", "") or "")
        summary = (
            f"**任务ID**：{task_id}\n"
            f"**产品**：{product_name}\n"
            f"**轮次**：第{current_round}轮\n"
            f"**任务状态**：{workflow_status}\n"
            f"**审核状态**：{review_status}"
        )
        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "裂变图审核"},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {"tag": "markdown", "content": summary},
                    {
                        "tag": "note",
                        "elements": [
                            {"tag": "plain_text", "content": "直接点击通过或重做。重做时可在下方填写修改意见。"}
                        ],
                    },
                    {
                        "tag": "input",
                        "name": "feedback",
                        "label": {"tag": "plain_text", "content": "修改意见"},
                        "placeholder": {"tag": "plain_text", "content": "例如：产品太大，爱心控制在0.5cm直径左右"},
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "通过"},
                                "type": "primary",
                                "value": {
                                    "action": "approve",
                                    "task_id": task_id,
                                    "round": str(current_round),
                                    "stage": review_stage,
                                },
                            },
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "重做"},
                                "type": "default",
                                "value": {
                                    "action": "rework",
                                    "task_id": task_id,
                                    "round": str(current_round),
                                    "stage": review_stage,
                                },
                            },
                        ],
                    },
                ],
            },
        }

    def _build_image_delivery_text(self, payload: dict[str, Any]) -> str:
        lines = ["套图交付通知"]
        receiver_name = str(payload.get("receiver_name", "") or "")
        if receiver_name:
            lines.append(f"收件人：{receiver_name}")
        lines.extend(
            [
                f"任务ID：{payload.get('task_id', '')}",
                f"产品：{payload.get('product_name', '')}",
                f"轮次：第{payload.get('current_round', '')}轮",
                f"工作流状态：{payload.get('workflow_status', '')}",
                f"交付状态：{payload.get('delivery_status', '')}",
                f"本次交付：主图 {payload.get('main_count', 0)} 张，副图 {payload.get('sub_count', 0)} 张",
            ]
        )
        bundle_link = str(payload.get("bundle_link", "") or "")
        if bundle_link:
            lines.append(f"完整图片包：{bundle_link}")
        return "\n".join(lines)

    def _send_message(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.client._request_json(
            method="POST",
            path=f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            access_token=self.client._get_tenant_access_token(),
            body={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        data = payload.get("data", {})
        message = data.get("message", {})
        return {
            "message_id": str(message.get("message_id", "") or data.get("message_id", "") or ""),
            "raw": data,
        }

    def _send_image_files(self, *, receive_id: str, receive_id_type: str, image_paths: list[Path]) -> list[dict[str, Any]]:
        image_results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for image_path in image_paths:
            if not image_path.is_file():
                continue
            key = str(image_path.resolve())
            if key in seen:
                continue
            seen.add(key)
            upload = self._upload_image(image_path)
            image_results.append(
                {
                    "image_path": str(image_path),
                    "upload": upload,
                    "message": self._send_message(
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                        msg_type="image",
                        content={"image_key": upload["image_key"]},
                    ),
                }
            )
        return image_results

    def _upload_image(self, image_path: Path) -> dict[str, Any]:
        if not image_path.is_file():
            raise FileNotFoundError(f"Feishu IM image not found: {image_path}")
        mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        boundary = f"----tkfeishuim{uuid4().hex}"
        body = self._encode_multipart(
            boundary,
            fields={"image_type": "message"},
            file_field_name="image",
            file_name=image_path.name,
            file_bytes=image_path.read_bytes(),
            mime_type=mime_type,
        )
        payload = self.client._request_bytes(
            method="POST",
            url=f"{self.client.config.base_url.rstrip('/')}/open-apis/im/v1/images",
            access_token=self.client._get_tenant_access_token(),
            raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        data = json.loads(payload.decode("utf-8"))
        if int(data.get("code", -1)) != 0:
            msg = data.get("msg", "unknown error")
            raise RuntimeError(f"Feishu API error {data.get('code')}: {msg} [/open-apis/im/v1/images]")
        image_key = str(data.get("data", {}).get("image_key", "") or "")
        if not image_key:
            raise RuntimeError(f"Feishu IM upload response missing image_key: {data}")
        return {"image_key": image_key, "name": image_path.name}

    def _upload_file(self, file_path: Path) -> dict[str, Any]:
        if not file_path.is_file():
            raise FileNotFoundError(f"Feishu IM file not found: {file_path}")
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        boundary = f"----tkfeishufile{uuid4().hex}"
        body = self._encode_multipart(
            boundary,
            fields={
                "file_type": "stream",
                "file_name": file_path.name,
                "duration": "0",
            },
            file_field_name="file",
            file_name=file_path.name,
            file_bytes=file_path.read_bytes(),
            mime_type=mime_type,
        )
        payload = self.client._request_bytes(
            method="POST",
            url=f"{self.client.config.base_url.rstrip('/')}/open-apis/im/v1/files",
            access_token=self.client._get_tenant_access_token(),
            raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        data = json.loads(payload.decode("utf-8"))
        if int(data.get("code", -1)) != 0:
            msg = data.get("msg", "unknown error")
            raise RuntimeError(f"Feishu API error {data.get('code')}: {msg} [/open-apis/im/v1/files]")
        file_key = str(data.get("data", {}).get("file_key", "") or "")
        if not file_key:
            raise RuntimeError(f"Feishu IM upload response missing file_key: {data}")
        return {"file_key": file_key, "name": file_path.name}

    def _collect_round_images(self, task_dir: Path, round_number: int) -> list[Path]:
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        image_paths: list[Path] = []
        for directory_name in ("main", "sub"):
            directory = round_dir / directory_name
            if not directory.exists():
                continue
            image_paths.extend(
                path
                for path in sorted(directory.iterdir())
                if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
            )
        return image_paths

    def _encode_multipart(
        self,
        boundary: str,
        *,
        fields: dict[str, str],
        file_field_name: str,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> bytes:
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.append(f"--{boundary}\r\n".encode("utf-8"))
            chunks.append(f"Content-Disposition: form-data; name=\"{key}\"\r\n\r\n".encode("utf-8"))
            chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f"Content-Disposition: form-data; name=\"{file_field_name}\"; filename=\"{file_name}\"\r\n".encode("utf-8"))
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(file_bytes)
        chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(chunks)

    def _pick_first_file(self, directory: Path, prefixes: tuple[str, ...] = ()) -> str:
        if not directory.exists():
            return ""
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if prefixes and not any(path.stem.startswith(prefix) for prefix in prefixes):
                continue
            return str(path)
        if prefixes:
            return ""
        for path in sorted(directory.iterdir()):
            if path.is_file():
                return str(path)
        return ""
