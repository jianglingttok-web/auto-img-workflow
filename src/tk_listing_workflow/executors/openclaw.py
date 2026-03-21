from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage import write_json


@dataclass(slots=True)
class OpenClawJobResult:
    run_id: str
    output_files: list[str]
    meta: dict[str, Any]


class OpenClawExecutor:
    """Deterministic local stand-in for the future OpenClaw prompt builder."""

    BENEFIT_KEYWORDS = (
        "洗发", "清洁", "去污", "除菌", "护理", "香氛", "控油", "蓬松", "修护", "保湿", "除臭", "功效", "容量", "续航",
    )
    PACKAGING_KEYWORDS = ("保留包装", "原包装", "不要新增文字", "只保留包装文字")
    JEWELRY_KEYWORDS = (
        "饰品", "项链", "耳环", "手链", "戒指", "胸针", "发夹", "配饰", "首饰", "吊坠",
    )

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def submit(self, job_type: str, payload: dict[str, Any]) -> OpenClawJobResult:
        fake_run_id = f"openclaw-{job_type}-stub"
        return OpenClawJobResult(
            run_id=fake_run_id,
            output_files=[],
            meta={"job_type": job_type, "payload_keys": sorted(payload.keys())},
        )

    def build_image_prompts(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_mode = payload["task_mode"]
        prompt_mode = self._infer_prompt_mode(payload)
        variables = self._extract_variables(payload)

        result: dict[str, Any] = {
            "task_mode": task_mode,
            "prompt_mode": prompt_mode,
            "reason": self._build_reason(prompt_mode, payload),
            "variables": variables,
        }

        if task_mode in {"main_only", "full_set"}:
            result["main_image_prompt"] = self._build_main_prompt(prompt_mode, payload, variables)

        if task_mode in {"sub_only", "full_set"}:
            sub_count = int(payload["requested_output"]["sub_count"])
            plan = self._build_sub_image_plan(prompt_mode, payload, sub_count)
            result["sub_image_plan"] = plan
            result["sub_image_prompts"] = [
                {"slot": item["slot"], "prompt": self._build_sub_prompt(prompt_mode, payload, variables, item["role"], item["slot"])}
                for item in plan
            ]

        return result

    def write_image_prompts(self, payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
        result = self.build_image_prompts(payload)
        write_json(output_path, result)
        return result

    def _infer_prompt_mode(self, payload: dict[str, Any]) -> str:
        text_blob = " ".join(
            self._flatten_text(
                payload.get("product_name", ""),
                payload.get("category_hint", ""),
                payload.get("selling_points", []),
                payload.get("style_requirements", []),
                payload.get("compliance_requirements", []),
                payload.get("notes", ""),
            )
        )
        if any(keyword in text_blob for keyword in self.PACKAGING_KEYWORDS):
            return "strict_packaging_only"
        if payload.get("marketing_phrases") or payload.get("numeric_claims"):
            return "benefit_copy"
        if any(keyword in text_blob for keyword in self.BENEFIT_KEYWORDS):
            return "benefit_copy"
        if any(keyword in text_blob for keyword in self.JEWELRY_KEYWORDS):
            return "visual_only"
        return "visual_only"

    def _extract_variables(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "style_summary": self._join(payload.get("style_requirements", []), sep="；") or "干净、统一、适合电商转化",
            "usage_scene_summary": "；".join(self._summarize_usage(payload)),
            "marketing_phrases": payload.get("marketing_phrases", []),
            "numeric_claims": payload.get("numeric_claims", []),
            "selling_points": payload.get("selling_points", []),
            "compliance_requirements": payload.get("compliance_requirements", []),
        }

    def _build_reason(self, prompt_mode: str, payload: dict[str, Any]) -> str:
        if prompt_mode == "benefit_copy":
            return "任务包含明确功效、营销表达或数字信息，适合 benefit_copy。"
        if prompt_mode == "strict_packaging_only":
            return "任务强调保留包装原文且不新增字样，适合 strict_packaging_only。"
        return "任务以产品展示、质感和场景表达为主，适合 visual_only。"

    def _build_main_prompt(self, prompt_mode: str, payload: dict[str, Any], variables: dict[str, Any]) -> str:
        product_name = payload.get("product_name", "产品")
        selling_points = self._join(payload.get("selling_points", []), sep="；")
        style_summary = variables["style_summary"]
        compliance = self._join(payload.get("compliance_requirements", []), sep="；")
        usage = variables["usage_scene_summary"] or "无使用图时围绕产品卖点构建简洁展示场景"

        base = [
            f"请基于参考图对 {product_name} 做 1:1 电商图生图。",
            "产品主体必须与白底图保持一致，不改变外形、颜色、材质、包装和品牌识别信息。",
            "允许优化背景、构图、场景、光影和道具，但必须持续突出产品主体，避免拼贴感、多宫格和低质模板感。",
            f"重点卖点：{selling_points or '突出主体和转化感'}。",
            f"风格要求：{style_summary}。",
            f"场景参考：{usage}。",
        ]

        if prompt_mode == "benefit_copy":
            marketing = self._join(payload.get("marketing_phrases", []), sep="；")
            numeric = self._join(payload.get("numeric_claims", []), sep="；")
            base.extend(
                [
                    "允许加入一句简短大字主文案，但文案必须克制、清晰、合规，且不能压过产品主体。",
                    f"可选营销表达：{marketing or '无'}。",
                    f"可选数字信息：{numeric or '无'}。",
                    "产品主体占画面 1/2 以上，不新增未经验证的功效承诺。",
                ]
            )
        elif prompt_mode == "strict_packaging_only":
            base.append("只允许保留产品包装原有文字，不允许在画面其他区域新增任何文案、logo、水印或促销元素。")
        else:
            base.append("不要在画面中新增任何文案、logo、水印或平台元素，只通过场景、质感和光影传达卖点。")

        if compliance:
            base.append(f"合规要求：{compliance}。")

        base.append("输出应适合跨境电商主图点击转化，画面真实、精致、主体清晰。")
        return "\n".join(base)

    def _build_sub_image_plan(self, prompt_mode: str, payload: dict[str, Any], sub_count: int) -> list[dict[str, str]]:
        roles_pool = self._role_pool(prompt_mode, payload)
        plan: list[dict[str, str]] = []
        for index in range(sub_count):
            role = roles_pool[index] if index < len(roles_pool) else roles_pool[-1]
            plan.append({"slot": f"sub_{index + 1:02d}", "role": role})
        return plan

    def _build_sub_prompt(self, prompt_mode: str, payload: dict[str, Any], variables: dict[str, Any], role: str, slot: str) -> str:
        product_name = payload.get("product_name", "产品")
        style_summary = variables["style_summary"]
        selling_points = payload.get("selling_points", [])
        role_hint = self._role_hint(role, selling_points)

        lines = [
            f"请基于参考图为 {product_name} 生成 1:1 电商副图，图位 {slot}。",
            f"本图职责：{role}。",
            "产品主体必须与白底图一致，不改变外形、颜色、材质、包装和品牌信息。",
            "本图需要与主图保持统一视觉语言和商业质感，但在构图、场景、道具或光影上形成差异。",
            f"风格要求：{style_summary}。",
            f"本图重点：{role_hint}。",
        ]

        if prompt_mode == "benefit_copy":
            marketing = self._join(payload.get("marketing_phrases", []), sep="；")
            numeric = self._join(payload.get("numeric_claims", []), sep="；")
            lines.append("如该图位适合加字，只允许一条简短强化信息，且需服务于当前图位职责。")
            if marketing:
                lines.append(f"营销表达参考：{marketing}。")
            if numeric:
                lines.append(f"数字信息参考：{numeric}。")
        elif prompt_mode == "strict_packaging_only":
            lines.append("不得新增任何文字，只允许保留产品包装原有文字。")
        else:
            lines.append("不要新增文字，仅通过场景、细节、构图和道具传达信息。")

        compliance = self._join(payload.get("compliance_requirements", []), sep="；")
        if compliance:
            lines.append(f"合规要求：{compliance}。")
        lines.append("避免拼贴感、多宫格、低质模板感、模糊主体和错误人体结构。")
        return "\n".join(lines)

    def _role_pool(self, prompt_mode: str, payload: dict[str, Any]) -> list[str]:
        product_text = " ".join(self._flatten_text(payload.get("product_name", ""), payload.get("category_hint", ""), payload.get("notes", "")))
        selling_points = payload.get("selling_points", [])
        if prompt_mode == "benefit_copy":
            pool = [
                "核心卖点图",
                "第二卖点图",
                "使用场景图",
                "效果体验图",
                "规格数字图",
                "补充使用场景图",
                "补充卖点图",
                "氛围收尾图",
            ]
        elif any(keyword in product_text for keyword in self.JEWELRY_KEYWORDS):
            pool = [
                "核心展示图",
                "佩戴场景图",
                "细节特写图",
                "补充佩戴图",
                "搭配展示图",
                "补充细节图",
                "氛围图",
                "收尾图",
            ]
        else:
            pool = [
                "核心卖点图",
                "第二卖点图",
                "使用场景图",
                "细节特写图",
                "补充使用场景图",
                "补充卖点图",
                "组合展示图",
                "氛围收尾图",
            ]

        if len(selling_points) >= 4:
            pool.insert(2, "第三卖点图")
        return pool

    def _role_hint(self, role: str, selling_points: list[str]) -> str:
        if "第一" in role or "核心卖点" in role or "核心展示" in role:
            return selling_points[0] if selling_points else "突出核心卖点与主体"
        if "第二" in role:
            return selling_points[1] if len(selling_points) > 1 else "补充第二层卖点"
        if "第三" in role:
            return selling_points[2] if len(selling_points) > 2 else "补充第三层卖点"
        if "使用场景" in role:
            return "强调产品在真实使用场景中的状态与氛围"
        if "细节" in role:
            return "突出局部细节、材质、结构或做工"
        if "效果" in role:
            return "突出体验感、结果感或前后对比式表达，但避免夸张承诺"
        if "规格" in role:
            return "突出容量、尺寸、时长等可量化信息"
        if "搭配" in role:
            return "突出与穿搭或场景的协调关系"
        return "补充展示信息并保持与主图统一风格"

    def _summarize_usage(self, payload: dict[str, Any]) -> list[str]:
        refs = payload.get("reference_images", {})
        usage_images = refs.get("usage_images", [])
        if usage_images:
            return ["参考使用图的场景氛围、动作和构图，但产品真实性以白底图为准"]
        return ["无使用图时围绕产品卖点构建自然、简洁、高级的展示场景"]

    def _join(self, values: list[str], sep: str = "，") -> str:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        return sep.join(cleaned)

    def _flatten_text(self, *values: Any) -> list[str]:
        parts: list[str] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, list):
                parts.extend(self._flatten_text(*value))
            else:
                text = str(value).strip()
                if text:
                    parts.append(text)
        return parts
