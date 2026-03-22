from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


DEFAULT_FEISHU_BASE_URL = "https://open.feishu.cn"


@dataclass(slots=True)
class FeishuBitableConfig:
    base_url: str = DEFAULT_FEISHU_BASE_URL
    app_id: str = ""
    app_secret: str = ""
    app_token: str = ""
    table_id: str = ""
    view_id: str = ""

    @classmethod
    def from_env(cls) -> "FeishuBitableConfig":
        from ..config import bootstrap_runtime_environment

        bootstrap_runtime_environment()
        return cls(
            base_url=os.environ.get("FEISHU_BASE_URL", DEFAULT_FEISHU_BASE_URL) or DEFAULT_FEISHU_BASE_URL,
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            app_token=os.environ.get("FEISHU_IMAGE_TASK_APP_TOKEN", ""),
            table_id=os.environ.get("FEISHU_IMAGE_TASK_TABLE_ID", ""),
            view_id=os.environ.get("FEISHU_IMAGE_TASK_VIEW_ID", ""),
        )

    def validate(self) -> None:
        missing: list[str] = []
        if not self.app_id:
            missing.append("FEISHU_APP_ID")
        if not self.app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not self.app_token:
            missing.append("FEISHU_IMAGE_TASK_APP_TOKEN")
        if not self.table_id:
            missing.append("FEISHU_IMAGE_TASK_TABLE_ID")
        if missing:
            raise ValueError(f"missing Feishu config: {', '.join(missing)}")


class FeishuBitableClient:
    def __init__(self, config: FeishuBitableConfig) -> None:
        config.validate()
        self.config = config
        self._tenant_access_token: str | None = None

    @classmethod
    def from_env(cls) -> "FeishuBitableClient":
        return cls(FeishuBitableConfig.from_env())

    def list_records(self, *, page_size: int = 10, page_token: str = "", view_id: str = "") -> dict[str, Any]:
        query: dict[str, Any] = {"page_size": max(1, min(page_size, 500))}
        if page_token:
            query["page_token"] = page_token
        resolved_view_id = view_id or self.config.view_id
        if resolved_view_id:
            query["view_id"] = resolved_view_id

        payload = self._request_json(
            method="GET",
            path=f"/open-apis/bitable/v1/apps/{self.config.app_token}/tables/{self.config.table_id}/records",
            access_token=self._get_tenant_access_token(),
            query=query,
        )
        data = payload.get("data", {})
        return {
            "has_more": bool(data.get("has_more", False)),
            "page_token": str(data.get("page_token", "") or ""),
            "total": int(data.get("total", 0) or 0),
            "items": data.get("items", []),
        }

    def get_record(self, record_id: str) -> dict[str, Any]:
        payload = self._request_json(
            method="GET",
            path=f"/open-apis/bitable/v1/apps/{self.config.app_token}/tables/{self.config.table_id}/records/{record_id}",
            access_token=self._get_tenant_access_token(),
        )
        data = payload.get("data", {})
        record = data.get("record")
        if not isinstance(record, dict):
            raise RuntimeError(f"Feishu record payload missing record: {payload}")
        return record

    def update_record(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        payload = self._request_json(
            method="PUT",
            path=f"/open-apis/bitable/v1/apps/{self.config.app_token}/tables/{self.config.table_id}/records/{record_id}",
            access_token=self._get_tenant_access_token(),
            body={"fields": fields},
        )
        data = payload.get("data", {})
        record = data.get("record")
        if not isinstance(record, dict):
            raise RuntimeError(f"Feishu update response missing record: {payload}")
        return record

    def upload_media(self, file_path: Path, *, parent_type: str = "bitable_file", parent_node: str = "") -> dict[str, Any]:
        file_path = Path(file_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Feishu upload file not found: {file_path}")

        resolved_parent_node = parent_node or self.config.app_token
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        boundary = f"----tkworkflow{uuid4().hex}"
        body = self._encode_multipart(
            boundary,
            fields={
                "file_name": file_path.name,
                "parent_type": parent_type,
                "parent_node": resolved_parent_node,
                "size": str(file_path.stat().st_size),
            },
            file_field_name="file",
            file_name=file_path.name,
            file_bytes=file_path.read_bytes(),
            mime_type=mime_type,
        )
        payload = self._request_bytes(
            method="POST",
            url=f"{self.config.base_url.rstrip('/')}/open-apis/drive/v1/medias/upload_all",
            access_token=self._get_tenant_access_token(),
            raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        data = json.loads(payload.decode("utf-8"))
        if int(data.get("code", -1)) != 0:
            msg = data.get("msg", "unknown error")
            raise RuntimeError(f"Feishu API error {data.get('code')}: {msg} [/open-apis/drive/v1/medias/upload_all]")

        upload = data.get("data", {})
        file_token = str(upload.get("file_token", "") or "")
        if not file_token:
            raise RuntimeError(f"Feishu upload response missing file_token: {data}")
        return {
            "file_token": file_token,
            "name": str(upload.get("name", file_path.name) or file_path.name),
            "type": str(upload.get("type", mime_type) or mime_type),
            "size": int(upload.get("size", file_path.stat().st_size) or file_path.stat().st_size),
        }

    def download_attachment(self, attachment: dict[str, Any], target_path: Path) -> Path:
        file_token = str(attachment.get("file_token", "") or "")
        download_url = str(attachment.get("url", "") or "")
        if not download_url and file_token:
            download_url = f"{self.config.base_url.rstrip('/')}/open-apis/drive/v1/medias/{file_token}/download"
        if not download_url:
            raise ValueError(f"attachment missing downloadable url: {attachment}")

        payload = self._request_bytes(method="GET", url=download_url, access_token=self._get_tenant_access_token())
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        return target_path

    def _get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token

        payload = self._request_json(
            method="POST",
            path="/open-apis/auth/v3/tenant_access_token/internal",
            body={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
        )
        token = str(payload.get("tenant_access_token", "") or "")
        if not token:
            raise RuntimeError(f"Feishu token response missing tenant_access_token: {payload}")
        self._tenant_access_token = token
        return token

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        access_token: str = "",
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_url = self.config.base_url.rstrip("/")
        url = f"{base_url}{path}"
        if query:
            query_string = urlencode({key: value for key, value in query.items() if value not in (None, "")})
            if query_string:
                url = f"{url}?{query_string}"

        payload = self._request_bytes(
            method=method,
            url=url,
            access_token=access_token,
            body=body,
            content_type="application/json; charset=utf-8",
        )
        data = json.loads(payload.decode("utf-8"))
        if int(data.get("code", -1)) != 0:
            msg = data.get("msg", "unknown error")
            raise RuntimeError(f"Feishu API error {data.get('code')}: {msg} [{path}]")
        return data

    def _request_bytes(
        self,
        *,
        method: str,
        url: str,
        access_token: str = "",
        body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        if body is not None and raw_body is not None:
            raise ValueError("body and raw_body cannot be provided at the same time")

        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        data = raw_body
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        request = Request(url=url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Feishu API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Feishu API network error: {exc}") from exc

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
        chunks.append(
            f"Content-Disposition: form-data; name=\"{file_field_name}\"; filename=\"{file_name}\"\r\n".encode("utf-8")
        )
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(file_bytes)
        chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(chunks)