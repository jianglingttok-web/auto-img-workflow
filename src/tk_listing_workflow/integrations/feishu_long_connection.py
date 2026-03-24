from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger, P2CardActionTriggerResponse
from lark_oapi.core.enum import LogLevel
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client

from ..models import utc_now_iso
from ..storage import write_json
from .feishu_card_action import FeishuCardActionProcessor
from .feishu_message_review import FeishuMessageReviewProcessor
from .feishu_notifier import FeishuNotifier

_LOGGER = logging.getLogger(__name__)
_LOG_LEVELS = {
    "CRITICAL": LogLevel.CRITICAL,
    "ERROR": LogLevel.ERROR,
    "WARNING": LogLevel.WARNING,
    "WARN": LogLevel.WARNING,
    "INFO": LogLevel.INFO,
    "DEBUG": LogLevel.DEBUG,
    "TRACE": LogLevel.DEBUG,
}
_TASK_ID_PATTERN = re.compile(r"\b([A-Za-z]+\d{2,}|[A-Za-z]+-\d{4,})\b")


class FeishuLongConnectionReceiver:
    def __init__(
        self,
        tasks_root: Path,
        *,
        app_id: str,
        app_secret: str,
        log_level: str = "INFO",
        auto_deliver_passed_reviews: bool = True,
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        if not self.app_id or not self.app_secret:
            raise ValueError("missing FEISHU_APP_ID or FEISHU_APP_SECRET for long-connection receiver")

        self.processor = FeishuMessageReviewProcessor(
            self.tasks_root,
            auto_deliver_passed_reviews=auto_deliver_passed_reviews,
        )
        self.card_processor = FeishuCardActionProcessor(
            self.tasks_root,
            auto_deliver_passed_reviews=auto_deliver_passed_reviews,
        )
        self.notifier = FeishuNotifier.from_env()
        self.processed_message_dir = self.tasks_root / '_processed_messages'
        self.processed_message_dir.mkdir(parents=True, exist_ok=True)
        self.processed_card_action_dir = self.tasks_root / '_processed_card_actions'
        self.processed_card_action_dir.mkdir(parents=True, exist_ok=True)
        self.log_level_name = str(log_level or "INFO").upper()
        self.log_level = _LOG_LEVELS.get(self.log_level_name, LogLevel.INFO)
        self.client = Client(
            self.app_id,
            self.app_secret,
            log_level=self.log_level,
            event_handler=self._build_event_handler(),
        )

    @classmethod
    def from_env(
        cls,
        tasks_root: Path,
        *,
        log_level: str = "INFO",
        auto_deliver_passed_reviews: bool = True,
    ) -> "FeishuLongConnectionReceiver":
        return cls(
            tasks_root,
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            log_level=log_level,
            auto_deliver_passed_reviews=auto_deliver_passed_reviews,
        )

    def serve(self) -> None:
        _LOGGER.info(
            "starting Feishu long-connection receiver: tasks_root=%s log_level=%s",
            self.tasks_root,
            self.log_level_name,
        )
        self.client.start()

    def _build_event_handler(self) -> EventDispatcherHandler:
        return (
            EventDispatcherHandler.builder("", "", level=self.log_level)
            .register_p2_im_message_receive_v1(self._handle_im_message_receive)
            .register_p2_card_action_trigger(self._handle_card_action_trigger)
            .build()
        )

def _handle_im_message_receive(self, payload: P2ImMessageReceiveV1) -> None:
    event_payload = self._to_event_payload(payload)
    snapshot: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "source": "feishu_long_connection",
        "payload": event_payload,
    }
    message_id = self._extract_message_id(event_payload)
    if message_id and not self._claim_message(message_id, event_payload):
        snapshot["ok"] = True
        snapshot["duplicate"] = True
        snapshot["message_id"] = message_id
        self._write_event_snapshot(snapshot)
        _LOGGER.info("skipping duplicate Feishu message event: message_id=%s", message_id)
        return
    try:
        result = self.processor.process_event(event_payload)
        snapshot["ok"] = True
        snapshot["result"] = result
        _LOGGER.info(
            "processed Feishu message review: task_id=%s decision=%s",
            result.get("task_id", ""),
            result.get("decision", ""),
        )
    except Exception as exc:
        snapshot["ok"] = False
        snapshot["error"] = str(exc)
        snapshot["error_feedback"] = self._safe_notify_processing_error(event_payload, str(exc))
        _LOGGER.exception("failed to process Feishu message review event")
    self._write_event_snapshot(snapshot)

