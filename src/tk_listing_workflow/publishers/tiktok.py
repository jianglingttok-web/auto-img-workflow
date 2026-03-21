from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PublishResult:
    success: bool
    status: str
    listing_url: str | None
    evidence_files: list[str]
    raw_response: dict[str, Any]


class TikTokPublisher:
    """Placeholder publisher for TikTok draft creation."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def publish_draft(self, package: dict[str, Any]) -> PublishResult:
        return PublishResult(
            success=False,
            status="not_implemented",
            listing_url=None,
            evidence_files=[],
            raw_response={"message": "TikTok publisher is not implemented yet."},
        )
