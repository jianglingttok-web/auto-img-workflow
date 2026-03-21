from __future__ import annotations

from pathlib import Path

from .storage import read_json


def run_preflight(task_dir: Path) -> list[str]:
    package = read_json(task_dir / "listing_package.json")
    issues: list[str] = []

    workflow_status = package["workflow"].get("status")
    if workflow_status not in {"listing_ready", "publish_pending", "publishing", "publish_success", "completed"}:
        issues.append(f"workflow.status must be listing_ready or later, got {workflow_status}")

    if not package.get("title"):
        issues.append("title is required")

    if len(package.get("main_images", [])) < 1:
        issues.append("at least 1 main image is required")

    if len(package.get("sub_images", [])) < 1:
        issues.append("at least 1 sub image is required")

    sku_list = package.get("skus", [])
    if len(sku_list) < 1:
        issues.append("skus requires at least 1 sku")

    for index, sku in enumerate(sku_list):
        if "price" not in sku:
            issues.append(f"skus[{index}].price is required")
        elif sku["price"] <= 0:
            issues.append(f"skus[{index}].price must be positive")

        if "inventory" not in sku:
            issues.append(f"skus[{index}].inventory is required")
        elif sku["inventory"] < 0:
            issues.append(f"skus[{index}].inventory must be non-negative")

    review_status = package.get("review_status", {})
    if review_status.get("image_review") != "passed":
        issues.append("review_status.image_review must be passed")
    if review_status.get("copy_review") != "passed":
        issues.append("review_status.copy_review must be passed")

    return issues
