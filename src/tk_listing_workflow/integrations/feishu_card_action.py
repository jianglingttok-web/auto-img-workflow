from __future__ import annotations

from pathlib import Path
from typing import Any

from .feishu_message_review import FeishuMessageReviewProcessor, ReviewDecision


class FeishuCardActionProcessor:
    def __init__(self, tasks_root: Path, *, auto_deliver_passed_reviews: bool = True) -> None:
        self.tasks_root = Path(tasks_root)
        self.message_processor = FeishuMessageReviewProcessor(
            self.tasks_root,
            auto_deliver_passed_reviews=auto_deliver_passed_reviews,
        )

    def process_event(self, event_payload: dict[str, Any]) -> dict[str, Any]:
        event = self._extract_event(event_payload)
        action = event.get("action", {}) if isinstance(event, dict) else {}
        action_value = action.get("value", {}) if isinstance(action, dict) else {}
        action_name = str(action_value.get("action", "") or "").strip().lower()
        task_id = str(action_value.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("card action missing task_id")

        task_dir = self.tasks_root / task_id
        if not task_dir.is_dir():
            raise FileNotFoundError(f"card action task not found: {task_id}")

        sender_open_id = self._extract_operator_open_id(event)
        review_stage = str(action_value.get("stage", "") or "").strip().lower() or self.message_processor._resolve_review_stage(task_dir)
        current_round = self._coerce_positive_int(action_value.get("round"), default=self.message_processor._resolve_round_number(task_dir))
        feedback = self._extract_feedback(action)
        decision = self._build_decision(action_name, task_id=task_id, review_stage=review_stage, feedback=feedback)

        self.message_processor._ensure_review_open(
            task_dir,
            decision=decision,
            review_stage=review_stage,
            current_round=current_round,
        )
        result = self.message_processor.apply_review_decision(
            task_dir,
            decision,
            raw_event=event_payload,
            sender_open_id=sender_open_id,
            review_stage=review_stage,
            current_round=current_round,
        )
        result["action_name"] = action_name
        result["action_feedback"] = feedback
        result["source"] = "feishu_card_action"
        return result

    def _extract_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("schema") == "2.0":
            return payload.get("event", {})
        return payload.get("event", payload)

    def _extract_operator_open_id(self, event: dict[str, Any]) -> str:
        operator = event.get("operator", {}) if isinstance(event, dict) else {}
        for key in ("open_id", "user_id", "union_id"):
            value = operator.get(key)
            if value:
                return str(value)
        raise ValueError("card action missing operator open_id")

    def _extract_feedback(self, action: dict[str, Any]) -> str:
        form_value = action.get("form_value", {}) if isinstance(action, dict) else {}
        if isinstance(form_value, dict):
            rendered = self._stringify_value(form_value.get("feedback"))
            if rendered:
                return rendered
        rendered_input = self._stringify_value(action.get("input_value"))
        if rendered_input:
            return rendered_input
        return ""

    def _build_decision(self, action_name: str, *, task_id: str, review_stage: str, feedback: str) -> ReviewDecision:
        normalized_stage = "??" if review_stage == "main" else "??"
        if action_name == "approve":
            source_text = feedback or f"?? {task_id}"
            return ReviewDecision(decision="approved", note=feedback, source_text=source_text)
        if action_name in {"rework", "rework_main", "rework_sub"}:
            default_note = f"{normalized_stage}????"
            source_text = feedback or default_note
            return ReviewDecision(decision="rejected", note=feedback or default_note, source_text=source_text)
        raise ValueError(f"unsupported card action: {action_name}")

    def _coerce_positive_int(self, value: Any, default: int) -> int:
        try:
            number = int(str(value).strip())
        except Exception:
            return int(default)
        return number if number > 0 else int(default)

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("value", "text", "content"):
                rendered = value.get(key)
                if rendered not in (None, ""):
                    return self._stringify_value(rendered)
            return ""
        if isinstance(value, list):
            parts = [self._stringify_value(item) for item in value]
            return " ".join(part for part in parts if part).strip()
        return str(value).strip()
