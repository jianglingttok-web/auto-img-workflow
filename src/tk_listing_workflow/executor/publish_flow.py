from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class PublishFlowResult:
    status: str
    product_url: str = ""
    draft_id: str = ""
    error_code: str = ""
    error_message: str = ""
    screenshots: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublishFlow:
    """Placeholder publish flow for TikTok Seller Center."""

    def run(self, listing_package: dict[str, Any]) -> PublishFlowResult:
        return PublishFlowResult(
            status="failed",
            error_code="FORM_SUBMIT_FAILED",
            error_message="Publish flow is not implemented yet.",
            screenshots=[],
        )
