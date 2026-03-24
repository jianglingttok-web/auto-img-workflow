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
        self.notifier = FeishuNotifier.from_env()

    def process_event(self, event_payload: dict[str, Any]) -> dict[str, Any]:
        event = self._extract_event(event_payload)
        sender_open_id = self._extract_sender_open_id(event)
        text = self._extract_message_text(event)
        decision = self.parse_review_text(text)
        task_dir = self._resolve_task_dir(event, sender_open_id, text)
        review_stage = self._resolve_review_stage(task_dir)
        current_round = self._resolve_round_number(task_dir)
        self._ensure_review_open(task_dir, decision=decision, review_stage=review_stage, current_round=current_round)

        progress_feedback: dict[str, Any] = {}
        progress_feedback["received"] = self._safe_send_progress_feedback(
            task_dir,
            sender_open_id=sender_open_id,
            decision=decision,
            review_stage=review_stage,
            current_round=current_round,
            phase="received",
        )

        try:
            result = self.apply_review_decision(
                task_dir,
                decision,
                raw_event=event_payload,
                sender_open_id=sender_open_id,
                review_stage=review_stage,
                current_round=current_round,
            )
        except Exception as exc:
            progress_feedback["failed"] = self._safe_send_progress_feedback(
                task_dir,
                sender_open_id=sender_open_id,
                decision=decision,
                review_stage=review_stage,
                current_round=current_round,
                phase="failed",
                error_message=str(exc),
            )
            raise

        progress_result = result.pop("progress_feedback", None)
        if progress_result is not None:
            progress_feedback["started"] = progress_result
        result["progress_feedback"] = progress_feedback
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
            decision = "rejected"
        elif approved:
            decision = "approved"
        else:
            decision = "rejected"

        note = self._extract_note(normalized, decision)
        if decision == "rejected" and not note:
            note = normalized
        return ReviewDecision(decision=decision, note=note, source_text=normalized)

    def apply_review_decision(
        self,
        task_dir: Path,
        decision: ReviewDecision,
        *,
        raw_event: dict[str, Any],
        sender_open_id: str,
        review_stage: str | None = None,
        current_round: int | None = None,
    ) -> dict[str, Any]:
        manager = TaskManager(task_dir.parent)
        task_mode = self._load_task_mode(task_dir)
        resolved_review_stage = review_stage or self._resolve_review_stage(task_dir)
        resolved_round = int(current_round or self._resolve_round_number(task_dir))
        manifest = manager.load_manifest(task_dir)
        current_status = str(manifest.get("status", "product_created") or "product_created")
        if current_status == "product_created":
            manager.advance_status(task_dir, "image_generation_pending", note="prepared from Feishu message event")
            current_status = "image_generation_pending"
        if current_status == "image_generation_pending":
            manager.advance_status(task_dir, "image_review_pending", note="entered image review from Feishu message event")

        rework_payload = self._normalize_rework_feedback(
            task_dir,
            review_stage=resolved_review_stage,
            current_round=resolved_round,
            decision=decision,
        )
        effective_note = str(rework_payload.get("review_note", decision.note or decision.source_text) or "").strip()
        effective_rework_reason = str(rework_payload.get("rework_reason", effective_note) or effective_note).strip()

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = str(manifest.get("status", "") or "")
        if decision.decision == "approved" and resulting_status == "image_review_pending":
            manager.advance_status(task_dir, "image_review_passed", note=effective_note or "Feishu message review passed")
        elif decision.decision == "rejected" and resulting_status == "image_review_pending":
            manager.advance_status(task_dir, "manual_check_pending", note=effective_note or "Feishu message review rejected")

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = str(manifest.get("status", "") or "")
        local_review_status = "passed" if decision.decision == "approved" else "rejected"
        manifest.setdefault("reviews", {})["image_review"] = local_review_status
        manifest["updated_at"] = utc_now_iso()
        manifest["reviews"].setdefault("review_logs", []).append(
            {
                "timestamp": utc_now_iso(),
                "source": "feishu_message_event",
                "status": decision.decision,
                "note": effective_note,
                "sender_open_id": sender_open_id,
                "message_text": decision.source_text,
                "review_stage": resolved_review_stage,
                "round": resolved_round,
                "rework_payload": rework_payload,
            }
        )
        manifest.setdefault("events", []).append(
            {
                "timestamp": utc_now_iso(),
                "event": "feishu_message_review_processed",
                "detail": {
                    "task_id": task_dir.name,
                    "decision": decision.decision,
                    "note": effective_note,
                    "sender_open_id": sender_open_id,
                    "review_stage": resolved_review_stage,
                    "round": resolved_round,
                    "rework_payload": rework_payload,
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
                "note": effective_note,
                "message_text": decision.source_text,
                "rework_payload": rework_payload,
                "raw_event": raw_event,
            },
        )

        follow_up: dict[str, Any] | None = None
        auto_delivery: dict[str, Any] | None = None
        auto_delivery_error = ""
        progress_feedback: dict[str, Any] | None = None

        if decision.decision == "approved":
            if task_mode == "full_set" and resolved_review_stage == "main":
                progress_feedback = self._safe_send_progress_feedback(
                    task_dir,
                    sender_open_id=sender_open_id,
                    decision=decision,
                    review_stage=resolved_review_stage,
                    current_round=resolved_round,
                    phase="follow_up_started",
                    next_stage="sub",
                    next_round=resolved_round,
                )
                follow_up = self._run_local_stage(task_dir, round_number=resolved_round, stage="sub", rework_reason="")
            elif self.auto_deliver_passed_reviews:
                progress_feedback = self._safe_send_progress_feedback(
                    task_dir,
                    sender_open_id=sender_open_id,
                    decision=decision,
                    review_stage=resolved_review_stage,
                    current_round=resolved_round,
                    phase="delivery_started",
                )
                try:
                    auto_delivery = self._deliver_approved_assets(task_dir, fallback_open_id=sender_open_id)
                    try:
                        manager.advance_status(task_dir, "completed", note="Feishu message review delivered")
                    except Exception:
                        pass
                except Exception as exc:
                    auto_delivery_error = str(exc)
        else:
            rerun_stage = "sub" if resolved_review_stage == "sub" or task_mode == "sub_only" else "main"
            progress_feedback = self._safe_send_progress_feedback(
                task_dir,
                sender_open_id=sender_open_id,
                decision=decision,
                review_stage=resolved_review_stage,
                current_round=resolved_round,
                phase="follow_up_started",
                next_stage=rerun_stage,
                next_round=resolved_round + 1,
            )
            follow_up = self._run_local_stage(
                task_dir,
                round_number=resolved_round + 1,
                stage=rerun_stage,
                rework_reason=effective_rework_reason,
            )

        manifest = read_json(task_dir / "manifest.json")
        resulting_status = manifest.get("status", "")
        result = {
            "ok": True,
            "task_id": task_dir.name,
            "decision": decision.decision,
            "review_note": effective_note,
            "rework_payload": rework_payload,
            "review_stage": resolved_review_stage,
            "task_mode": task_mode,
            "current_round": resolved_round,
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
        if progress_feedback is not None:
            result["progress_feedback"] = progress_feedback
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

        latest_task = self._select_latest_pending_task(pending_matches)
        if latest_task is not None:
            return latest_task

        raise ValueError(
            f"multiple pending image-review tasks found for sender {sender_open_id}; include task id in message"
        )

    def _select_latest_pending_task(self, task_dirs: list[Path]) -> Path | None:
        ranked: list[tuple[str, str, Path]] = []
        for task_dir in task_dirs:
            manifest_path = task_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = read_json(manifest_path)
            latest_notification = ""
            for entry in manifest.get("events", []):
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("event", "") or "") != "review_notification_sent":
                    continue
                timestamp = str(entry.get("timestamp", "") or "")
                if timestamp and timestamp > latest_notification:
                    latest_notification = timestamp
            score = latest_notification or str(manifest.get("updated_at", "") or "") or task_dir.name
            ranked.append((score, task_dir.name, task_dir))

        if not ranked:
            return None
        ranked.sort(key=lambda item: (item[0], item[1]))
        return ranked[-1][2]

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
        tokens = set(_APPROVE_PATTERNS if decision == "approved" else _REJECT_PATTERNS)
        for token in sorted(tokens, key=len, reverse=True):
            cleaned = cleaned.replace(token, " ")
        cleaned = re.sub(r"^[\s:：,，;；.-]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned
    def _normalize_rework_feedback(
        self,
        task_dir: Path,
        *,
        review_stage: str,
        current_round: int,
        decision: ReviewDecision,
    ) -> dict[str, Any]:
        fallback_note = str(decision.note or decision.source_text or "").strip()
        payload = {
            "mode": "direct_feedback",
            "review_note": fallback_note,
            "rework_reason": fallback_note,
            "model_used": False,
        }
        if decision.decision != "rejected":
            return payload

        try:
            from ..executors.openclaw import OpenClawExecutor

            image_task_path = task_dir / "image_task.json"
            image_task = read_json(image_task_path) if image_task_path.is_file() else {}
            executor = OpenClawExecutor(task_dir)
            if executor.text_config is None:
                return payload

            model_input = {
                "task_id": task_dir.name,
                "product_name": image_task.get("product_name", task_dir.name),
                "site": image_task.get("target_market", ""),
                "task_mode": image_task.get("task_mode", ""),
                "review_stage": review_stage,
                "current_round": current_round,
                "selling_points": image_task.get("selling_points", []),
                "style_requirements": image_task.get("style", []),
                "compliance_requirements": image_task.get("compliance_rules", []),
                "notes": image_task.get("notes", ""),
                "user_feedback": decision.source_text,
            }
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You convert image-review feedback into a concise production instruction for the next image generation round.\n"
                        "Return JSON only.\n"
                        "Required keys: action, normalized_feedback, production_instruction, must_keep, avoid.\n"
                        "Rules:\n"
                        "1. action must always be rework.\n"
                        "2. Keep normalized_feedback concise and faithful to the user's intent.\n"
                        "3. production_instruction must be directly executable for an image-generation workflow.\n"
                        "4. must_keep and avoid must be arrays of short strings.\n"
                        "Example: {\"action\":\"rework\",\"normalized_feedback\":\"...\",\"production_instruction\":\"...\",\"must_keep\":[\"...\"],\"avoid\":[\"...\"]}"
                    ),
                },
                {"role": "user", "content": json.dumps(model_input, ensure_ascii=False, indent=2)},
            ]
            response = executor._call_text_api(messages)
            content = executor._extract_text_content(response)
            normalized = executor._parse_json_object(content)
            normalized_feedback = str(normalized.get("normalized_feedback", "") or fallback_note).strip() or fallback_note
            production_instruction = str(normalized.get("production_instruction", "") or normalized_feedback).strip() or normalized_feedback
            must_keep = [str(item).strip() for item in normalized.get("must_keep", []) if str(item).strip()]
            avoid = [str(item).strip() for item in normalized.get("avoid", []) if str(item).strip()]
            instruction_parts = [production_instruction]
            if must_keep:
                instruction_parts.append("Must keep: " + "; ".join(must_keep))
            if avoid:
                instruction_parts.append("Avoid: " + "; ".join(avoid))
            payload.update(
                {
                    "mode": "ark_text_review_rewrite",
                    "review_note": normalized_feedback,
                    "rework_reason": "\n".join(part for part in instruction_parts if part).strip(),
                    "model_used": True,
                    "model": executor.text_config.model,
                    "raw_model_output": normalized,
                }
            )
            return payload
        except Exception as exc:
            payload["model_error"] = str(exc)
            return payload

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
        manifest = read_json(task_dir / "manifest.json")
        review_meta = manifest.get("reviews", {}) if isinstance(manifest, dict) else {}
        pending_stage = str(review_meta.get("pending_stage", "") or "").strip().lower()
        pending_round = review_meta.get("pending_round")
        if pending_stage in {"main", "sub"} and pending_round == round_number:
            return pending_stage

        notification_path = task_dir / "review" / f"round_{round_number:02d}_feishu_notification_result.json"
        if notification_path.is_file():
            payload = read_json(notification_path).get("payload", {})
            if str(payload.get("sub_contact_sheet_preview", "") or "").strip():
                return "sub"
            if str(payload.get("main_preview", "") or "").strip():
                return "main"

            workflow_status = str(payload.get("workflow_status", "") or "")
            if "副图" in workflow_status:
                return "sub"
            if "主图" in workflow_status:
                return "main"

        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        sub_dir = round_dir / "sub"
        main_dir = round_dir / "main"
        has_sub = sub_dir.is_dir() and any(path.is_file() for path in sub_dir.iterdir())
        has_main = main_dir.is_dir() and any(path.is_file() for path in main_dir.iterdir())
        if has_sub:
            return "sub"
        if has_main:
            return "main"
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
        candidates: list[int] = []
        manifest = read_json(task_dir / "manifest.json")

        outputs_round = manifest.get("outputs", {}).get("image_assets", {}).get("round")
        if isinstance(outputs_round, int) and outputs_round > 0:
            candidates.append(outputs_round)

        pending_round = manifest.get("reviews", {}).get("pending_round")
        if isinstance(pending_round, int) and pending_round > 0:
            candidates.append(pending_round)

        image_assets_path = task_dir / "image_assets.json"
        if image_assets_path.is_file():
            image_assets = read_json(image_assets_path)
            generation_round = image_assets.get("generation_meta", {}).get("round")
            if isinstance(generation_round, int) and generation_round > 0:
                candidates.append(generation_round)

        media_dir = task_dir / "media"
        if media_dir.exists():
            for path in media_dir.iterdir():
                if not path.is_dir() or not path.name.startswith("round_"):
                    continue
                suffix = path.name.removeprefix("round_")
                if suffix.isdigit():
                    candidates.append(int(suffix))

        review_dir = task_dir / "review"
        if review_dir.exists():
            for path in review_dir.iterdir():
                if not path.name.startswith("round_"):
                    continue
                parts = path.name.split("_", 2)
                if len(parts) >= 2 and parts[1].isdigit():
                    candidates.append(int(parts[1]))

        for entry in manifest.get("reviews", {}).get("review_logs", []):
            round_value = entry.get("round")
            if isinstance(round_value, int) and round_value > 0:
                candidates.append(round_value)

        for entry in manifest.get("events", []):
            detail = entry.get("detail", {}) if isinstance(entry, dict) else {}
            round_value = detail.get("round")
            if isinstance(round_value, int) and round_value > 0:
                candidates.append(round_value)

        if candidates:
            return max(candidates)
        raise FileNotFoundError(f"unable to resolve generated round for task: {task_dir}")

    def _ensure_review_open(self, task_dir: Path, *, decision: ReviewDecision, review_stage: str, current_round: int) -> None:
        manifest = read_json(task_dir / "manifest.json")
        review_meta = manifest.get("reviews", {}) if isinstance(manifest, dict) else {}
        current_status = str(manifest.get("status", "") or "")
        if current_status == "completed":
            raise ValueError(f"task {task_dir.name} is already completed")
        if current_status == "image_review_passed":
            raise ValueError(f"task {task_dir.name} current review is already approved")

        raw_record_path = task_dir / "intake" / "feishu_record_raw.json"
        if raw_record_path.is_file():
            raw_record = read_json(raw_record_path)
            fields = raw_record.get("fields", {}) if isinstance(raw_record, dict) else {}
            table_task_status = self._stringify_field_value(fields.get("图片任务状态"))
            table_review_status = self._stringify_field_value(fields.get("图片审核状态"))
            if table_task_status in {"已通过", "已交付"} or table_review_status in {"已通过", "审核通过"}:
                raise ValueError(f"task {task_dir.name} is already marked passed in Feishu table")

        pending_stage = str(review_meta.get("pending_stage", "") or "").strip().lower()
        pending_round = review_meta.get("pending_round")
        if pending_stage in {"main", "sub"} and isinstance(pending_round, int):
            if pending_round != current_round or pending_stage != review_stage:
                raise ValueError(
                    f"task {task_dir.name} active review moved to round {pending_round} {pending_stage}; ignore stale message for round {current_round} {review_stage}"
                )

    def _stringify_field_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            for item in value:
                rendered = self._stringify_field_value(item)
                if rendered:
                    return rendered
            return ""
        if isinstance(value, dict):
            for key in ("text", "name", "value", "label", "en_name"):
                rendered = value.get(key)
                if rendered not in (None, ""):
                    return self._stringify_field_value(rendered)
            return ""
        return str(value).strip()

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

    def _safe_send_progress_feedback(
        self,
        task_dir: Path,
        *,
        sender_open_id: str,
        decision: ReviewDecision,
        review_stage: str,
        current_round: int,
        phase: str,
        next_stage: str = "",
        next_round: int | None = None,
        error_message: str = "",
    ) -> dict[str, Any]:
        text = self._build_progress_text(
            task_id=task_dir.name,
            decision=decision,
            review_stage=review_stage,
            current_round=current_round,
            phase=phase,
            next_stage=next_stage,
            next_round=next_round,
            error_message=error_message,
        )
        review_dir = task_dir / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(review_dir.glob(f"round_{current_round:02d}_feishu_progress_{phase}_{review_stage}_*.json"))
        result_path = review_dir / f"round_{current_round:02d}_feishu_progress_{phase}_{review_stage}_{len(existing) + 1:03d}.json"
        payload = {
            "timestamp": utc_now_iso(),
            "task_id": task_dir.name,
            "phase": phase,
            "decision": decision.decision,
            "review_stage": review_stage,
            "current_round": current_round,
            "next_stage": next_stage,
            "next_round": next_round,
            "receive_id": sender_open_id,
            "text": text,
        }
        try:
            send_result = self.notifier.notify_text(receive_id=sender_open_id, text=text)
            payload["ok"] = True
            payload["send_result"] = send_result
        except Exception as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
        write_json(result_path, payload)
        payload["result_path"] = str(result_path)
        return payload

    def _build_progress_text(
        self,
        *,
        task_id: str,
        decision: ReviewDecision,
        review_stage: str,
        current_round: int,
        phase: str,
        next_stage: str,
        next_round: int | None,
        error_message: str,
    ) -> str:
        stage_label = self._render_stage_label(review_stage)
        next_stage_label = self._render_stage_label(next_stage) if next_stage else ""

        if phase == "received":
            if decision.decision == "approved":
                return f"已收到 {task_id} 第{current_round}轮{stage_label}通过意见，正在处理后续流程。"
            return f"已收到 {task_id} 第{current_round}轮{stage_label}修改意见，正在解析并准备重做。"

        if phase == "follow_up_started":
            target_round = int(next_round or current_round)
            if decision.decision == "approved":
                return f"已启动 {task_id} 第{target_round}轮{next_stage_label}生成，完成后会自动发送预览。"
            return f"已启动 {task_id} 第{target_round}轮{next_stage_label}重做，完成后会自动发送预览。"

        if phase == "delivery_started":
            return f"已收到 {task_id} 第{current_round}轮通过意见，正在整理并发送交付资料。"

        if phase == "failed":
            brief_error = str(error_message or "未知错误").strip()
            return f"已收到 {task_id} 的审核消息，但自动处理失败：{brief_error}。请稍后重试或人工检查。"

        return f"已收到 {task_id} 的审核消息，正在处理中。"

    def _render_stage_label(self, stage: str) -> str:
        normalized = str(stage or "").strip().lower()
        if normalized == "main":
            return "主图"
        if normalized == "sub":
            return "副图"
        return "图片"



