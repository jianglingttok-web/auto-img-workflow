from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.core.enum import LogLevel
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws.client import Client

from ..models import utc_now_iso
from ..storage import write_json
from .feishu_message_review import FeishuMessageReviewProcessor

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
            .build()
        )

    def _handle_im_message_receive(self, payload: P2ImMessageReceiveV1) -> None:
        event_payload = self._to_event_payload(payload)
        snapshot: dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "source": "feishu_long_connection",
            "payload": event_payload,
        }
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
            _LOGGER.exception("failed to process Feishu message review event")
        self._write_event_snapshot(snapshot)

    def _write_event_snapshot(self, payload: dict[str, Any]) -> Path:
        event_dir = self.tasks_root / "_callback_events"
        event_dir.mkdir(parents=True, exist_ok=True)
        event_index = len(list(event_dir.glob("event_*.json"))) + 1
        event_path = event_dir / f"event_{event_index:03d}.json"
        write_json(event_path, payload)
        return event_path

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
