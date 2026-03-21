from __future__ import annotations

from pathlib import Path

from .models import ListingPackage, Manifest, ManifestEvent, utc_now_iso
from .storage import ensure_task_dirs, read_json, write_json


class InvalidTransitionError(ValueError):
    pass


class TaskManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def init_task(self, task_id: str, product_id: str, market: str, shop_id: str, category: str) -> Path:
        task_dir = self.base_dir / task_id
        ensure_task_dirs(task_dir)

        manifest = Manifest(
            task_id=task_id,
            product_id=product_id,
            status="product_created",
            market=market,
            shop_id=shop_id,
            category=category,
            events=[ManifestEvent(timestamp=utc_now_iso(), event="task_created", detail={})],
        )
        product_brief = {
            "task_id": task_id,
            "product_id": product_id,
            "product_name": "",
            "shop_id": shop_id,
            "target_market": market,
            "category_hint": category,
            "selling_points": [],
            "target_user": "",
            "price_range": "",
            "style": "",
            "image_rules": {"main_image_count": 1, "sub_image_count": 8},
            "attribute_template": {},
            "sku_template": [],
            "competitor_links": [],
            "compliance_rules": [],
            "notes": "",
        }
        image_assets = {
            "product_id": product_id,
            "main_images": [],
            "sub_images": [],
            "detail_images": [],
            "a_plus_images": [],
            "generation_meta": {"tool": "", "version": "", "created_at": ""},
        }
        copy_assets = {
            "product_id": product_id,
            "title": "",
            "bullet_points": [],
            "description_blocks": [],
            "a_plus_copy": [],
        }
        listing = ListingPackage(
            product_id=product_id,
            task_id=task_id,
            target_market=market,
            shop_id=shop_id,
            category=category,
        )

        write_json(task_dir / "manifest.json", manifest)
        write_json(task_dir / "product_brief.json", product_brief)
        write_json(task_dir / "image_assets.json", image_assets)
        write_json(task_dir / "copy_assets.json", copy_assets)
        write_json(task_dir / "listing_package.json", listing)
        return task_dir

    def bootstrap_from_product_brief(self, task_dir: Path) -> Path:
        ensure_task_dirs(task_dir)
        product_brief = read_json(task_dir / "product_brief.json")

        manifest = Manifest(
            task_id=product_brief["task_id"],
            product_id=product_brief["product_id"],
            status="product_created",
            market=product_brief["target_market"],
            shop_id=product_brief["shop_id"],
            category=product_brief.get("category_hint", ""),
            events=[ManifestEvent(timestamp=utc_now_iso(), event="task_created_from_feishu", detail={})],
        )
        image_assets = {
            "product_id": product_brief["product_id"],
            "main_images": [],
            "sub_images": [],
            "detail_images": [],
            "a_plus_images": [],
            "generation_meta": {"tool": "", "version": "", "created_at": ""},
        }
        copy_assets = {
            "product_id": product_brief["product_id"],
            "title": "",
            "bullet_points": [],
            "description_blocks": [],
            "a_plus_copy": [],
        }
        listing = ListingPackage(
            product_id=product_brief["product_id"],
            task_id=product_brief["task_id"],
            target_market=product_brief["target_market"],
            shop_id=product_brief["shop_id"],
            category=product_brief.get("category_hint", ""),
        )

        write_json(task_dir / "manifest.json", manifest)
        write_json(task_dir / "image_assets.json", image_assets)
        write_json(task_dir / "copy_assets.json", copy_assets)
        write_json(task_dir / "listing_package.json", listing)
        return task_dir

    def load_manifest(self, task_dir: Path) -> dict:
        return read_json(task_dir / "manifest.json")

    def load_listing_package(self, task_dir: Path) -> dict:
        return read_json(task_dir / "listing_package.json")

    def advance_status(self, task_dir: Path, new_status: str, note: str | None = None) -> None:
        from .models import ALLOWED_TRANSITIONS, WORKFLOW_STATES

        if new_status not in WORKFLOW_STATES:
            raise InvalidTransitionError(f"Unknown status: {new_status}")

        manifest = self.load_manifest(task_dir)
        current = manifest["status"]
        if new_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"Invalid transition: {current} -> {new_status}")

        manifest["status"] = new_status
        manifest["updated_at"] = utc_now_iso()
        if new_status == "image_review_passed":
            manifest["reviews"]["image_review"] = "passed"
        if new_status == "copy_review_passed":
            manifest["reviews"]["copy_review"] = "passed"
        if new_status == "manual_check_pending":
            manifest["reviews"].setdefault("review_logs", []).append({"timestamp": utc_now_iso(), "note": note or "manual check pending"})
        manifest["events"].append(
            {
                "timestamp": utc_now_iso(),
                "event": "status_changed",
                "detail": {"from": current, "to": new_status, "note": note or ""},
            }
        )
        write_json(task_dir / "manifest.json", manifest)

        package = self.load_listing_package(task_dir)
        package["workflow"]["status"] = new_status
        if new_status == "image_review_passed":
            package["review_status"]["image_review"] = "passed"
        if new_status == "copy_review_passed":
            package["review_status"]["copy_review"] = "passed"
        write_json(task_dir / "listing_package.json", package)
