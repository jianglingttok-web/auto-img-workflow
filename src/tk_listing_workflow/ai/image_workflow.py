from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..storage import read_json, write_json


VALID_PROMPT_MODES = {"visual_only", "benefit_copy", "strict_packaging_only"}
VALID_TASK_MODES = {"main_only", "sub_only", "full_set"}


def infer_task_mode(main_count: int, sub_count: int) -> str:
    if main_count < 0 or sub_count < 0:
        raise ValueError("image counts must be non-negative")
    if main_count == 0 and sub_count == 0:
        raise ValueError("at least one image must be requested")
    if main_count > 0 and sub_count == 0:
        return "main_only"
    if main_count == 0 and sub_count > 0:
        return "sub_only"
    return "full_set"


@dataclass(slots=True)
class ReferenceImages:
    product_white_background: list[str] = field(default_factory=list)
    usage_images: list[str] = field(default_factory=list)
    style_reference_images: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StandardizedImageTask:
    task_id: str
    product_id: str
    product_name: str
    shop_id: str
    target_market: str
    task_mode: str
    main_image_count: int
    sub_image_count: int
    task_type: str = ""
    category_hint: str = ""
    use_case: str = ""
    variation_scope: str = ""
    operator_group: str = ""
    submitter: str = ""
    style: list[str] = field(default_factory=list)
    selling_points: list[str] = field(default_factory=list)
    compliance_rules: list[str] = field(default_factory=list)
    marketing_phrases: list[str] = field(default_factory=list)
    numeric_claims: list[str] = field(default_factory=list)
    competitor_links: list[str] = field(default_factory=list)
    notes: str = ""
    reference_images: ReferenceImages = field(default_factory=ReferenceImages)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageWorkflowBuilder:
    def build_standardized_task(self, task_dir: Path) -> dict[str, Any]:
        product_brief = read_json(task_dir / "product_brief.json")
        intake_payload = self._read_optional(task_dir / "intake" / "feishu_record.json")
        task = self._from_task_files(task_dir, product_brief, intake_payload)
        payload = task.to_dict()
        write_json(task_dir / "image_task.json", payload)
        return payload

    def build_oc_input(self, task_dir: Path, round_number: int = 1, rework_reason: str = "", rework_scope: str = "") -> dict[str, Any]:
        task = self._load_standardized_task(task_dir)
        payload = {
            "task_id": task["task_id"],
            "task_mode": task["task_mode"],
            "task_type": task.get("task_type", ""),
            "product_id": task["product_id"],
            "product_name": task["product_name"],
            "site": task["target_market"],
            "shop_id": task["shop_id"],
            "category_hint": task.get("category_hint", ""),
            "use_case": task.get("use_case", ""),
            "variation_scope": task.get("variation_scope", ""),
            "operator_group": task.get("operator_group", ""),
            "submitter": task.get("submitter", ""),
            "selling_points": task.get("selling_points", []),
            "style_requirements": task.get("style", []),
            "compliance_requirements": task.get("compliance_rules", []),
            "marketing_phrases": task.get("marketing_phrases", []),
            "numeric_claims": task.get("numeric_claims", []),
            "competitor_links": task.get("competitor_links", []),
            "notes": task.get("notes", ""),
            "reference_images": task.get("reference_images", {}),
            "requested_output": {
                "main_count": task["main_image_count"],
                "sub_count": task["sub_image_count"],
            },
            "rework": {
                "round": round_number,
                "reason": rework_reason,
                "scope": rework_scope,
            },
        }
        write_json(task_dir / "prompts" / f"round_{round_number:02d}_oc_input.json", payload)
        return payload

    def _load_standardized_task(self, task_dir: Path) -> dict[str, Any]:
        image_task_path = task_dir / "image_task.json"
        if not image_task_path.exists():
            return self.build_standardized_task(task_dir)
        return read_json(image_task_path)

    def _from_task_files(self, task_dir: Path, product_brief: dict[str, Any], intake_payload: dict[str, Any]) -> StandardizedImageTask:
        image_rules = product_brief.get("image_rules", {})
        main_count = int(image_rules.get("main_image_count", 0) or 0)
        sub_count = int(image_rules.get("sub_image_count", 0) or 0)
        task_mode = infer_task_mode(main_count, sub_count)

        style = self._ensure_list(product_brief.get("style", []))
        selling_points = self._ensure_list(product_brief.get("selling_points", []))
        compliance_rules = self._ensure_list(product_brief.get("compliance_rules", []))
        competitor_links = self._ensure_list(product_brief.get("competitor_links", []))
        marketing_phrases = self._ensure_list(intake_payload.get("营销表达", []))
        numeric_claims = self._ensure_list(intake_payload.get("数字信息", []))
        task_type = str(product_brief.get("task_type", "") or "").strip()

        brief_refs = product_brief.get("reference_images", {}) if isinstance(product_brief, dict) else {}
        unified_reference_images = self._collect_attachment_paths(intake_payload.get("参考图"), task_dir)
        legacy_fission_refs = self._collect_attachment_paths(intake_payload.get("裂变参考图"), task_dir)
        legacy_style_refs = self._collect_attachment_paths(intake_payload.get("已有主图/风格参考图"), task_dir)

        reference_images = ReferenceImages(
            product_white_background=self._collect_attachment_paths(intake_payload.get("产品白底图"), task_dir),
            usage_images=self._collect_attachment_paths(intake_payload.get("使用图"), task_dir),
            style_reference_images=unified_reference_images or legacy_style_refs,
        )

        if not reference_images.style_reference_images and legacy_fission_refs:
            reference_images.style_reference_images = legacy_fission_refs

        if not reference_images.product_white_background:
            reference_images.product_white_background = self._ensure_list(brief_refs.get("product_white_background", []))
        if not reference_images.usage_images:
            reference_images.usage_images = self._ensure_list(brief_refs.get("usage_images", []))
        if not reference_images.style_reference_images:
            reference_images.style_reference_images = self._ensure_list(brief_refs.get("style_reference_images", []))
        if not reference_images.style_reference_images:
            reference_images.style_reference_images = self._ensure_list(brief_refs.get("fission_reference", []))

        if not reference_images.product_white_background:
            fallback_white = self._collect_local_assets(task_dir / "intake", prefixes=("product_white", "white", "main_ref"))
            reference_images.product_white_background.extend(fallback_white)
        if not reference_images.usage_images:
            fallback_usage = self._collect_local_assets(task_dir / "intake", prefixes=("usage", "scene", "wear"))
            reference_images.usage_images.extend(fallback_usage)
        if not reference_images.style_reference_images:
            fallback_style = self._collect_local_assets(
                task_dir / "intake",
                prefixes=("reference", "style_ref", "existing_main", "main_style", "fission_ref"),
            )
            reference_images.style_reference_images.extend(fallback_style)

        if task_mode == "sub_only" and not reference_images.style_reference_images:
            raise ValueError("sub_only mode requires existing main / style reference image or matching local style reference image")

        return StandardizedImageTask(
            task_id=product_brief["task_id"],
            product_id=product_brief["product_id"],
            product_name=product_brief.get("product_name", ""),
            shop_id=product_brief.get("shop_id", ""),
            target_market=product_brief.get("target_market", ""),
            task_mode=task_mode,
            main_image_count=main_count,
            sub_image_count=sub_count,
            category_hint=product_brief.get("category_hint", ""),
            task_type=task_type,
            use_case=product_brief.get("use_case", ""),
            variation_scope=product_brief.get("variation_scope", ""),
            operator_group=str(intake_payload.get("运营组", "") or "").strip(),
            submitter=str(intake_payload.get("提交人", "") or "").strip(),
            style=style,
            selling_points=selling_points,
            compliance_rules=compliance_rules,
            marketing_phrases=marketing_phrases,
            numeric_claims=numeric_claims,
            competitor_links=competitor_links,
            notes=product_brief.get("notes", ""),
            reference_images=reference_images,
        )
    def _read_optional(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return read_json(path)

    def _ensure_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]

    def _collect_attachment_paths(self, value: Any, task_dir: Path) -> list[str]:
        paths: list[str] = []
        if value is None:
            return paths
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                candidate = item.get("path") or item.get("file_path") or item.get("url") or item.get("name")
            else:
                candidate = str(item).strip()
            if not candidate:
                continue
            candidate_path = Path(candidate)
            if candidate_path.is_absolute():
                paths.append(str(candidate_path))
            else:
                intake_candidate = task_dir / "intake" / candidate
                if intake_candidate.exists():
                    paths.append(str(intake_candidate))
                else:
                    paths.append(candidate)
        return paths

    def _collect_local_assets(self, intake_dir: Path, prefixes: tuple[str, ...]) -> list[str]:
        if not intake_dir.exists():
            return []
        matches = []
        for path in sorted(intake_dir.iterdir()):
            if not path.is_file():
                continue
            lowered = path.stem.lower()
            if any(lowered.startswith(prefix) for prefix in prefixes):
                matches.append(str(path))
        return matches


