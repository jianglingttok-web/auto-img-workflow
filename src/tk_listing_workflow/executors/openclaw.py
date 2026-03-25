from __future__ import annotations

import json
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import bootstrap_runtime_environment
from ..storage import write_json


@dataclass(slots=True)
class OpenClawJobResult:
    run_id: str
    output_files: list[str]
    meta: dict[str, Any]


@dataclass(slots=True)
class ArkTextConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float
    http_retries: int
    retry_delay_seconds: float
    timeout_seconds: float


class OpenClawExecutor:
    """Hybrid prompt builder: local rule routing + optional Ark text generation."""

    DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
    DEFAULT_ARK_TEXT_MODEL = "doubao-seed-2-0-lite-260215"
    DEFAULT_ARK_TEXT_TEMPERATURE = 0.3
    DEFAULT_HTTP_RETRIES = 3
    DEFAULT_RETRY_DELAY_SECONDS = 2.0
    DEFAULT_TIMEOUT_SECONDS = 180.0
    RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}

    BENEFIT_KEYWORDS = (
        "洗发", "清洁", "去污", "除菌", "护理", "香氛", "控油", "蓬松", "修护", "保湿", "除臭", "功效", "容量", "续航",
    )
    PACKAGING_KEYWORDS = ("保留包装", "原包装", "不要新增文字", "只保留包装文字")
    JEWELRY_KEYWORDS = (
        "饰品", "项链", "耳环", "手链", "戒指", "胸针", "发夹", "配饰", "首饰", "吊坠",
    )
    SITE_LANGUAGE_MAP = {
        "TH": {"language": "泰语", "market": "泰国", "code": "th-TH"},
        "ID": {"language": "印尼语", "market": "印度尼西亚", "code": "id-ID"},
        "MY": {"language": "马来语", "market": "马来西亚", "code": "ms-MY"},
        "PH": {"language": "英语或菲律宾语", "market": "菲律宾", "code": "en-PH"},
        "VN": {"language": "越南语", "market": "越南", "code": "vi-VN"},
        "SG": {"language": "英语", "market": "新加坡", "code": "en-SG"},
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        bootstrap_runtime_environment()
        self.text_config = self._load_text_config()

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
        sub_plan: list[dict[str, str]] = []

        result: dict[str, Any] = {
            "task_mode": task_mode,
            "prompt_mode": prompt_mode,
            "reason": self._build_reason(prompt_mode, payload),
            "variables": variables,
            "site_language": self._site_language_spec(payload),
        }

        if task_mode in {"sub_only", "full_set"}:
            sub_count = int(payload["requested_output"]["sub_count"])
            sub_plan = self._build_sub_image_plan(prompt_mode, payload, sub_count)
            result["sub_image_plan"] = sub_plan

        if self.text_config is not None:
            generated = self._generate_prompts_with_model(payload, prompt_mode, variables, sub_plan)
            if task_mode in {"main_only", "full_set"}:
                result["main_image_prompt"] = generated["main_image_prompt"]
            if task_mode in {"sub_only", "full_set"}:
                result["sub_image_prompts"] = generated["sub_image_prompts"]
            result["generator"] = {
                "mode": "ark_text",
                "provider": "volcengine",
                "model": self.text_config.model,
                "base_url": self.text_config.base_url,
            }
            return result

        if task_mode in {"main_only", "full_set"}:
            result["main_image_prompt"] = self._build_main_prompt(prompt_mode, payload, variables)

        if task_mode in {"sub_only", "full_set"}:
            result["sub_image_prompts"] = [
                {"slot": item["slot"], "prompt": self._build_sub_prompt(prompt_mode, payload, variables, item["role"], item["slot"])}
                for item in sub_plan
            ]

        result["generator"] = {"mode": "deterministic_local_rules"}
        return result

    def write_image_prompts(self, payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
        result = self.build_image_prompts(payload)
        write_json(output_path, result)
        return result

    def _load_text_config(self) -> ArkTextConfig | None:
        api_key = os.environ.get("ARK_API_KEY", "").strip()
        model = os.environ.get("ARK_TEXT_MODEL", self.DEFAULT_ARK_TEXT_MODEL).strip()
        if not api_key or not model:
            return None
        return ArkTextConfig(
            api_key=api_key,
            base_url=os.environ.get("ARK_BASE_URL", self.DEFAULT_ARK_BASE_URL).strip() or self.DEFAULT_ARK_BASE_URL,
            model=model,
            temperature=float(os.environ.get("ARK_TEXT_TEMPERATURE", str(self.DEFAULT_ARK_TEXT_TEMPERATURE)) or self.DEFAULT_ARK_TEXT_TEMPERATURE),
            http_retries=max(int(os.environ.get("ARK_TEXT_HTTP_RETRIES", str(self.DEFAULT_HTTP_RETRIES)) or self.DEFAULT_HTTP_RETRIES), 1),
            retry_delay_seconds=max(
                float(os.environ.get("ARK_TEXT_RETRY_DELAY_SECONDS", str(self.DEFAULT_RETRY_DELAY_SECONDS)) or self.DEFAULT_RETRY_DELAY_SECONDS),
                0.0,
            ),
            timeout_seconds=max(
                float(os.environ.get("ARK_TEXT_TIMEOUT_SECONDS", str(self.DEFAULT_TIMEOUT_SECONDS)) or self.DEFAULT_TIMEOUT_SECONDS),
                1.0,
            ),
        )

    def _generate_prompts_with_model(
        self,
        payload: dict[str, Any],
        prompt_mode: str,
        variables: dict[str, Any],
        sub_plan: list[dict[str, str]],
    ) -> dict[str, Any]:
        if self.text_config is None:
            raise RuntimeError("ARK text config is not available")

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    self._build_model_input(payload, prompt_mode, variables, sub_plan),
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
        response = self._call_text_api(messages)
        content = self._extract_text_content(response)
        raw_result = self._parse_json_object(content)
        return self._normalize_model_result(payload["task_mode"], raw_result, sub_plan)

    def _build_system_prompt(self) -> str:
        return (
            "你是电商图生图 prompt builder，只负责输出给 Seedream 使用的中文提示词。\n"
            "你的职责边界：\n"
            "1. 不改写 task_mode。\n"
            "2. 不改写 prompt_mode。\n"
            "3. 不改写副图槽位和 role，只基于给定角色生成对应 prompt。\n"
            "4. 必须服从项目既定规则：产品主体不能变、1:1 电商图、避免低质模板感、突出主体、服务转化。\n"
            "5. visual_only 模式严禁新增文案、logo、水印、平台元素。\n"
            "6. benefit_copy 模式最多允许一句简短大字主文案，且必须克制、清晰、合规，不能压过产品主体。\n"
            "7. strict_packaging_only 模式只允许保留包装原有文字，不得新增任何文案。\n"
            "8. 副图 prompt 必须与主图或风格参考图保持统一视觉语言，同时围绕各自 role 强化差异。\n"
            "9. 如果存在 rework，要精准修改，不要整体推翻。\n"
            "10. 仅输出 JSON 对象，不要输出 markdown、解释、前后缀。\n"
            "11. 最终 prompt 必须写成图像模型可直接使用的视觉提示词，不要写成项目说明书。\n"
            "12. 先写画面结果和视觉风格，再写产品锁定要求，最后写禁止项；少用‘请基于参考图’‘本图职责’‘本图重点’‘输出应适合’这类流程语言。\n"
            "13. prompt 应尽量具体到商业摄影感、构图、镜头距离、光影、材质和场景氛围，但不要虚构任务里没有的卖点。\n"
            "输出 JSON 结构：\n"
            "{\n"
            '  "main_image_prompt": "string，可为空",\n'
            '  "sub_image_prompts": [{"slot": "sub_01", "prompt": "string"}]\n'
            "}\n"
        )
    def _build_model_input(
        self,
        payload: dict[str, Any],
        prompt_mode: str,
        variables: dict[str, Any],
        sub_plan: list[dict[str, str]],
    ) -> dict[str, Any]:
        sub_plan_with_hints = [
            {
                "slot": item["slot"],
                "role": item["role"],
                "role_hint": self._role_hint(item["role"], payload.get("selling_points", [])),
                "role_visual_direction": self._role_visual_direction(item["role"]),
            }
            for item in sub_plan
        ]
        return {
            "task_mode": payload["task_mode"],
            "prompt_mode": prompt_mode,
            "reason": self._build_reason(prompt_mode, payload),
            "product": {
                "product_id": payload.get("product_id", ""),
                "product_name": payload.get("product_name", ""),
                "category_hint": payload.get("category_hint", ""),
                "use_case": payload.get("use_case", ""),
                "variation_scope": payload.get("variation_scope", ""),
                "site": payload.get("site", ""),
                "shop_id": payload.get("shop_id", ""),
            },
            "variables": variables,
            "site_language": self._site_language_spec(payload),
            "selling_points": payload.get("selling_points", []),
            "style_requirements": payload.get("style_requirements", []),
            "compliance_requirements": payload.get("compliance_requirements", []),
            "language_requirement": self._language_requirement(payload, prompt_mode),
            "marketing_phrases": payload.get("marketing_phrases", []),
            "numeric_claims": payload.get("numeric_claims", []),
            "notes": payload.get("notes", ""),
            "reference_images": payload.get("reference_images", {}),
            "requested_output": payload.get("requested_output", {}),
            "rework": payload.get("rework", {}),
            "sub_image_plan": sub_plan_with_hints,
            "prompt_shape_requirements": {
                "goal": "写成可直接给图像模型使用的视觉提示词，而不是流程说明或任务拆解。",
                "preferred_order": ["画面结果", "构图/镜头/光线", "产品锁定", "风格质感", "禁止项"],
                "preferred_style": [
                    "先写最终会看到的画面，再补充摄影与材质语言",
                    "句子可以自然一些，但必须保持图像生成可执行性",
                    "允许克制的双画面或局部细节并置，不要写成海报策划案",
                    "避免空泛词，尽量具体到光线、景深、材质、人物姿态和商业感",
                ],
                "avoid_phrases": [
                    "请基于参考图生成",
                    "本图职责",
                    "输出应适合",
                    "不要改写任务",
                ],
            },
            "local_prompt_baseline": {
                "main_image_prompt": self._build_main_prompt(prompt_mode, payload, variables),
                "sub_image_prompts": [
                    {
                        "slot": item["slot"],
                        "prompt": self._build_sub_prompt(prompt_mode, payload, variables, item["role"], item["slot"]),
                    }
                    for item in sub_plan
                ],
            },
        }

    def _call_text_api(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self.text_config is None:
            raise RuntimeError("ARK text config is not available")

        body = json.dumps(
            {
                "model": self.text_config.model,
                "messages": messages,
                "temperature": self.text_config.temperature,
            }
        ).encode("utf-8")
        request = Request(
            url=f"{self.text_config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.text_config.api_key}",
            },
            method="POST",
        )

        def send() -> dict[str, Any]:
            with urlopen(request, timeout=self.text_config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            return self._with_retries(send)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ark text API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Ark text API network error: {exc}") from exc
        except ssl.SSLError as exc:
            raise RuntimeError(f"Ark text API SSL error: {exc}") from exc

    def _with_retries(self, action):
        if self.text_config is None:
            raise RuntimeError("ARK text config is not available")

        last_error: Exception | None = None
        for attempt in range(1, self.text_config.http_retries + 1):
            try:
                return action()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in self.RETRYABLE_HTTP_STATUS or attempt >= self.text_config.http_retries:
                    raise
            except (URLError, ssl.SSLError) as exc:
                last_error = exc
                if attempt >= self.text_config.http_retries:
                    raise

            if self.text_config.retry_delay_seconds > 0:
                time.sleep(self.text_config.retry_delay_seconds)

        if last_error is not None:
            raise last_error
        raise RuntimeError("Ark text API request failed without a captured exception")

    def _extract_text_content(self, response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            raise RuntimeError(f"Ark text API returned no choices: {response}")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
            combined = "\n".join(parts).strip()
            if combined:
                return combined
        raise RuntimeError(f"Ark text API returned unsupported message content: {message}")

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end < start:
                raise RuntimeError(f"Ark text API did not return valid JSON: {text}")
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Ark text API returned invalid JSON: {text}") from exc

    def _normalize_model_result(
        self,
        task_mode: str,
        raw_result: dict[str, Any],
        sub_plan: list[dict[str, str]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"main_image_prompt": "", "sub_image_prompts": []}

        if task_mode in {"main_only", "full_set"}:
            main_prompt = str(raw_result.get("main_image_prompt", "")).strip()
            if not main_prompt:
                raise RuntimeError("Ark text API output missing main_image_prompt")
            result["main_image_prompt"] = main_prompt

        if task_mode in {"sub_only", "full_set"}:
            raw_prompts = raw_result.get("sub_image_prompts", [])
            if not isinstance(raw_prompts, list):
                raise RuntimeError("Ark text API output field sub_image_prompts must be a list")
            prompts_by_slot = {
                str(item.get("slot", "")).strip(): str(item.get("prompt", "")).strip()
                for item in raw_prompts
                if isinstance(item, dict)
            }
            normalized: list[dict[str, str]] = []
            for item in sub_plan:
                slot = item["slot"]
                prompt = prompts_by_slot.get(slot, "")
                if not prompt:
                    raise RuntimeError(f"Ark text API output missing prompt for slot {slot}")
                normalized.append({"slot": slot, "prompt": prompt})
            result["sub_image_prompts"] = normalized

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
            "style_summary": self._join(payload.get("style_requirements", []), sep="?") or "????????????",
            "variation_scope": str(payload.get("variation_scope", "") or "").strip(),
            "usage_scene_summary": "?".join(self._summarize_usage(payload)),
            "marketing_phrases": payload.get("marketing_phrases", []),
            "numeric_claims": payload.get("numeric_claims", []),
            "selling_points": payload.get("selling_points", []),
            "compliance_requirements": payload.get("compliance_requirements", []),
            "site_language": self._site_language_spec(payload),
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
        hero_focus = selling_points or "突出主体和转化感"

        refs = payload.get("reference_images", {}) if isinstance(payload, dict) else {}


        base = [


            f"{product_name}?1:1 ?????????????????????????????????",


            f"?????{hero_focus}????{style_summary}??????{usage}?",


            f"?????{self._main_visual_direction(payload, prompt_mode)}?",


            "?????????????????????????????????????????????????",


            "???????????????????????????????????????????????????",


        ]


        if payload.get("task_type") == "same_product_fission" or refs.get("style_reference_images"):


            base.append("??????????????????????????????????????????????????????")



        if prompt_mode == "benefit_copy":
            marketing = self._join(payload.get("marketing_phrases", []), sep="；")
            numeric = self._join(payload.get("numeric_claims", []), sep="；")
            base.extend(
                [
                    "允许出现一句简短大字主文案，文案需要克制、清晰、易读，且不能压过产品主体。",
                    f"营销表达参考：{marketing or '无'}；数字信息参考：{numeric or '无'}。",
                    "产品主体占画面一半以上，不出现未经验证的功效承诺，不写多段说明文案。",
                ]
            )
        elif prompt_mode == "strict_packaging_only":
            base.append("文字规则：只允许保留产品包装原有文字，不允许在画面其他区域新增任何文案、logo、水印或促销元素。")
        else:
            base.append("文字规则：不要新增任何文案、logo、水印或平台元素，只通过场景、质感、光线和构图传达卖点。")

        if compliance:
            base.append(f"合规要求：{compliance}。")

        base.append(self._layout_guardrail(payload))
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
        role_visual = self._role_visual_direction(role)
        usage = variables["usage_scene_summary"] or "围绕产品卖点构建自然、简洁、高级的展示场景"

        lines = [
            f"{product_name}，1:1 电商副图，图位 {slot}，{role}，真实商业摄影质感。",
            f"画面方向：{role_visual}；视觉重点：{role_hint}；整体风格：{style_summary}。",
            f"场景与氛围参考：{usage}，但画面表达需要与主图保持统一视觉语言，同时在构图、镜头、光影或道具上形成差异。",
            "产品锁定：产品主体与白底图一致，不改变外形、颜色、材质、包装、品牌信息和结构细节。",
        ]

        if prompt_mode == "benefit_copy":
            marketing = self._join(payload.get("marketing_phrases", []), sep="；")
            numeric = self._join(payload.get("numeric_claims", []), sep="；")
            lines.append("如该图位适合加字，只允许一条简短强化信息，且必须服务于当前图位职责，不能压过主体。")
            if marketing:
                lines.append(f"营销表达参考：{marketing}。")
            if numeric:
                lines.append(f"数字信息参考：{numeric}。")
        elif prompt_mode == "strict_packaging_only":
            lines.append("文字规则：不得新增任何文字，只允许保留产品包装原有文字。")
        else:
            lines.append("文字规则：不要新增文字，仅通过场景、细节、构图和道具传达信息。")

        compliance = self._join(payload.get("compliance_requirements", []), sep="；")
        if compliance:
            lines.append(f"合规要求：{compliance}。")
        lines.append(self._layout_guardrail(payload))
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

    def _site_language_spec(self, payload: dict[str, Any]) -> dict[str, str]:
        site = str(payload.get("site", "") or "").strip().upper()
        info = self.SITE_LANGUAGE_MAP.get(site)
        if info is None:
            return {
                "site": site,
                "market": site or "未知站点",
                "language": "与目标站点一致的当地语言",
                "code": "",
            }
        return {"site": site, **info}

    def _language_requirement(self, payload: dict[str, Any], prompt_mode: str) -> str:
        spec = self._site_language_spec(payload)
        if prompt_mode == "visual_only":
            return (
                f"语言要求：目标站点为 {spec['market']}（{spec['site'] or '未标记'}），默认不要新增任何画面文字；"
                f"如因任务特殊需要出现极少量文字，也必须使用 {spec['language']}，不能混入中文或错误语种。"
            )
        if prompt_mode == "strict_packaging_only":
            return (
                f"语言要求：目标站点为 {spec['market']}（{spec['site'] or '未标记'}），不新增任何画面文字；"
                f"仅保留产品包装原有文字，不对包装原字样做翻译或改写。"
            )
        return (
            f"语言要求：目标站点为 {spec['market']}（{spec['site'] or '未标记'}），所有新增文案必须使用 {spec['language']}，"
            f"表达方式符合当地电商习惯，不能使用中文、英文占位翻译或错误语种混写。"
        )

    def _main_visual_direction(self, payload: dict[str, Any], prompt_mode: str) -> str:
        product_text = " ".join(self._flatten_text(payload.get("product_name", ""), payload.get("category_hint", ""), payload.get("notes", "")))
        refs = payload.get("reference_images", {}) if isinstance(payload, dict) else {}
        style_refs = refs.get("style_reference_images", []) or refs.get("fission_reference", [])
        task_type = str(payload.get("task_type", "") or "").strip()
        use_case = str(payload.get("use_case", "") or "").strip()
        variation_scope = str(payload.get("variation_scope", "") or "").strip()

        if task_type == "same_style_product_swap":
            return "严格延续参考图的整体风格、光线、构图和氛围，但主体必须替换为本次提交的新产品，避免改成另一种视觉体系。"
        if task_type == "same_product_fission" or use_case == "image-to-image-fission" or style_refs:
            if variation_scope:
                return f"以参考图为主导延续视觉风格与主体呈现，保持同款产品不变，仅在允许范围内调整背景、构图或镜头语言：{variation_scope}"
            return "以参考图为主导延续视觉风格与主体呈现，保持同款产品不变，只做受控的场景和构图变化。"
        if any(keyword in product_text for keyword in self.JEWELRY_KEYWORDS):
            return "????????????????????????????????????????????????"
        if prompt_mode == "benefit_copy":
            return "??????????????????????????????????????"
        return "???????????????????????????????????"
    def _supports_refined_composite_layout(self, payload: dict[str, Any]) -> bool:
        text_blob = " ".join(
            self._flatten_text(
                payload.get("product_name", ""),
                payload.get("category_hint", ""),
                payload.get("style_requirements", []),
                payload.get("notes", ""),
            )
        )
        return any(keyword in text_blob for keyword in self.JEWELRY_KEYWORDS)

    def _layout_guardrail(self, payload: dict[str, Any]) -> str:
        if self._supports_refined_composite_layout(payload):
            return "版式限制：允许为饰品主图或副图使用克制的双画面、局部细节并置或左右分栏，但必须统一光线、色调和商业质感，不能做成廉价拼贴、多宫格海报或低质模板排版；同时避免模糊主体、错误手部和错误人体结构。"
        return "版式限制：避免廉价拼贴感、多宫格、低质模板排版、模糊主体、错误手部和错误人体结构。"

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

    def _role_visual_direction(self, role: str) -> str:
        if "核心展示" in role or "核心卖点" in role:
            return "居中主体展示，主体完整清晰，背景简洁但有高级层次"
        if "佩戴" in role:
            return "真实佩戴场景，中近景构图，人物动作自然，产品佩戴位置清晰可见"
        if "细节" in role:
            return "微距或近景特写，突出材质、结构、切面、边缘和做工细节"
        if "搭配" in role:
            return "产品与穿搭或配饰形成协调搭配，突出整体造型关系"
        if "氛围" in role:
            return "以光影、景深和辅助道具强化氛围感，但主体仍然突出"
        if "收尾" in role:
            return "简洁统一的收束画面，商业感完整，适合作为套图结尾"
        if "使用场景" in role:
            return "真实使用状态，场景可信，动作自然，产品使用关系明确"
        if "效果" in role:
            return "强调体验结果和感受变化，但表达克制，不做夸张对比"
        if "规格" in role:
            return "用构图和陈列强化尺寸、容量或数量感，画面干净清晰"
        if "组合" in role:
            return "多元素组合陈列，但主产品依然是视觉中心"
        return "保持统一视觉语言，在构图、镜头或光影上做出有区分度的变化"
    def _summarize_usage(self, payload: dict[str, Any]) -> list[str]:
        refs = payload.get("reference_images", {})
        usage_images = refs.get("usage_images", [])
        if usage_images:
            return ["?????????????????????????????"]

        style_refs = refs.get("style_reference_images", []) or refs.get("fission_reference", [])
        task_type = str(payload.get("task_type", "") or "").strip()
        variation_scope = str(payload.get("variation_scope", "") or "").strip()
        if task_type == "same_style_product_swap":
            return ["参考图用于锁定整体风格、构图和光线，新产品主体必须来自本次提交的白底图商品。"]
        if style_refs:
            if variation_scope:
                return [f"参考图用于锁定同款产品的视觉方向，仅在允许范围内做受控变化：{variation_scope}"]
            return ["参考图用于锁定同款产品的视觉方向，只允许受控的背景或构图变化。"]

        return ["??????????????????????????"]
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





