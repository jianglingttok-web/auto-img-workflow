from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .ai.image_workflow import ImageWorkflowBuilder, SeedreamJobPlanner, infer_task_mode
from .executors.openclaw import OpenClawExecutor
from .executors.seedream import SeedreamExecutor
from .integrations import FeishuBitableClient, FeishuNotifier
from .intake.feishu_mapper import FeishuImageTaskMapper
from .media import ImageAssetsBuilder, PreviewBuilder
from .storage import read_json, write_json
from .task_manager import InvalidTransitionError, TaskManager

_ATTACHMENT_PREFIX = {
    "产品白底图": "product_white",
    "使用图": "usage",
    "已有主图/风格参考图": "style_ref",
}
_DEFAULT_IMAGE_TASK_STATUS = "待处理"
_DEFAULT_IMAGE_REVIEW_STATUS = "待审核"
_REVIEW_STATUS_BY_STAGE = {"main": "待审核主图", "sub": "待审核副图"}


class LocalFeishuImageWorker:
    def __init__(
        self,
        tasks_root: Path,
        *,
        task_id_filter: str = "",
        client: FeishuBitableClient | None = None,
        sync_bitable_record: bool = False,
    ) -> None:
        self.tasks_root = Path(tasks_root)
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self.task_id_filter = task_id_filter.strip()
        self.client = client or FeishuBitableClient.from_env()
        self.sync_bitable_record = sync_bitable_record
        self.notifier = FeishuNotifier(self.client)
        self.mapper = FeishuImageTaskMapper()
        self.manager = TaskManager(self.tasks_root)
        self.image_builder = ImageWorkflowBuilder()
        self.seedream = SeedreamExecutor.from_env()

    def run_forever(self, *, poll_interval_seconds: int = 30) -> None:
        interval = max(int(poll_interval_seconds), 5)
        while True:
            self.run_once()
            time.sleep(interval)

    def run_once(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for decision in self._iter_runnable_decisions():
            task_id = str(decision.get("task_id", "") or "")
            if self.task_id_filter and task_id != self.task_id_filter:
                continue
            try:
                results.append(self._process_decision(decision))
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "task_id": task_id,
                        "record_id": decision.get("record_id", ""),
                        "action": decision.get("next_action", ""),
                        "error": str(exc),
                    }
                )
        return {"ok": True, "count": len(results), "results": results}

    def _process_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        record_id = str(decision.get("record_id", "") or "")
        if not record_id:
            raise ValueError(f"missing record_id in decision: {decision}")

        record = self.client.get_record(record_id)
        task_dir = self._refresh_task_from_record(record)
        action = str(decision.get("next_action", "") or "")
        round_number = int(decision.get("target_round", 1) or 1)
        rework_reason = self._stringify_field(record.get("fields", {}).get("审核意见")) if action.startswith("rerun_") else ""

        if action in {"run_main_generation", "rerun_main_generation"}:
            return self._run_generation_stage(
                task_dir=task_dir,
                record=record,
                round_number=round_number,
                stage="main",
                rework_reason=rework_reason,
            )
        if action in {"run_sub_generation", "rerun_sub_generation"}:
            return self._run_generation_stage(
                task_dir=task_dir,
                record=record,
                round_number=round_number,
                stage="sub",
                rework_reason=rework_reason,
            )

        raise ValueError(f"unsupported worker action: {action}")

    def _run_generation_stage(
        self,
        *,
        task_dir: Path,
        record: dict[str, Any],
        round_number: int,
        stage: str,
        rework_reason: str,
    ) -> dict[str, Any]:
        if stage not in {"main", "sub"}:
            raise ValueError(f"unsupported stage: {stage}")

        record_id = self._record_id(record)
        self.image_builder.build_standardized_task(task_dir)
        self._safe_advance(task_dir, "image_generation_pending", note=f"worker preparing {stage} generation")

        oc_input = self.image_builder.build_oc_input(
            task_dir,
            round_number=round_number,
            rework_reason=rework_reason,
            rework_scope=stage,
        )
        oc_output_path = task_dir / "prompts" / f"round_{round_number:02d}_oc_output.json"
        oc_output = OpenClawExecutor(task_dir).write_image_prompts(oc_input, oc_output_path)
        jobs_path, jobs_payload = self._build_stage_jobs(task_dir, oc_output_path, round_number=round_number, stage=stage)
        seedream_result = self.seedream.run_jobs(task_dir, jobs_path)
        preview_payload = PreviewBuilder().build_round_previews(task_dir, round_number)
        image_assets = ImageAssetsBuilder().sync_round(task_dir, round_number)
        self._safe_advance(task_dir, "image_review_pending", note=f"worker generated {stage} assets")
        backfill = {"skipped": True, "reason": "sync_bitable_record disabled"}
        if self.sync_bitable_record:
            backfill = self._backfill_review_assets(task_dir, record_id=record_id, round_number=round_number, stage=stage)
        notify = self._notify_review(task_dir, record=record, round_number=round_number, stage=stage)

        result = {
            "ok": True,
            "task_id": task_dir.name,
            "record_id": record_id,
            "stage": stage,
            "round": round_number,
            "rework_reason": rework_reason,
            "task_dir": str(task_dir),
            "oc_output": str(oc_output_path),
            "jobs_file": str(jobs_path),
            "job_count": len(jobs_payload.get("jobs", [])),
            "preview_payload": preview_payload,
            "image_assets": image_assets,
            "backfill": backfill,
            "notify": notify,
            "generator": oc_output.get("generator", {}),
            "seedream_result_file": str(task_dir / "media" / f"round_{round_number:02d}_seedream_results.json"),
        }
        result_path = task_dir / "review" / f"round_{round_number:02d}_worker_{stage}_result.json"
        write_json(result_path, result)
        result["result_path"] = str(result_path)
        return result

    def _build_stage_jobs(self, task_dir: Path, oc_output_path: Path, *, round_number: int, stage: str) -> tuple[Path, dict[str, Any]]:
        payload = SeedreamJobPlanner().build_jobs(task_dir, oc_output_path, round_number=round_number)
        target_type = "main" if stage == "main" else "sub"
        filtered_jobs = [job for job in payload.get("jobs", []) if str(job.get("image_type", "") or "") == target_type]
        if not filtered_jobs:
            raise ValueError(f"no {target_type} jobs produced for stage {stage}: {task_dir}")

        filtered_payload = {
            "task_id": payload.get("task_id", task_dir.name),
            "round": round_number,
            "task_mode": payload.get("task_mode", ""),
            "prompt_mode": payload.get("prompt_mode", ""),
            "jobs": filtered_jobs,
        }
        jobs_path = task_dir / "prompts" / f"round_{round_number:02d}_seedream_jobs_{stage}_only.json"
        write_json(jobs_path, filtered_payload)
        return jobs_path, filtered_payload

    def _refresh_task_from_record(self, record: dict[str, Any]) -> Path:
        fields = record.get("fields", {}) if isinstance(record, dict) else {}
        normalized = self.mapper.normalize_record(fields)
        product_brief = self.mapper.parse_record(normalized)
        task_dir = self.tasks_root / product_brief["task_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        write_json(task_dir / "product_brief.json", product_brief)
        if not (task_dir / "manifest.json").is_file():
            self.manager.bootstrap_from_product_brief(task_dir)
        normalized_with_downloads = self._materialize_attachments(task_dir, normalized)
        write_json(task_dir / "intake" / "feishu_record.json", normalized_with_downloads)
        write_json(task_dir / "intake" / "feishu_record_raw.json", record)
        self.image_builder.build_standardized_task(task_dir)
        return task_dir

    def _materialize_attachments(self, task_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
        normalized = self.mapper.normalize_record(record)
        intake_dir = task_dir / "intake"
        for field_name in self.mapper.ATTACHMENT_FIELDS:
            attachments = normalized.get(field_name, [])
            if not isinstance(attachments, list):
                continue
            prefix = _ATTACHMENT_PREFIX.get(field_name, "attachment")
            for index, attachment in enumerate(attachments, start=1):
                if not isinstance(attachment, dict):
                    continue
                ext = Path(str(attachment.get("name", "") or "")).suffix or Path(str(attachment.get("url", "") or "")).suffix or ".bin"
                target_name = f"{prefix}_{index:02d}{ext}"
                target_path = intake_dir / target_name
                self.client.download_attachment(attachment, target_path)
                attachment["path"] = target_name
        return normalized

    def _backfill_review_assets(self, task_dir: Path, *, record_id: str, round_number: int, stage: str) -> dict[str, Any]:
        preview_manifest = read_json(task_dir / "media" / f"round_{round_number:02d}" / "preview" / "preview_manifest.json")
        fields: dict[str, Any] = {
            "图片任务状态": _REVIEW_STATUS_BY_STAGE[stage],
            "图片审核状态": _DEFAULT_IMAGE_REVIEW_STATUS,
            "最新轮次": round_number,
            "异常原因": "",
            "审核意见": "",
        }

        main_preview = Path(str(preview_manifest.get("main_preview", "") or ""))
        sub_contact_sheet = Path(str(preview_manifest.get("sub_contact_sheet", "") or ""))
        uploaded: dict[str, Any] = {}
        if main_preview.is_file():
            uploaded["主图预览"] = [self.client.upload_media(main_preview, parent_type="bitable_file")]
        if sub_contact_sheet.is_file():
            uploaded["副图拼图预览"] = [self.client.upload_media(sub_contact_sheet, parent_type="bitable_file")]
        fields.update(uploaded)

        updated_record = self.client.update_record(record_id, fields)
        result = {
            "record_id": record_id,
            "round": round_number,
            "stage": stage,
            "updated_fields": list(fields.keys()),
            "record": updated_record,
        }
        result_path = task_dir / "review" / f"round_{round_number:02d}_feishu_backfill_result.json"
        write_json(result_path, result)
        result["result_path"] = str(result_path)
        return result

    def _notify_review(self, task_dir: Path, *, record: dict[str, Any], round_number: int, stage: str) -> dict[str, Any]:
        recipient = self._resolve_notification_recipient(record)
        payload = self.notifier.build_image_review_payload(
            task_id=task_dir.name,
            product_name=self._load_product_name(task_dir),
            round_number=round_number,
            task_dir=task_dir,
            review_status=_DEFAULT_IMAGE_REVIEW_STATUS,
            workflow_status=_REVIEW_STATUS_BY_STAGE[stage],
            receiver_open_id=recipient["receive_id"],
            receiver_name=recipient["name"],
        )
        result = self.notifier.notify_image_review(
            receive_id=recipient["receive_id"],
            receive_id_type="open_id",
            payload=payload,
        )
        result["recipient"] = recipient
        result_path = task_dir / "review" / f"round_{round_number:02d}_feishu_notification_result.json"
        write_json(result_path, result)
        result["result_path"] = str(result_path)
        return result

    def _resolve_notification_recipient(self, record: dict[str, Any]) -> dict[str, str]:
        fields = record.get("fields", {}) if isinstance(record, dict) else {}
        for field_name in ("提交人", "审核人"):
            person = self._extract_person_from_field(fields, field_name)
            if person:
                return person
        raise ValueError(f"no notification recipient found for record {self._record_id(record)}")

    def _extract_person_from_field(self, fields: dict[str, Any], field_name: str) -> dict[str, str] | None:
        value = fields.get(field_name)
        if not isinstance(value, list):
            return None
        for item in value:
            if not isinstance(item, dict):
                continue
            receive_id = str(item.get("id", "") or item.get("open_id", "") or item.get("user_id", "") or "")
            if not receive_id:
                continue
            return {
                "field_name": field_name,
                "receive_id": receive_id,
                "name": str(item.get("name", "") or item.get("en_name", "") or ""),
            }
        return None

    def _load_product_name(self, task_dir: Path) -> str:
        for file_name in ("image_task.json", "product_brief.json"):
            path = task_dir / file_name
            if not path.is_file():
                continue
            payload = read_json(path)
            product_name = str(payload.get("product_name", "") or "")
            if product_name:
                return product_name
        return task_dir.name

    def _safe_advance(self, task_dir: Path, new_status: str, *, note: str) -> None:
        manifest = read_json(task_dir / "manifest.json")
        current = str(manifest.get("status", "") or "")
        if current == new_status:
            return
        try:
            self.manager.advance_status(task_dir, new_status, note=note)
        except InvalidTransitionError:
            return

    def _iter_runnable_decisions(self):
        page_token = ""
        while True:
            payload = self.client.list_records(page_size=100, page_token=page_token)
            for item in payload.get("items", []):
                decision = self._decide_feishu_task(item)
                if decision.get("runnable"):
                    yield decision
            if not payload.get("has_more"):
                break
            page_token = str(payload.get("page_token", "") or "")
            if not page_token:
                break

    def _decide_feishu_task(self, item: dict[str, Any]) -> dict[str, Any]:
        summary = self._summarize_feishu_record(item)
        if summary.get("parse_error"):
            return {
                **summary,
                "runnable": False,
                "next_action": "invalid_record",
                "reason": summary["parse_error"],
            }

        task_status = str(summary.get("image_task_status", _DEFAULT_IMAGE_TASK_STATUS) or _DEFAULT_IMAGE_TASK_STATUS)
        review_status = str(summary.get("image_review_status", _DEFAULT_IMAGE_REVIEW_STATUS) or _DEFAULT_IMAGE_REVIEW_STATUS)
        task_mode = str(summary.get("task_mode", "") or "")
        latest_round = int(summary.get("latest_round", 1) or 1)
        local_status = self._load_local_manifest_status(str(summary.get("task_id", "") or ""))

        if local_status and local_status not in {"product_created", "image_generation_pending"}:
            return {
                **summary,
                "local_status": local_status,
                "runnable": False,
                "next_action": "local_workflow_active",
                "reason": f"????? {local_status}???????????",
            }

        if review_status == "已打回" and task_status == "待生成副图":
            return {
                **summary,
                "runnable": True,
                "next_action": "rerun_sub_generation",
                "reason": "副图审核已打回，重跑副图",
                "target_round": latest_round + 1,
            }
        if task_status in {"异常", "待人工处理", "已交付", "已通过", "生图中", "待审核主图", "待审核副图"}:
            return {
                **summary,
                "runnable": False,
                "next_action": task_status,
                "reason": f"任务状态为 {task_status}",
            }
        if task_status == "待生成副图":
            return {
                **summary,
                "runnable": True,
                "next_action": "run_sub_generation",
                "reason": "状态已推进到待生成副图，可以直接跑副图",
                "target_round": latest_round,
            }
        if task_status == "重做中":
            if task_mode == "sub_only":
                return {
                    **summary,
                    "runnable": True,
                    "next_action": "rerun_sub_generation",
                    "reason": "仅副图任务进入重做中，默认重跑副图",
                    "target_round": latest_round + 1,
                }
            return {
                **summary,
                "runnable": True,
                "next_action": "rerun_main_generation",
                "reason": "任务进入重做中，默认从主图阶段重跑",
                "target_round": latest_round + 1,
            }
        if review_status == "已打回":
            if task_mode == "sub_only":
                return {
                    **summary,
                    "runnable": True,
                    "next_action": "rerun_sub_generation",
                    "reason": "审核已打回，重跑副图",
                    "target_round": latest_round + 1,
                }
            return {
                **summary,
                "runnable": True,
                "next_action": "rerun_main_generation",
                "reason": "审核已打回，默认从主图阶段重跑",
                "target_round": latest_round + 1,
            }
        if task_mode in {"main_only", "full_set"}:
            return {
                **summary,
                "runnable": True,
                "next_action": "run_main_generation",
                "reason": "待处理任务默认先跑主图阶段",
                "target_round": latest_round,
            }
        return {
            **summary,
            "runnable": True,
            "next_action": "run_sub_generation",
            "reason": "仅副图任务默认直接跑副图阶段",
            "target_round": latest_round,
        }

    def _summarize_feishu_record(self, item: dict[str, Any]) -> dict[str, Any]:
        fields = item.get("fields", {}) if isinstance(item, dict) else {}
        normalized = self.mapper.normalize_record(fields)
        image_task_status = self._stringify_field(normalized.get("图片任务状态")) or _DEFAULT_IMAGE_TASK_STATUS
        image_review_status = self._stringify_field(normalized.get("图片审核状态")) or _DEFAULT_IMAGE_REVIEW_STATUS
        latest_round = self._coerce_non_negative_int(normalized.get("最新轮次", 1), default=1)
        try:
            parsed = self.mapper.parse_record(normalized)
            task_mode = infer_task_mode(parsed["image_rules"]["main_image_count"], parsed["image_rules"]["sub_image_count"])
        except Exception as exc:
            return {
                "record_id": self._record_id(item),
                "task_id": self._stringify_field(normalized.get("任务ID")),
                "product_name": self._stringify_field(normalized.get("产品名称")),
                "shop_id": self._stringify_field(normalized.get("店铺")),
                "target_market": self._stringify_field(normalized.get("站点")),
                "image_task_status": image_task_status,
                "image_review_status": image_review_status,
                "latest_round": latest_round,
                "parse_error": str(exc),
            }

        return {
            "record_id": self._record_id(item),
            "task_id": parsed["task_id"],
            "product_id": parsed["product_id"],
            "product_name": parsed["product_name"],
            "shop_id": parsed["shop_id"],
            "target_market": parsed["target_market"],
            "task_mode": task_mode,
            "main_image_count": parsed["image_rules"]["main_image_count"],
            "sub_image_count": parsed["image_rules"]["sub_image_count"],
            "image_task_status": image_task_status,
            "image_review_status": image_review_status,
            "latest_round": latest_round,
        }

    def _stringify_field(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            for item in value:
                rendered = self._stringify_field(item)
                if rendered:
                    return rendered
            return ""
        if isinstance(value, dict):
            for key in ("text", "name", "value", "label", "en_name"):
                rendered = value.get(key)
                if rendered not in (None, ""):
                    return self._stringify_field(rendered)
            return ""
        return str(value).strip()

    def _coerce_non_negative_int(self, value: Any, default: int) -> int:
        if value in (None, ""):
            return default
        number = int(float(value))
        if number < 0:
            raise ValueError(f"count must be non-negative, got {value}")
        return number

    def _load_local_manifest_status(self, task_id: str) -> str:
        if not task_id:
            return ""
        manifest_path = self.tasks_root / task_id / "manifest.json"
        if not manifest_path.is_file():
            return ""
        manifest = read_json(manifest_path)
        return str(manifest.get("status", "") or "")

    def _record_id(self, item: dict[str, Any]) -> str:
        return str(item.get("record_id", "") or item.get("id", "") or "")
