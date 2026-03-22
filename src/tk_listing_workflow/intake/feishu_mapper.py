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
    source: str = "local_json"


class FeishuImageTaskMapper:
    """Map Chinese Feishu image-task fields into internal product_brief.json."""

    ATTACHMENT_FIELDS = ("产品白底图", "使用图", "已有主图/风格参考图")

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        for key in self.ATTACHMENT_FIELDS:
            normalized[key] = self._normalize_attachments(record.get(key))
        return normalized

    def parse_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_record(record)

        task_id = self._required_text(normalized, "任务ID")
        product_name = self._required_text(normalized, "产品名称")
        shop_id = self._required_text(normalized, "店铺")
        target_market = self._required_text(normalized, "站点")

        product_id = self._optional_text(normalized, "产品ID") or task_id
        category_hint = self._optional_text(normalized, "类目建议")
        selling_points = self._split_lines(normalized.get("产品卖点", ""))
        target_user = self._optional_text(normalized, "目标人群")
        style = self._split_lines(normalized.get("风格要求", ""))
        competitor_links = self._split_lines(normalized.get("参考链接", ""))
        notes = self._optional_text(normalized, "补充说明")
        compliance_rules = self._split_lines(normalized.get("合规要求", ""))

        main_image_count = self._coerce_non_negative_int(normalized.get("主图数量", 1), default=1)
        sub_image_count = self._coerce_non_negative_int(normalized.get("副图数量", 8), default=8)
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
        return self.import_record_data(record, tasks_root, source_payload=record, source="local_json")

    def import_record_data(
        self,
        record: dict[str, Any],
        tasks_root: Path,
        *,
        source_payload: dict[str, Any] | None = None,
        source: str = "feishu_bitable",
    ) -> FeishuImportResult:
        normalized_record = self.normalize_record(record)
        product_brief = self.parse_record(normalized_record)

        task_id = product_brief["task_id"]
        task_dir = tasks_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        write_json(task_dir / "product_brief.json", product_brief)
        write_json(task_dir / "intake" / "feishu_record.json", normalized_record)
        if source_payload is not None and source_payload is not normalized_record:
            write_json(task_dir / "intake" / "feishu_record_raw.json", source_payload)

        return FeishuImportResult(
            task_id=task_id,
            task_dir=str(task_dir),
            product_brief_path=str(task_dir / "product_brief.json"),
            feishu_record_path=str(task_dir / "intake" / "feishu_record.json"),
            source=source,
        )

    def _required_text(self, record: dict[str, Any], key: str) -> str:
        value = self._optional_text(record, key)
        if not value:
            raise ValueError(f"missing required field: {key}")
        return value

    def _optional_text(self, record: dict[str, Any], key: str) -> str:
        return self._stringify_value(record.get(key, ""))

    def _split_lines(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return [self._stringify_attachment(item) for item in value if self._stringify_attachment(item)]
        text = self._stringify_value(value).replace("\r\n", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]

    def _coerce_non_negative_int(self, value: Any, default: int) -> int:
        if value in (None, ""):
            return default
        if isinstance(value, dict):
            value = self._stringify_value(value)
        number = int(float(value))
        if number < 0:
            raise ValueError(f"count must be non-negative, got {value}")
        return number

    def _normalize_attachments(self, value: Any) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
        attachments: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                attachment: dict[str, Any] = {}
                for source_key, target_key in (
                    ("name", "name"),
                    ("path", "path"),
                    ("file_path", "file_path"),
                    ("url", "url"),
                    ("tmp_url", "tmp_url"),
                    ("file_token", "file_token"),
                    ("type", "type"),
                    ("size", "size"),
                ):
                    if item.get(source_key) not in (None, ""):
                        attachment[target_key] = item[source_key]
                if attachment:
                    attachments.append(attachment)
                continue

            text = str(item).strip()
            if text:
                attachments.append({"name": Path(text).name, "path": text})
        return attachments

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            parts = [self._stringify_value(item) for item in value]
            return ", ".join(part for part in parts if part)
        if isinstance(value, dict):
            for key in ("text", "name", "value", "link", "email", "en_name"):
                rendered = value.get(key)
                if rendered not in (None, ""):
                    return self._stringify_value(rendered)
            record_ids = value.get("record_ids")
            if isinstance(record_ids, list):
                joined = ", ".join(str(item).strip() for item in record_ids if str(item).strip())
                if joined:
                    return joined
            text_arr = value.get("text_arr")
            if isinstance(text_arr, list):
                joined = ", ".join(str(item).strip() for item in text_arr if str(item).strip())
                if joined:
                    return joined
            return ""
        return str(value).strip()

    def _stringify_attachment(self, value: dict[str, Any]) -> str:
        for key in ("path", "file_path", "url", "tmp_url", "name"):
            rendered = value.get(key)
            if rendered not in (None, ""):
                return str(rendered).strip()
        return ""