def _handle_card_action_trigger(self, payload: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    event_payload = self._to_card_action_payload(payload)
    snapshot: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "source": "feishu_long_connection_card_action",
        "payload": event_payload,
    }
    action_token = self._extract_card_action_token(event_payload)
    if action_token and not self._claim_card_action(action_token, event_payload):
        snapshot["ok"] = True
        snapshot["duplicate"] = True
        snapshot["action_token"] = action_token
        self._write_event_snapshot(snapshot)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "?????????????"}})
    try:
        result = self.card_processor.process_event(event_payload)
        snapshot["ok"] = True
        snapshot["result"] = result
        self._write_event_snapshot(snapshot)
        return P2CardActionTriggerResponse({"toast": {"type": "success", "content": "????????"}})
    except Exception as exc:
        snapshot["ok"] = False
        snapshot["error"] = str(exc)
        self._write_event_snapshot(snapshot)
        _LOGGER.exception("failed to process Feishu card action")
        return P2CardActionTriggerResponse({"toast": {"type": "danger", "content": f"?????{str(exc)}"}})

    def _safe_notify_processing_error(self, event_payload: dict[str, Any], error_message: str) -> dict[str, Any]:
        sender_open_id = self._extract_sender_open_id(event_payload)
        if not sender_open_id:
            return {"ok": False, "error": "missing sender open_id"}

        text = self._build_processing_error_text(event_payload, error_message)
        try:
            result = self.notifier.notify_text(receive_id=sender_open_id, text=text)
            return {"ok": True, "text": text, "result": result}
        except Exception as exc:
            return {"ok": False, "text": text, "error": str(exc)}

    def _build_processing_error_text(self, event_payload: dict[str, Any], error_message: str) -> str:
        message_text = self._extract_message_text(event_payload)
        task_id = self._extract_task_id(message_text)
        brief = str(error_message or "未知错误").strip()
        if "multiple pending image-review tasks" in brief:
            return "已收到你的审核消息，但当前存在多个待审核任务。请在消息里带上任务ID，例如：打回 cck04 审核意见：主图比例太大。"
        if "no pending image-review task found" in brief:
            if task_id:
                return f"已收到你的审核消息，但没有找到可处理的待审核任务：{task_id}。请确认任务是否仍处于待审核状态。"
            return "已收到你的审核消息，但没有找到可处理的待审核任务。请带上任务ID重试，例如：通过 cck04。"
        if "unsupported message_type" in brief:
            return "已收到你的消息，但当前只支持文本审核意见。请直接发送文字，例如：通过 cck04 或 打回 cck04 审核意见：主图比例太大。"
        return f"已收到你的消息，但自动处理失败：{brief}。请稍后重试，或联系管理员检查本地工作流。"

    def _write_event_snapshot(self, payload: dict[str, Any]) -> Path:
        event_dir = self.tasks_root / "_callback_events"
        event_dir.mkdir(parents=True, exist_ok=True)
        event_index = len(list(event_dir.glob("event_*.json"))) + 1
        event_path = event_dir / f"event_{event_index:03d}.json"
        write_json(event_path, payload)
        return event_path

    def _claim_message(self, message_id: str, event_payload: dict[str, Any]) -> bool:
        marker = self.processed_message_dir / f"{message_id}.json"
        if marker.exists():
            return False
        write_json(
            marker,
            {
                "message_id": message_id,
                "timestamp": utc_now_iso(),
                "payload": event_payload,
            },
        )
        return True

    def _to_event_payload(self, payload: P2ImMessageReceiveV1) -> dict[str, Any]:
        event = payload.event
        sender = event.sender if event else None
        sender_id = sender.sender_id if sender else None
        message = event.message if event else None
        return {
            "schema": "2.0",
            "header": {
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": getattr(sender_id, "open_id", None),
                        "user_id": getattr(sender_id, "user_id", None),
                        "union_id": getattr(sender_id, "union_id", None),
                    },
                    "sender_type": getattr(sender, "sender_type", None),
                    "tenant_key": getattr(sender, "tenant_key", None),
                },
                "message": {
                    "message_id": getattr(message, "message_id", None),
                    "root_id": getattr(message, "root_id", None),
                    "parent_id": getattr(message, "parent_id", None),
                    "create_time": getattr(message, "create_time", None),
                    "update_time": getattr(message, "update_time", None),
                    "chat_id": getattr(message, "chat_id", None),
                    "thread_id": getattr(message, "thread_id", None),
                    "chat_type": getattr(message, "chat_type", None),
                    "message_type": getattr(message, "message_type", None),
                    "content": getattr(message, "content", None),
                    "mentions": [self._mention_to_dict(item) for item in getattr(message, "mentions", []) or []],
                    "user_agent": getattr(message, "user_agent", None),
                },
            },
        }


