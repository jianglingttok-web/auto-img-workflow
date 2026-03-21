from __future__ import annotations

from pathlib import Path
from typing import Any


class FeishuNotifier:
    """Placeholder notifier for review and publish callbacks."""

    def send_stage_notification(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "stage": stage,
            "message": "Feishu notifier is not implemented yet.",
            "payload": payload,
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
            "actions": ["通过", "打回"],
        }

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