class SeedreamJobPlanner:
    def build_jobs(self, task_dir: Path, oc_output_path: Path, round_number: int = 1) -> dict[str, Any]:
        task = read_json(task_dir / "image_task.json")
        oc_output = read_json(oc_output_path)
        prompt_mode = oc_output.get("prompt_mode", "")
        if prompt_mode not in VALID_PROMPT_MODES:
            raise ValueError(f"invalid prompt_mode: {prompt_mode}")

        task_mode = task["task_mode"]
        jobs: list[dict[str, Any]] = []
        refs = task.get("reference_images", {})

        main_prompt = str(oc_output.get("main_image_prompt", "")).strip()
        if task_mode in {"main_only", "full_set"}:
            if not main_prompt:
                raise ValueError("OC output missing main_image_prompt")
            for index in range(1, task["main_image_count"] + 1):
                jobs.append({
                    "task_id": task["task_id"],
                    "round": round_number,
                    "image_type": "main",
                    "slot": f"main_{index:02d}",
                    "task_mode": task_mode,
                    "prompt_mode": prompt_mode,
                    "prompt": main_prompt,
                    "reference_images": {
                        "product_white_background": refs.get("product_white_background", []),
                        "usage_images": refs.get("usage_images", []),
                        "style_reference_images": refs.get("style_reference_images", []),
                    },
                    "output_spec": {"ratio": "1:1", "count": 1},
                })

        sub_prompts = oc_output.get("sub_image_prompts", [])
        if task_mode in {"sub_only", "full_set"}:
            expected = task["sub_image_count"]
            if len(sub_prompts) != expected:
                raise ValueError(f"sub_image_prompts count mismatch: expected {expected}, got {len(sub_prompts)}")
            for item in sub_prompts:
                slot = str(item.get("slot", "")).strip()
                prompt = str(item.get("prompt", "")).strip()
                if not slot or not prompt:
                    raise ValueError("each sub_image_prompt must include slot and prompt")
                jobs.append({
                    "task_id": task["task_id"],
                    "round": round_number,
                    "image_type": "sub",
                    "slot": slot,
                    "task_mode": task_mode,
                    "prompt_mode": prompt_mode,
                    "prompt": prompt,
                    "reference_images": {
                        "product_white_background": refs.get("product_white_background", []),
                        "usage_images": refs.get("usage_images", []),
                        "style_reference_images": refs.get("style_reference_images", []),
                    },
                    "output_spec": {"ratio": "1:1", "count": 1},
                })

        payload = {
            "task_id": task["task_id"],
            "round": round_number,
            "task_mode": task_mode,
            "prompt_mode": prompt_mode,
            "jobs": jobs,
        }
        write_json(task_dir / "prompts" / f"round_{round_number:02d}_seedream_jobs.json", payload)
        return payload



