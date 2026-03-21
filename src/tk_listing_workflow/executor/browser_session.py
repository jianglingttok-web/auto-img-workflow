from __future__ import annotations

from pathlib import Path


class BrowserSession:
    """Placeholder browser session wrapper."""

    def __init__(self, screenshots_dir: Path) -> None:
        self.screenshots_dir = screenshots_dir

    def connect(self) -> None:
        raise NotImplementedError("Browser connection is not implemented yet.")

    def save_screenshot(self, step_name: str) -> str:
        target = self.screenshots_dir / f"{step_name}.png"
        return str(target)
