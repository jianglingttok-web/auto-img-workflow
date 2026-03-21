from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LauncherResult:
    success: bool
    debug_port: int | None
    ws_endpoint: str | None
    error_code: str = ""
    error_message: str = ""


class ZiniuLauncher:
    """Placeholder launcher for 紫鸟 API."""

    def launch(self, shop_id: str) -> LauncherResult:
        return LauncherResult(
            success=False,
            debug_port=None,
            ws_endpoint=None,
            error_code="ZINIU_API_ERROR",
            error_message=f"Launcher not implemented for shop_id={shop_id}",
        )