def _claim_card_action(self, action_token: str, event_payload: dict[str, Any]) -> bool:
    marker = self.processed_card_action_dir / f"{action_token}.json"
    if marker.exists():
        return False
    write_json(
        marker,
        {
            "action_token": action_token,
            "timestamp": utc_now_iso(),
            "payload": event_payload,
        },
    )
    return True

def _to_card_action_payload(self, payload: P2CardActionTrigger) -> dict[str, Any]:
    event = payload.event
    action = event.action if event else None
    operator = event.operator if event else None
    context = event.context if event else None
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {
                "tenant_key": getattr(operator, "tenant_key", None),
                "user_id": getattr(operator, "user_id", None),
                "open_id": getattr(operator, "open_id", None),
                "union_id": getattr(operator, "union_id", None),
            },
            "token": getattr(event, "token", None),
            "action": {
                "value": getattr(action, "value", None),
                "tag": getattr(action, "tag", None),
                "option": getattr(action, "option", None),
                "timezone": getattr(action, "timezone", None),
                "name": getattr(action, "name", None),
                "form_value": getattr(action, "form_value", None),
                "input_value": getattr(action, "input_value", None),
                "options": getattr(action, "options", None),
                "checked": getattr(action, "checked", None),
            },
            "host": getattr(event, "host", None),
            "delivery_type": getattr(event, "delivery_type", None),
            "context": {
                "url": getattr(context, "url", None),
                "preview_token": getattr(context, "preview_token", None),
                "open_message_id": getattr(context, "open_message_id", None),
                "open_chat_id": getattr(context, "open_chat_id", None),
            },
        },
    }

def _extract_card_action_token(self, payload: dict[str, Any]) -> str:
    event = payload.get("event", {}) if isinstance(payload, dict) else {}
    token = str(event.get("token", "") or "").strip()
    if token:
        return token
    context = event.get("context", {}) if isinstance(event, dict) else {}
    action = event.get("action", {}) if isinstance(event, dict) else {}
    value = action.get("value", {}) if isinstance(action, dict) else {}
    parts = [
        str(context.get("open_message_id", "") or "").strip(),
        str(value.get("task_id", "") or "").strip(),
        str(value.get("round", "") or "").strip(),
        str(value.get("action", "") or "").strip(),
    ]
    return "_".join(part for part in parts if part)

    def _extract_sender_open_id(self, payload: dict[str, Any]) -> str:
        event = payload.get("event", {}) if isinstance(payload, dict) else {}
        sender = event.get("sender", {}) if isinstance(event, dict) else {}
        sender_id = sender.get("sender_id", {}) if isinstance(sender, dict) else {}
        for key in ("open_id", "user_id", "union_id"):
            value = sender_id.get(key)
            if value:
                return str(value)
        return ""

    def _extract_message_text(self, payload: dict[str, Any]) -> str:
        event = payload.get("event", {}) if isinstance(payload, dict) else {}
        message = event.get("message", {}) if isinstance(event, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except Exception:
                return str(content).strip()
            if isinstance(parsed, dict):
                return str(parsed.get("text", "") or "").strip()
        if isinstance(content, dict):
            return str(content.get("text", "") or "").strip()
        return str(content or "").strip()

    def _extract_message_id(self, payload: dict[str, Any]) -> str:
        event = payload.get("event", {}) if isinstance(payload, dict) else {}
        message = event.get("message", {}) if isinstance(event, dict) else {}
        return str(message.get("message_id", "") or "").strip()

    def _extract_task_id(self, text: str) -> str:
        match = _TASK_ID_PATTERN.search(str(text or ""))
        return str(match.group(1)).strip() if match else ""

    def _mention_to_dict(self, mention: Any) -> dict[str, Any]:
        if mention is None:
            return {}
        return {
            "key": getattr(mention, "key", None),
            "id": {
                "open_id": getattr(getattr(mention, "id", None), "open_id", None),
                "user_id": getattr(getattr(mention, "id", None), "user_id", None),
                "union_id": getattr(getattr(mention, "id", None), "union_id", None),
            },
            "name": getattr(mention, "name", None),
            "tenant_key": getattr(mention, "tenant_key", None),
        }
