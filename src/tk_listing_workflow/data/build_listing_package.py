from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..storage import read_json, write_json


class BuildListingPackageError(ValueError):
    pass


class ListingPackageBuilder:
    def build(self, task_dir: Path) -> dict[str, Any]:
        manifest = read_json(task_dir / "manifest.json")
        product_brief = read_json(task_dir / "product_brief.json")
        image_assets = read_json(task_dir / "image_assets.json")
        copy_assets = read_json(task_dir / "copy_assets.json")
        package = read_json(task_dir / "listing_package.json")

        package["title"] = copy_assets.get("title", "")
        package["main_images"] = image_assets.get("main_images", [])
        package["sub_images"] = image_assets.get("sub_images", [])
        package["detail_images"] = image_assets.get("detail_images", [])
        package["a_plus_images"] = image_assets.get("a_plus_images", [])
        package["description_blocks"] = self._build_description_blocks(product_brief, copy_assets)
        package["attributes"] = product_brief.get("attribute_template", {})
        package["skus"] = self._build_skus(product_brief)
        package["price_strategy"] = self._build_price_strategy(product_brief)
        package["publish_mode"] = package.get("publish_mode") or "draft"
        package["category"] = package.get("category") or product_brief.get("category_hint", "")
        package["target_market"] = product_brief.get("target_market", package.get("target_market", ""))
        package["shop_id"] = product_brief.get("shop_id", package.get("shop_id", ""))
        package["review_status"] = {
            "image_review": manifest.get("reviews", {}).get("image_review", package.get("review_status", {}).get("image_review", "pending")),
            "copy_review": manifest.get("reviews", {}).get("copy_review", package.get("review_status", {}).get("copy_review", "pending")),
        }

        write_json(task_dir / "listing_package.json", package)
        self._update_manifest(task_dir)
        return package

    def _build_description_blocks(self, product_brief: dict[str, Any], copy_assets: dict[str, Any]) -> list[dict[str, Any]]:
        blocks = list(copy_assets.get("description_blocks", []))
        if blocks:
            return blocks

        selling_points = product_brief.get("selling_points", [])
        if selling_points:
            return [{"section": "selling_points", "content": " | ".join(selling_points)}]
        return []

    def _build_skus(self, product_brief: dict[str, Any]) -> list[dict[str, Any]]:
        sku_template = product_brief.get("sku_template", [])
        if sku_template:
            result: list[dict[str, Any]] = []
            for index, sku in enumerate(sku_template, start=1):
                result.append(
                    {
                        "sku_id": sku.get("sku_id") or f"{product_brief['product_id']}-SKU{index:03d}",
                        "variant": sku.get("variant", {}),
                        "price": self._coerce_price(sku.get("price"), product_brief.get("price_range", "")),
                        "inventory": int(sku.get("inventory", 0)),
                    }
                )
            return result

        return [
            {
                "sku_id": f"{product_brief['product_id']}-SKU001",
                "variant": {},
                "price": self._coerce_price(None, product_brief.get("price_range", "")),
                "inventory": 0,
            }
        ]

    def _build_price_strategy(self, product_brief: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "product_brief.price_range",
            "input_price_range": product_brief.get("price_range", ""),
            "suggested_price": self._coerce_price(None, product_brief.get("price_range", "")),
        }

    def _coerce_price(self, explicit_price: Any, price_range: str) -> float:
        if explicit_price is not None and explicit_price != "":
            return float(explicit_price)

        matches = re.findall(r"\d+(?:\.\d+)?", price_range or "")
        if not matches:
            return 0.0
        return float(matches[0])

    def _update_manifest(self, task_dir: Path) -> None:
        manifest = read_json(task_dir / "manifest.json")
        manifest["outputs"]["listing_package"] = {
            "path": str(task_dir / "listing_package.json"),
            "built": True,
        }
        write_json(task_dir / "manifest.json", manifest)
