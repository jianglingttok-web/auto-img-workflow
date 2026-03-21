from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


WORKFLOW_STATES = [
    "product_created",
    "image_generation_pending",
    "image_review_pending",
    "image_review_passed",
    "copy_generation_pending",
    "copy_review_pending",
    "copy_review_passed",
    "listing_ready",
    "publish_pending",
    "publishing",
    "publish_success",
    "publish_failed",
    "manual_check_pending",
    "completed",
]

ALLOWED_TRANSITIONS = {
    "product_created": {"image_generation_pending", "publish_failed"},
    "image_generation_pending": {"image_review_pending", "publish_failed"},
    "image_review_pending": {"image_review_passed", "manual_check_pending", "publish_failed"},
    "image_review_passed": {"copy_generation_pending", "publish_failed"},
    "copy_generation_pending": {"copy_review_pending", "publish_failed"},
    "copy_review_pending": {"copy_review_passed", "manual_check_pending", "publish_failed"},
    "copy_review_passed": {"listing_ready", "publish_failed"},
    "listing_ready": {"publish_pending", "publish_failed"},
    "publish_pending": {"publishing", "publish_failed"},
    "publishing": {"publish_success", "publish_failed", "manual_check_pending"},
    "publish_success": {"completed"},
    "publish_failed": {"manual_check_pending"},
    "manual_check_pending": {"completed", "publish_pending", "publish_failed"},
    "completed": set(),
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ManifestEvent:
    timestamp: str
    event: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Manifest:
    task_id: str
    product_id: str
    status: str
    market: str
    shop_id: str
    category: str
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    executor: dict[str, Any] = field(
        default_factory=lambda: {"engine": "openclaw", "run_id": None, "version": None}
    )
    reviews: dict[str, Any] = field(
        default_factory=lambda: {
            "image_review": "pending",
            "copy_review": "pending",
            "review_logs": [],
        }
    )
    outputs: dict[str, Any] = field(
        default_factory=lambda: {
            "product_brief": {},
            "image_assets": {},
            "copy_assets": {},
            "listing_package": {},
            "publish": {},
        }
    )
    errors: list[dict[str, Any]] = field(default_factory=list)
    events: list[ManifestEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["events"] = [asdict(event) for event in self.events]
        return data


@dataclass(slots=True)
class ListingPackage:
    product_id: str
    task_id: str
    target_market: str
    shop_id: str
    category: str
    title: str = ""
    main_images: list[str] = field(default_factory=list)
    sub_images: list[str] = field(default_factory=list)
    detail_images: list[str] = field(default_factory=list)
    a_plus_images: list[str] = field(default_factory=list)
    description_blocks: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    skus: list[dict[str, Any]] = field(default_factory=list)
    price_strategy: dict[str, Any] = field(default_factory=dict)
    publish_mode: str = "draft"
    review_status: dict[str, Any] = field(
        default_factory=lambda: {
            "image_review": "pending",
            "copy_review": "pending",
        }
    )
    workflow: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "product_created",
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
