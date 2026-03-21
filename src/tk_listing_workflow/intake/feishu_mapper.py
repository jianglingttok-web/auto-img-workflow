from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage import read_json, write_json


@dataclass(slots=True)
class FeishuImportResult:
    task_id: str
    task_dir: str
    product_brief_path: str
    feishu_record_path: str


class FeishuImageTaskMapper:
    """Map Chinese Feishu image-task fields into internal product_brief.json."""

    def parse_record(self, record: dict[str, Any]) -> dict[str, Any]:
        task_id = self._required_text(record, "任务ID")
        product_name = self._required_text(record, "产品名称")
        shop_id = self._required_text(record, "店铺")
        target_market = self._required_text(record, "站点")

        product_id = self._optional_text(record, "产品ID") or task_id
        category_hint = self._optional_text(record, "类目建议")
        selling_points = self._split_lines(record.get("产品卖点", ""))
        target_user = self._optional_text(record, "目标人群")
        style = self._split_lines(record.get("风格要求", ""))
        competitor_links = self._split_lines(record.get("参考链接", ""))
        notes = self._optional_text(record, "补充说明")
        compliance_rules = self._split_lines(record.get("合规要求", ""))

        main_image_count = self._coerce_non_negative_int(record.get("主图数量", 1), default=1)
        sub_image_count = self._coerce_non_negative_int(record.get("副图数量", 8), default=8)
        if main_image_count == 0 and sub_image_count == 0:
            raise ValueError("主图数量 和 副图数量 不能同时为 0")

        return {
            "task_id": task_id,
            "product_id": product_id,
            "product_name": product_name,
            "shop_id": shop_id,
            "target_market": target_market,
            "category_hint": category_hint,
            "selling_points": selling_points,
            "target_user": target_user,
            "price_range": "",
            "style": style,
            "image_rules": {
                "main_image_count": main_image_count,
                "sub_image_count": sub_image_count,
            },
            "attribute_template": {},
            "sku_template": [],
            "competitor_links": competitor_links,
            "compliance_rules": compliance_rules,
            "notes": notes,
        }

    def import_record(self, record_path: Path, tasks_root: Path) -> FeishuImportResult:
        record = read_json(record_path)
        product_brief = self.parse_record(record)

        task_id = product_brief["task_id"]
        task_dir = tasks_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        write_json(task_dir / "product_brief.json", product_brief)
        write_json(task_dir / "intake" / "feishu_record.json", record)

        return FeishuImportResult(
            task_id=task_id,
            task_dir=str(task_dir),
            product_brief_path=str(task_dir / "product_brief.json"),
            feishu_record_path=str(task_dir / "intake" / "feishu_record.json"),
        )

    def _required_text(self, record: dict[str, Any], key: str) -> str:
        value = self._optional_text(record, key)
        if not value:
            raise ValueError(f"missing required field: {key}")
        return value

    def _optional_text(self, record: dict[str, Any], key: str) -> str:
        value = record.get(key, "")
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    def _split_lines(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).replace("\r\n", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]

    def _coerce_non_negative_int(self, value: Any, default: int) -> int:
        if value in (None, ""):
            return default
        number = int(value)
        if number < 0:
            raise ValueError(f"count must be non-negative, got {value}")
        return number
