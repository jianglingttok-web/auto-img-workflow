from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..data.build_listing_package import ListingPackageBuilder
from ..models import utc_now_iso
from ..storage import read_json, write_json
from ..task_manager import TaskManager
from .feishu_notifier import FeishuNotifier

_APPROVE_PATTERNS = (
    "审核通过",
    "已通过",
    "通过",
    "ok",
    "pass",
    "approve",
    "approved",
)
_REJECT_PATTERNS = (
    "审核不通过",
    "不通过",
    "已打回",
    "打回",
    "驳回",
    "reject",
    "rejected",
)
_NOTE_PREFIXES = ("审核意见", "意见", "备注", "原因", "说明")
_TASK_ID_PATTERN = re.compile(r"\b([A-Za-z]+\d{2,}|[A-Za-z]+-\d{4,})\b")


@dataclass(slots=True)
class ReviewDecision:
    decision: str
    note: str
    source_text: str


class FeishuMessageReviewProcessor:
    def __init__(self, tasks_root: Path, *, auto_deliver_passed_reviews: bool = True) -> None:
        self.tasks_root = Path(tasks_root)
        self.auto_deliver_passed_reviews = auto_deliver_passed_reviews

    def process_event(self, event_payload: dict[str, Any]) -> dict[str, Any]:
        event = self._extract_event(event_payload)
        sender_open_id = self._extract_sender_open_id(event)
        text = self._extract_message_text(event)
        decision = self.parse_review_text(text)
        task_dir = self._resolve_task_dir(event, sender_open_id, text)
        result = self.apply_review_decision(task_dir, decision, raw_event=event_payload, sender_open_id=sender_open_id)
        result["sender_open_id"] = sender_open_id
        result["message_text"] = text
        return result

    def parse_review_text(self, text: str) -> ReviewDecision:
        normalized = str(text or "").strip()
        if not normalized:
            raise ValueError("empty Feishu review message")

        lowered = normalized.lower()
        rejected = any(pattern in normalized or pattern in lowered for pattern in _REJECT_PATTERNS)
        approved = any(pattern in normalized or pattern in lowered for pattern in _APPROVE_PATTERNS)

        if rejected:
            decision = "已打回"
        elif approved:
            decision = "已通过"
        else:
            raise ValueError(f"unable to infer review decision from message: {normalized}")

        note = self._extract_note(normalized, decision)
        return ReviewDecision(decision=decision, note=note, source_text=normalized)

    def apply_review_decision(
        self,
        task_dir: Path,
        decision: ReviewDecision,
        *,
        raw_event: dict[str, Any],
        sender_open_id: str,
    ) -> dict[str, Any]:
        manager = TaskManager(task_dir.parent)
        task_mode = self._load_task_mode(task_dir)
        review_stage = self._resolve_review_stage(task_dir)
        current_round = self._resolve_round_number(task_dir)
        manifest = manager.load_manifest(task_dir)
        current_status = manifest.get("status", "product_created")
        if current_status == "product_created":
            manager.advance_status(task_dir, "image_generation_pending", note="prepared from Feishu message event")
            current_status = "image_generation_pending"
        if current_status == "image_generation_pending":
            manager.advance_status(task_dir, "image_review_pending", note="entered image review from Feishu message event")

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = manifest.get("status", "")
        if decision.decision == "已通过" and resulting_status == "image_review_pending":
            manager.advance_status(task_dir, "image_review_passed", note=decision.note or "Feishu message review passed")
        elif decision.decision == "已打回" and resulting_status == "image_review_pending":
            manager.advance_status(task_dir, "manual_check_pending", note=decision.note or "Feishu message review rejected")

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = manifest.get("status", "")
        local_review_status = "passed" if decision.decision == "已通过" else "rejected"
        manifest.setdefault("reviews", {})["image_review"] = local_review_status
        manifest["updated_at"] = utc_now_iso()
        manifest["reviews"].setdefault("review_logs", []).append(
            {
                "timestamp": utc_now_iso(),
                "source": "feishu_message_event",
                "status": decision.decision,
                "note": decision.note,
                "sender_open_id": sender_open_id,
                "message_text": decision.source_text,
                "review_stage": review_stage,
                "round": current_round,
            }
        )
        manifest.setdefault("events", []).append(
            {
                "timestamp": utc_now_iso(),
                "event": "feishu_message_review_processed",
                "detail": {
                    "task_id": task_dir.name,
                    "decision": decision.decision,
                    "note": decision.note,
                    "sender_open_id": sender_open_id,
                    "review_stage": review_stage,
                    "round": current_round,
                },
            }
        )
        write_json(task_dir / "manifest.json", manifest)
        package = ListingPackageBuilder().build(task_dir)

        event_dir = task_dir / "review" / "message_events"
        event_dir.mkdir(parents=True, exist_ok=True)
        event_index = len(list(event_dir.glob("*.json"))) + 1
        event_path = event_dir / f"event_{event_index:03d}.json"
        write_json(
            event_path,
            {
                "sender_open_id": sender_open_id,
                "decision": decision.decision,
                "note": decision.note,
                "message_text": decision.source_text,
                "raw_event": raw_event,
            },
        )

        follow_up: dict[str, Any] | None = None
        auto_delivery: dict[str, Any] | None = None
        auto_delivery_error = ""

        if decision.decision == "已通过":
            if task_mode == "full_set" and review_stage == "main":
                follow_up = self._run_local_stage(task_dir, round_number=current_round, stage="sub", rework_reason="")
            elif self.auto_deliver_passed_reviews:
                try:
                    auto_delivery = self._deliver_approved_assets(task_dir, fallback_open_id=sender_open_id)
                    try:
                        manager.advance_status(task_dir, "completed", note="Feishu message review delivered")
                    except Exception:
                        pass
                except Exception as exc:
                    auto_delivery_error = str(exc)
        else:
            rerun_stage = "sub" if review_stage == "sub" or task_mode == "sub_only" else "main"
            follow_up = self._run_local_stage(
                task_dir,
                round_number=current_round + 1,
                stage=rerun_stage,
                rework_reason=decision.note or decision.source_text,
            )

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = manifest.get("status", "")
        result = {
            "ok": True,
            "task_id": task_dir.name,
            "decision": decision.decision,
            "review_note": decision.note,
            "review_stage": review_stage,
            "task_mode": task_mode,
            "current_round": current_round,
            "local_workflow_status": resulting_status,
            "local_review_status": local_review_status,
            "listing_package_review_status": package.get("review_status", {}),
            "event_path": str(event_path),
        }
        if follow_up is not None:
            result["follow_up"] = follow_up
        if auto_delivery is not None:
            result["auto_delivery"] = auto_delivery
        if auto_delivery_error:
            result["auto_delivery_error"] = auto_delivery_error
        return result

    def _extract_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schema") == "2.0":
            return payload.get("event", {})
        return payload.get("event", payload)

    def _extract_sender_open_id(self, event: dict[str, Any]) -> str:
        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {}) if isinstance(sender, dict) else {}
        for key in ("open_id", "user_id", "union_id"):
            value = sender_id.get(key)
            if value:
                return str(value)
        raise ValueError("unable to resolve sender open_id from event payload")

    def _extract_message_text(self, event: dict[str, Any]) -> str:
        message = event.get("message", {})
        if str(message.get("message_type", "")) != "text":
            raise ValueError(f"unsupported message_type: {message.get('message_type', '')}")
        content = message.get("content", "")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return content.strip()
            if isinstance(parsed, dict):
                return str(parsed.get("text", "") or "").strip()
        if isinstance(content, dict):
            return str(content.get("text", "") or "").strip()
        return str(content or "").strip()

    def _resolve_task_dir(self, event: dict[str, Any], sender_open_id: str, text: str) -> Path:
        explicit_task_id = self._extract_task_id(text)
        if explicit_task_id:
            candidate = self.tasks_root / explicit_task_id
            if candidate.exists():
                return candidate

        pending_matches: list[Path] = []
        for task_dir in sorted(self.tasks_root.iterdir()):
            if not task_dir.is_dir():
                continue
            manifest_path = task_dir / "manifest.json"
            raw_record_path = task_dir / "intake" / "feishu_record_raw.json"
            if not manifest_path.exists() or not raw_record_path.exists():
                continue
            manifest = read_json(manifest_path)
            if manifest.get("status") != "image_review_pending":
                continue
            raw_record = read_json(raw_record_path)
            if self._record_has_person(raw_record.get("fields", {}), sender_open_id):
                pending_matches.append(task_dir)

        if len(pending_matches) == 1:
            return pending_matches[0]
        if not pending_matches:
            raise ValueError(f"no pending image-review task found for sender {sender_open_id}; include task id in message")
        raise ValueError(
            f"multiple pending image-review tasks found for sender {sender_open_id}; include task id in message"
        )

    def _extract_task_id(self, text: str) -> str:
        for match in _TASK_ID_PATTERN.findall(text):
            candidate = str(match).strip()
            if candidate:
                return candidate
        return ""

    def _record_has_person(self, fields: dict[str, Any], sender_open_id: str) -> bool:
        for field_name in ("提交人", "审核人"):
            value = fields.get(field_name)
            items = value if isinstance(value, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                person_id = str(item.get("id", "") or item.get("open_id", "") or item.get("user_id", "") or "")
                if person_id and person_id == sender_open_id:
                    return True
        return False

    def _extract_note(self, text: str, decision: str) -> str:
        note_candidate = text
        for prefix in _NOTE_PREFIXES:
            marker_index = note_candidate.find(prefix)
            if marker_index >= 0:
                suffix = note_candidate[marker_index + len(prefix):].lstrip("：: ,-，")
                return suffix.strip()

        cleaned = note_candidate
        tokens = set(_APPROVE_PATTERNS if decision == "已通过" else _REJECT_PATTERNS)
        for token in sorted(tokens, key=len, reverse=True):
            cleaned = cleaned.replace(token, " ")
        cleaned = re.sub(r"^[\s:：,，;；.-]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _load_task_mode(self, task_dir: Path) -> str:
        image_task_path = task_dir / "image_task.json"
        if not image_task_path.is_file():
            raise FileNotFoundError(f"missing image_task.json for task: {task_dir}")
        payload = read_json(image_task_path)
        task_mode = str(payload.get("task_mode", "") or "")
        if not task_mode:
            raise ValueError(f"missing task_mode in image_task.json: {task_dir}")
        return task_mode

    def _resolve_review_stage(self, task_dir: Path) -> str:
        round_number = self._resolve_round_number(task_dir)
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        sub_dir = round_dir / "sub"
        if sub_dir.is_dir() and any(path.is_file() for path in sub_dir.iterdir()):
            return "sub"
        manifest = read_json(task_dir / "manifest.json")
        sub_count = int(manifest.get("outputs", {}).get("image_assets", {}).get("sub_count", 0) or 0)
        if sub_count > 0:
            return "sub"
        return "main"

    def _run_local_stage(self, task_dir: Path, *, round_number: int, stage: str, rework_reason: str) -> dict[str, Any]:
        from ..worker import LocalFeishuImageWorker

        raw_record_path = task_dir / "intake" / "feishu_record_raw.json"
        if not raw_record_path.is_file():
            raise FileNotFoundError(f"missing Feishu raw record for task: {task_dir}")
        record = read_json(raw_record_path)
        worker = LocalFeishuImageWorker(self.tasks_root, task_id_filter=task_dir.name, sync_bitable_record=False)
        return worker._run_generation_stage(
            task_dir=task_dir,
            record=record,
            round_number=round_number,
            stage=stage,
            rework_reason=rework_reason,
        )

    def _deliver_approved_assets(self, task_dir: Path, *, fallback_open_id: str) -> dict[str, Any]:
        round_number = self._resolve_round_number(task_dir)
        recipient = self._resolve_delivery_recipient(task_dir, fallback_open_id=fallback_open_id)
        notifier = FeishuNotifier.from_env()
        bundle_path = self._build_round_suite_bundle(task_dir, round_number)
        payload = notifier.build_image_delivery_payload(
            task_id=task_dir.name,
            product_name=self._load_product_name(task_dir),
            round_number=round_number,
            task_dir=task_dir,
            bundle_path=str(bundle_path),
            delivery_status="已交付",
            workflow_status="image_review_passed",
            receiver_open_id=recipient["receive_id"],
            receiver_name=recipient["name"],
        )
        result = notifier.notify_image_delivery(receive_id=recipient["receive_id"], payload=payload)
        result["recipient"] = recipient
        result_path = task_dir / "review" / f"round_{round_number:02d}_feishu_delivery_result.json"
        write_json(result_path, result)

        manifest = read_json(task_dir / "manifest.json")
        manifest["updated_at"] = utc_now_iso()
        manifest.setdefault("events", []).append(
            {
                "timestamp": utc_now_iso(),
                "event": "feishu_delivery_sent",
                "detail": {
                    "task_id": task_dir.name,
                    "round": round_number,
                    "receive_id": recipient["receive_id"],
                },
            }
        )
        write_json(task_dir / "manifest.json", manifest)
        return {
            "round": round_number,
            "result_path": str(result_path),
            "recipient": recipient,
        }

    def _resolve_round_number(self, task_dir: Path) -> int:
        manifest = read_json(task_dir / "manifest.json")
        round_number = manifest.get("outputs", {}).get("image_assets", {}).get("round")
        if isinstance(round_number, int) and round_number > 0:
            return round_number

        media_dir = task_dir / "media"
        candidates: list[int] = []
        if media_dir.exists():
            for path in media_dir.iterdir():
                if not path.is_dir() or not path.name.startswith("round_"):
                    continue
                suffix = path.name.removeprefix("round_")
                if suffix.isdigit():
                    candidates.append(int(suffix))
        if candidates:
            return max(candidates)
        raise FileNotFoundError(f"unable to resolve generated round for task: {task_dir}")

    def _resolve_delivery_recipient(self, task_dir: Path, *, fallback_open_id: str) -> dict[str, str]:
        raw_record_path = task_dir / "intake" / "feishu_record_raw.json"
        if raw_record_path.is_file():
            raw_record = read_json(raw_record_path)
            fields = raw_record.get("fields", {}) if isinstance(raw_record, dict) else {}
            for field_name in ("提交人", "审核人"):
                person = self._extract_person_from_field(fields, field_name)
                if person:
                    return person
        if fallback_open_id:
            return {
                "field_name": "message_sender",
                "receive_id": fallback_open_id,
                "name": "",
            }
        raise ValueError(f"unable to resolve delivery recipient for task: {task_dir}")

    def _extract_person_from_field(self, fields: dict[str, Any], field_name: str) -> dict[str, str] | None:
        value = fields.get(field_name)
        if not isinstance(value, list):
            return None
        for item in value:
            if not isinstance(item, dict):
                continue
            receive_id = str(item.get("id", "") or item.get("open_id", "") or item.get("user_id", "") or "")
            if not receive_id:
                continue
            return {
                "field_name": field_name,
                "receive_id": receive_id,
                "name": str(item.get("name", "") or item.get("en_name", "") or ""),
            }
        return None

    def _load_product_name(self, task_dir: Path) -> str:
        for file_name in ("image_task.json", "product_brief.json"):
            path = task_dir / file_name
            if not path.is_file():
                continue
            payload = read_json(path)
            product_name = str(payload.get("product_name", "") or "")
            if product_name:
                return product_name
        return task_dir.name

    def _build_round_suite_bundle(self, task_dir: Path, round_number: int) -> Path:
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        bundle_path = task_dir / "review" / f"round_{round_number:02d}_suite_bundle.zip"
        candidates: list[Path] = []
        for directory_name in ("main", "sub", "preview"):
            directory = round_dir / directory_name
            if not directory.is_dir():
                continue
            candidates.extend(sorted(path for path in directory.iterdir() if path.is_file()))

        if not candidates:
            raise FileNotFoundError(f"no round assets found to bundle: {round_dir}")

        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in candidates:
                archive.write(path, arcname=str(path.relative_to(round_dir)))
        return bundle_path