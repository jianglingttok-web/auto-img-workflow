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
    """Map Feishu image-task fields into internal product_brief.json.

    Supports both the new unified fission intake schema and the previous
    broader image-task schema so existing records do not break during migration.
    """

    ATTACHMENT_FIELDS = ("参考图", "产品白底图", "裂变参考图", "使用图", "已有主图/风格参考图")

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        for key in self.ATTACHMENT_FIELDS:
            normalized[key] = self._normalize_attachments(record.get(key))
        return normalized

    def parse_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = self.normalize_record(record)
        if self._looks_like_unified_fission_schema(normalized):
            return self._parse_unified_fission_record(normalized)
        return self._parse_legacy_record(normalized)

    def _parse_unified_fission_record(self, normalized: dict[str, Any]) -> dict[str, Any]:
        task_id = self._required_text(normalized, "任务ID")
        target_market = self._required_text(normalized, "站点")
        task_type = self._normalize_task_type(self._required_text(normalized, "裂变类型"))
        product_name = self._optional_text(normalized, "产品名称") or task_id
        generate_count = self._coerce_non_negative_int(normalized.get("生成数量", 1), default=1)
        notes = self._optional_text(normalized, "补充注意事项")

        reference_paths = self._collect_attachment_paths(normalized.get("参考图"))
        white_background_paths = self._collect_attachment_paths(normalized.get("产品白底图"))

        if task_type == "same_product_fission":
            use_case = "image-to-image-fission"
            variation_scope = "保持同款产品不变，参考参考图延续构图、背景、光线与氛围，可做受控视觉变化。"
            fission_reference = reference_paths
            style_reference_images = reference_paths
            category_hint = "same-product-fission"
        else:
            use_case = "same-style-product-swap"
            variation_scope = "保持参考图的视觉风格、构图与氛围，替换为提交的新产品主体。"
            fission_reference = []
            style_reference_images = reference_paths
            category_hint = "same-style-product-swap"

        return {
            "task_id": task_id,
            "product_id": task_id,
            "product_name": product_name,
            "shop_id": self._optional_text(normalized, "店铺") or "auto-fission",
            "target_market": target_market,
            "category_hint": category_hint,
            "task_type": task_type,
            "selling_points": [],
            "target_user": "",
            "price_range": "",
            "style": [task_type],
            "variation_scope": variation_scope,
            "image_rules": {
                "main_image_count": generate_count,
                "sub_image_count": 0,
            },
            "attribute_template": {},
            "sku_template": [],
            "competitor_links": [],
            "compliance_rules": [],
            "reference_images": {
                "fission_reference": fission_reference,
                "product_white_background": white_background_paths,
                "usage_images": [],
                "style_reference_images": style_reference_images,
            },
            "notes": notes,
            "use_case": use_case,
        }

    def _parse_legacy_record(self, normalized: dict[str, Any]) -> dict[str, Any]:
        task_id = self._required_text(normalized, "任务ID")
        product_name = self._required_text(normalized, "产品名称")
        notes = self._optional_text(normalized, "补充说明") or self._optional_text(normalized, "补充注意事项")
        target_market = self._optional_text(normalized, "站点") or self._infer_target_market(normalized)

        reference_paths = self._collect_attachment_paths(normalized.get("参考图"))
        legacy_fission_refs = self._collect_attachment_paths(normalized.get("裂变参考图"))
        legacy_style_refs = self._collect_attachment_paths(normalized.get("已有主图/风格参考图"))

        return {
            "task_id": task_id,
            "product_id": task_id,
            "product_name": product_name,
            "shop_id": self._optional_text(normalized, "店铺"),
            "target_market": target_market,
            "category_hint": self._optional_text(normalized, "品类") or self._optional_text(normalized, "类目"),
            "task_type": "",
            "selling_points": self._split_lines(normalized.get("产品卖点")),
            "target_user": "",
            "price_range": "",
            "style": self._split_lines(normalized.get("风格要求")),
            "variation_scope": "",
            "image_rules": {
                "main_image_count": self._coerce_non_negative_int(normalized.get("主图数量", 1), default=1),
                "sub_image_count": self._coerce_non_negative_int(normalized.get("副图数量", 0), default=0),
            },
            "attribute_template": {},
            "sku_template": [],
            "competitor_links": self._split_lines(normalized.get("参考链接")),
            "compliance_rules": self._split_lines(normalized.get("合规要求")),
            "reference_images": {
                "fission_reference": legacy_fission_refs or reference_paths,
                "product_white_background": self._collect_attachment_paths(normalized.get("产品白底图")),
                "usage_images": self._collect_attachment_paths(normalized.get("使用图")),
                "style_reference_images": legacy_style_refs or reference_paths or legacy_fission_refs,
            },
            "notes": notes,
            "use_case": "",
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

    def _normalize_task_type(self, value: str) -> str:
        text = str(value).strip()
        if text in {"同产品裂变", "same_product_fission"}:
            return "same_product_fission"
        if text in {"同风格换品", "same_style_product_swap", "same_style_swap"}:
            return "same_style_product_swap"
        raise ValueError(f"unsupported 裂变类型: {value}")

    def _infer_target_market(self, normalized: dict[str, Any]) -> str:
        operator_group = self._optional_text(normalized, "运营组").upper()
        if "TH" in operator_group or "泰国" in operator_group:
            return "TH"
        if "ID" in operator_group or "印尼" in operator_group or "印度尼西亚" in operator_group:
            return "ID"
        if "MY" in operator_group or "马来" in operator_group:
            return "MY"
        if "PH" in operator_group or "菲律宾" in operator_group:
            return "PH"
        if "VN" in operator_group or "越南" in operator_group:
            return "VN"
        if "SG" in operator_group or "新加坡" in operator_group:
            return "SG"
        return ""

    def _looks_like_unified_fission_schema(self, normalized: dict[str, Any]) -> bool:
        return any(
            key in normalized and normalized.get(key) not in (None, "", [])
            for key in ("裂变类型", "参考图", "生成数量")
        )

    def _collect_attachment_paths(self, value: Any) -> list[str]:
        items = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
        paths: list[str] = []
        for item in items:
            if isinstance(item, dict):
                rendered = self._stringify_attachment(item)
            else:
                rendered = str(item).strip()
            if rendered:
                paths.append(rendered)
        return paths

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
