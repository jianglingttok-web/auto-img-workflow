from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .feishu_message_review import FeishuMessageReviewProcessor
from ..storage import write_json


class FeishuCallbackServer:
    def __init__(
        self,
        tasks_root: Path,
        host: str = "127.0.0.1",
        port: int = 8000,
        callback_path: str = "/feishu/callback",
        health_path: str = "/healthz",
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.host = host
        self.port = port
        self.callback_path = self._normalize_path(callback_path)
        self.health_path = self._normalize_path(health_path)

    def serve(self) -> None:
        processor = FeishuMessageReviewProcessor(self.tasks_root)
        tasks_root = self.tasks_root
        callback_path = self.callback_path
        health_path = self.health_path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == health_path:
                    self._send_json(200, {"ok": True, "status": "healthy", "path": health_path})
                    return
                if self.path == callback_path:
                    self._send_json(200, {"ok": True, "status": "ready", "path": callback_path})
                    return
                self._send_json(404, {"ok": False, "error": "not found", "path": self.path})

            def do_POST(self) -> None:  # noqa: N802
                if self.path != callback_path:
                    self._send_json(404, {"ok": False, "error": "not found", "path": self.path})
                    return

                content_length = int(self.headers.get("Content-Length", "0") or 0)
                raw_body = self.rfile.read(content_length)
                try:
                    payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "invalid json"})
                    return

                event_dir = tasks_root / "_callback_events"
                event_dir.mkdir(parents=True, exist_ok=True)
                event_path = event_dir / f"event_{len(list(event_dir.glob('*.json'))) + 1:03d}.json"
                write_json(event_path, payload)

                if "challenge" in payload:
                    self._send_json(200, {"challenge": payload.get("challenge", "")})
                    return

                try:
                    result = processor.process_event(payload)
                except Exception as exc:
                    self._send_json(200, {"ok": False, "error": str(exc), "event_path": str(event_path)})
                    return

                self._send_json(200, {"ok": True, "result": result, "event_path": str(event_path)})

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()

    def _normalize_path(self, path: str) -> str:
        value = str(path or "").strip() or "/"
        if not value.startswith("/"):
            value = f"/{value}"
        return value
