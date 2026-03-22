from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .ai.image_workflow import ImageWorkflowBuilder, SeedreamJobPlanner, infer_task_mode
from .config import bootstrap_runtime_environment
from .data.build_listing_package import ListingPackageBuilder
from .executors.openclaw import OpenClawExecutor
from .executors.seedream import SeedreamExecutor
from .integrations import (
    FeishuBitableClient,
    FeishuCallbackServer,
    FeishuLongConnectionReceiver,
    FeishuMessageReviewProcessor,
    FeishuNotifier,
)
from .intake.feishu_mapper import FeishuImageTaskMapper
from .media import ImageAssetsBuilder, PreviewBuilder
from .models import utc_now_iso
from .preflight import run_preflight
from .storage import read_json, write_json
from .task_manager import TaskManager

_ATTACHMENT_PREFIX = {
    "产品白底图": "product_white",
    "使用图": "usage",
    "已有主图/风格参考图": "style_ref",
}
_DEFAULT_IMAGE_TASK_STATUS = "待处理"
_DEFAULT_IMAGE_REVIEW_STATUS = "待审核"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TK listing workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-task", help="Create a new task workspace")
    init_parser.add_argument("--task-id", required=True)
    init_parser.add_argument("--product-id", required=True)
    init_parser.add_argument("--market", required=True)
    init_parser.add_argument("--shop-id", required=True)
    init_parser.add_argument("--category", required=True)

    import_feishu_parser = subparsers.add_parser("import-feishu-image-task", help="Import one Chinese Feishu image-task record into product_brief.json")
    import_feishu_parser.add_argument("--record", required=True)
    import_feishu_parser.add_argument("--tasks-root", default="runtime/tasks")

    feishu_list_parser = subparsers.add_parser("feishu-list-image-tasks", help="List live Feishu Bitable image-task records")
    feishu_list_parser.add_argument("--page-size", type=int, default=10)
    feishu_list_parser.add_argument("--page-token", default="")
    feishu_list_parser.add_argument("--view-id", default="")
    feishu_list_parser.add_argument("--raw", action="store_true")

    feishu_runnable_parser = subparsers.add_parser("feishu-list-runnable-image-tasks", help="List live Feishu image tasks that are currently runnable")
    feishu_runnable_parser.add_argument("--page-size", type=int, default=100)
    feishu_runnable_parser.add_argument("--view-id", default="")
    feishu_runnable_parser.add_argument("--include-non-runnable", action="store_true")

    feishu_evaluate_parser = subparsers.add_parser("feishu-evaluate-image-task", help="Evaluate one live Feishu image task and decide the next runnable action")
    feishu_evaluate_parser.add_argument("--task-id", default="")
    feishu_evaluate_parser.add_argument("--record-id", default="")
    feishu_evaluate_parser.add_argument("--view-id", default="")

    import_live_feishu_parser = subparsers.add_parser("import-feishu-image-task-live", help="Import one live Feishu Bitable image-task record")
    import_live_feishu_parser.add_argument("--record-id", required=True)
    import_live_feishu_parser.add_argument("--tasks-root", default="runtime/tasks")

    backfill_feishu_parser = subparsers.add_parser("backfill-feishu-image-task", help="Backfill previews and workflow status to one live Feishu record")
    backfill_feishu_parser.add_argument("--task-dir", required=True)
    backfill_feishu_parser.add_argument("--round", type=int, default=1)
    backfill_feishu_parser.add_argument("--record-id", default="")
    backfill_feishu_parser.add_argument("--task-status", default="待审核副图")
    backfill_feishu_parser.add_argument("--review-status", default="待审核")
    backfill_feishu_parser.add_argument("--package-link", default="")
    backfill_feishu_parser.add_argument("--suite-link", default="")
    backfill_feishu_parser.add_argument("--upload-suite-result", action="store_true")
    backfill_feishu_parser.add_argument("--keep-error-reason", action="store_true")
    backfill_feishu_parser.add_argument("--status-only", action="store_true")

    notify_feishu_parser = subparsers.add_parser("notify-feishu-image-review", help="Send Feishu review notification with previews to one member")
    notify_feishu_parser.add_argument("--task-dir", required=True)
    notify_feishu_parser.add_argument("--round", type=int, default=1)
    notify_feishu_parser.add_argument("--record-id", default="")
    notify_feishu_parser.add_argument("--recipient-field", default="提交人")
    notify_feishu_parser.add_argument("--fallback-field", default="审核人")
    notify_feishu_parser.add_argument("--receive-id", default="")
    notify_feishu_parser.add_argument("--receive-id-type", default="open_id")
    notify_feishu_parser.add_argument("--bundle-link", default="")
    notify_feishu_parser.add_argument("--table-link", default="")
    notify_feishu_parser.add_argument("--task-status", default="待审核副图")
    notify_feishu_parser.add_argument("--review-status", default="待审核")

    notify_feishu_delivery_parser = subparsers.add_parser("notify-feishu-image-delivery", help="Send full generated image set to one member after approval")
    notify_feishu_delivery_parser.add_argument("--task-dir", required=True)
    notify_feishu_delivery_parser.add_argument("--round", type=int, default=1)
    notify_feishu_delivery_parser.add_argument("--record-id", default="")
    notify_feishu_delivery_parser.add_argument("--recipient-field", default="提交人")
    notify_feishu_delivery_parser.add_argument("--fallback-field", default="审核人")
    notify_feishu_delivery_parser.add_argument("--receive-id", default="")
    notify_feishu_delivery_parser.add_argument("--receive-id-type", default="open_id")
    notify_feishu_delivery_parser.add_argument("--bundle-link", default="")
    notify_feishu_delivery_parser.add_argument("--include-images", action="store_true")
    notify_feishu_delivery_parser.add_argument("--no-preview", action="store_true")
    notify_feishu_delivery_parser.add_argument("--workflow-status", default="image_review_passed")
    notify_feishu_delivery_parser.add_argument("--delivery-status", default="已交付")

    sync_feishu_review_parser = subparsers.add_parser("sync-feishu-image-review", help="Sync image review decision from live Feishu record into local workflow")
    sync_feishu_review_parser.add_argument("--task-dir", required=True)
    sync_feishu_review_parser.add_argument("--record-id", default="")

    process_feishu_message_parser = subparsers.add_parser("process-feishu-review-message", help="Process one Feishu message event payload and sync review result")
    process_feishu_message_parser.add_argument("--event-file", required=True)
    process_feishu_message_parser.add_argument("--tasks-root", default="runtime/tasks")

    serve_feishu_callback_parser = subparsers.add_parser("serve-feishu-callback", help="Run a local HTTP server for Feishu message callbacks")
    serve_feishu_callback_parser.add_argument("--tasks-root", default="runtime/tasks")
    serve_feishu_callback_parser.add_argument("--host", default="127.0.0.1")
    serve_feishu_callback_parser.add_argument("--port", type=int, default=8000)
    serve_feishu_callback_parser.add_argument("--callback-path", default="/feishu/callback")
    serve_feishu_callback_parser.add_argument("--health-path", default="/healthz")

    long_connection_parser = subparsers.add_parser("run-feishu-long-connection", help="Run Feishu official SDK long-connection event receiver")
    long_connection_parser.add_argument("--tasks-root", default="runtime/tasks")
    long_connection_parser.add_argument("--log-level", default="INFO")

    worker_parser = subparsers.add_parser("run-feishu-image-worker", help="Poll Feishu tasks and run the local image workflow automatically")
    worker_parser.add_argument("--tasks-root", default="runtime/tasks")
    worker_parser.add_argument("--poll-interval", type=int, default=30)
    worker_parser.add_argument("--task-id", default="")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--sync-bitable", action="store_true")

    build_image_task_parser = subparsers.add_parser("build-image-task", help="Build standardized image_task.json from task inputs")
    build_image_task_parser.add_argument("--task-dir", required=True)

    build_oc_input_parser = subparsers.add_parser("build-oc-input", help="Build OC input payload from task inputs")
    build_oc_input_parser.add_argument("--task-dir", required=True)
    build_oc_input_parser.add_argument("--round", type=int, default=1)
    build_oc_input_parser.add_argument("--rework-reason", default="")
    build_oc_input_parser.add_argument("--rework-scope", default="")

    build_oc_output_parser = subparsers.add_parser("build-oc-output", help="Build OC output payload from OC input")
    build_oc_output_parser.add_argument("--task-dir", required=True)
    build_oc_output_parser.add_argument("--oc-input", required=True)
    build_oc_output_parser.add_argument("--round", type=int, default=1)

    build_seedream_jobs_parser = subparsers.add_parser("build-seedream-jobs", help="Build Seedream jobs from OC output")
    build_seedream_jobs_parser.add_argument("--task-dir", required=True)
    build_seedream_jobs_parser.add_argument("--oc-output", required=True)
    build_seedream_jobs_parser.add_argument("--round", type=int, default=1)

    build_previews_parser = subparsers.add_parser("build-previews", help="Build main preview and sub contact sheet for one round")
    build_previews_parser.add_argument("--task-dir", required=True)
    build_previews_parser.add_argument("--round", type=int, default=1)

    sync_image_assets_parser = subparsers.add_parser("sync-image-assets", help="Sync generated round images into image_assets.json")
    sync_image_assets_parser.add_argument("--task-dir", required=True)
    sync_image_assets_parser.add_argument("--round", type=int, required=True)

    run_seedream_jobs_parser = subparsers.add_parser("run-seedream-jobs", help="Run real Seedream jobs against Ark images API")
    run_seedream_jobs_parser.add_argument("--task-dir", required=True)
    run_seedream_jobs_parser.add_argument("--jobs-file", required=True)

    show_parser = subparsers.add_parser("show-task", help="Show manifest and listing package")
    show_parser.add_argument("--task-dir", required=True)

    advance_parser = subparsers.add_parser("advance", help="Advance task status")
    advance_parser.add_argument("--task-dir", required=True)
    advance_parser.add_argument("--to", required=True)
    advance_parser.add_argument("--note", default="")

    build_package_parser = subparsers.add_parser("build-package", help="Build listing_package.json from task inputs")
    build_package_parser.add_argument("--task-dir", required=True)

    preflight_parser = subparsers.add_parser("preflight", help="Run preflight checks")
    preflight_parser.add_argument("--task-dir", required=True)

    return parser


def _summarize_feishu_record(mapper: FeishuImageTaskMapper, item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields", {}) if isinstance(item, dict) else {}
    normalized = mapper.normalize_record(fields)
    image_task_status = mapper._optional_text(normalized, "图片任务状态") or _DEFAULT_IMAGE_TASK_STATUS
    image_review_status = mapper._optional_text(normalized, "图片审核状态") or _DEFAULT_IMAGE_REVIEW_STATUS
    latest_round = mapper._coerce_non_negative_int(normalized.get("最新轮次", 1), default=1)
    try:
        parsed = mapper.parse_record(normalized)
        task_mode = infer_task_mode(parsed["image_rules"]["main_image_count"], parsed["image_rules"]["sub_image_count"])
    except Exception as exc:
        return {
            "record_id": item.get("record_id", ""),
            "task_id": mapper._optional_text(normalized, "任务ID"),
            "product_name": mapper._optional_text(normalized, "产品名称"),
            "shop_id": mapper._optional_text(normalized, "店铺"),
            "target_market": mapper._optional_text(normalized, "站点"),
            "image_task_status": image_task_status,
            "image_review_status": image_review_status,
            "latest_round": latest_round,
            "parse_error": str(exc),
        }

    return {
        "record_id": item.get("record_id", ""),
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


def _materialize_feishu_attachments(client: FeishuBitableClient, mapper: FeishuImageTaskMapper, task_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    normalized = mapper.normalize_record(record)
    intake_dir = task_dir / "intake"

    for field_name in mapper.ATTACHMENT_FIELDS:
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
            client.download_attachment(attachment, target_path)
            attachment["path"] = target_name
    return normalized


def _iter_feishu_records(client: FeishuBitableClient, *, page_size: int = 100, view_id: str = ""):
    page_token = ""
    while True:
        payload = client.list_records(page_size=page_size, page_token=page_token, view_id=view_id)
        for item in payload.get("items", []):
            yield item
        if not payload.get("has_more"):
            break
        page_token = payload.get("page_token", "")
        if not page_token:
            break


def _find_feishu_record(client: FeishuBitableClient, *, task_id: str = "", record_id: str = "", view_id: str = "") -> dict[str, Any]:
    if record_id:
        return client.get_record(record_id)
    if not task_id:
        raise ValueError("pass --task-id or --record-id")

    for item in _iter_feishu_records(client, page_size=100, view_id=view_id):
        fields = item.get("fields", {}) if isinstance(item, dict) else {}
        if str(fields.get("任务ID", "") or "").strip() == task_id:
            return item

    raise ValueError(f"Feishu task not found: {task_id}")


def _resolve_feishu_record_id(task_dir: Path, explicit_record_id: str) -> str:
    if explicit_record_id:
        return explicit_record_id

    raw_record_path = task_dir / "intake" / "feishu_record_raw.json"
    if raw_record_path.is_file():
        payload = read_json(raw_record_path)
        record_id = str(payload.get("record_id", "") or payload.get("id", "") or "")
        if record_id:
            return record_id

    raise ValueError("missing Feishu record id; pass --record-id or import from live Feishu first")


def _load_round_preview_manifest(task_dir: Path, round_number: int) -> dict[str, Any]:
    preview_dir = task_dir / "media" / f"round_{round_number:02d}" / "preview"
    manifest_path = preview_dir / "preview_manifest.json"
    if manifest_path.is_file():
        payload = read_json(manifest_path)
    else:
        payload = {
            "round": round_number,
            "main_preview": str(preview_dir / "main_preview.jpg"),
            "sub_contact_sheet": str(preview_dir / "sub_contact_sheet.jpg"),
        }

    main_preview = Path(str(payload.get("main_preview", "") or ""))
    sub_contact_sheet = Path(str(payload.get("sub_contact_sheet", "") or ""))
    if not main_preview.is_file():
        raise FileNotFoundError(f"main preview not found for round {round_number}: {main_preview}")
    if not sub_contact_sheet.is_file():
        raise FileNotFoundError(f"sub contact sheet not found for round {round_number}: {sub_contact_sheet}")

    return {
        "round": round_number,
        "main_preview": main_preview,
        "sub_contact_sheet": sub_contact_sheet,
    }


def _build_round_suite_bundle(task_dir: Path, round_number: int) -> Path:
    round_dir = task_dir / "media" / f"round_{round_number:02d}"
    main_dir = round_dir / "main"
    sub_dir = round_dir / "sub"
    preview_dir = round_dir / "preview"
    bundle_path = task_dir / "review" / f"round_{round_number:02d}_suite_bundle.zip"

    candidates: list[Path] = []
    for directory in (main_dir, sub_dir, preview_dir):
        if directory.is_dir():
            candidates.extend(sorted(path for path in directory.iterdir() if path.is_file()))

    if not candidates:
        raise FileNotFoundError(f"no round assets found to bundle: {round_dir}")

    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in candidates:
            archive.write(path, arcname=str(path.relative_to(round_dir)))
    return bundle_path


def _build_feishu_backfill_payload(args: argparse.Namespace, task_dir: Path) -> tuple[dict[str, Any], dict[str, Path | int | str]]:
    preview_manifest = _load_round_preview_manifest(task_dir, args.round)
    fields: dict[str, Any] = {
        "图片任务状态": args.task_status,
        "图片审核状态": args.review_status,
        "最新轮次": args.round,
    }
    if not args.keep_error_reason:
        fields["异常原因"] = ""
    if args.package_link:
        fields["完整图片包链接"] = args.package_link
    if args.suite_link:
        fields["套图结果链接"] = args.suite_link

    local_assets: dict[str, Path | int | str] = {
        "round": args.round,
        "main_preview": preview_manifest["main_preview"],
        "sub_contact_sheet": preview_manifest["sub_contact_sheet"],
    }
    if args.upload_suite_result:
        local_assets["suite_bundle"] = _build_round_suite_bundle(task_dir, args.round)
    return fields, local_assets


def _load_product_name(task_dir: Path) -> str:
    for file_name in ("image_task.json", "product_brief.json"):
        path = task_dir / file_name
        if not path.is_file():
            continue
        payload = read_json(path)
        product_name = str(payload.get("product_name", "") or "")
        if product_name:
            return product_name
    return task_dir.name


def _extract_person_from_field(fields: dict[str, Any], field_name: str) -> dict[str, str] | None:
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


def _resolve_notification_recipient(args: argparse.Namespace, client: FeishuBitableClient, task_dir: Path) -> tuple[dict[str, str], dict[str, Any]]:
    if args.receive_id:
        return {
            "field_name": "manual",
            "receive_id": args.receive_id,
            "name": "",
        }, {}

    record_id = _resolve_feishu_record_id(task_dir, args.record_id)
    record = client.get_record(record_id)
    fields = record.get("fields", {}) if isinstance(record, dict) else {}

    for field_name in (args.recipient_field, args.fallback_field):
        if not field_name:
            continue
        person = _extract_person_from_field(fields, field_name)
        if person:
            return person, record

    raise ValueError(
        f"no notification recipient found in record {record_id}; checked fields: {args.recipient_field}, {args.fallback_field}"
    )


def _stringify_feishu_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        for item in value:
            rendered = _stringify_feishu_field(item)
            if rendered:
                return rendered
        return ""
    if isinstance(value, dict):
        for key in ("text", "name", "value", "label", "en_name"):
            rendered = value.get(key)
            if rendered not in (None, ""):
                return _stringify_feishu_field(rendered)
        return ""
    return str(value).strip()


def _sync_local_image_review_state(manager: TaskManager, task_dir: Path, review_status: str, review_note: str, raw_record: dict[str, Any]) -> dict[str, Any]:
    manifest = manager.load_manifest(task_dir)
    current_status = manifest.get("status", "product_created")
    if current_status == "product_created":
        manager.advance_status(task_dir, "image_generation_pending", note="prepared for image review sync")
        current_status = "image_generation_pending"
    if current_status == "image_generation_pending":
        manager.advance_status(task_dir, "image_review_pending", note="entered image review stage")

    manifest = read_json(task_dir / "manifest.json")
    resulting_status = manifest.get("status", "")

    if review_status == "已通过" and resulting_status == "image_review_pending":
        manager.advance_status(task_dir, "image_review_passed", note=review_note or "Feishu image review passed")
    elif review_status == "已打回" and resulting_status == "image_review_pending":
        manager.advance_status(task_dir, "manual_check_pending", note=review_note or "Feishu image review rejected")

    manifest = read_json(task_dir / "manifest.json")
    resulting_status = manifest.get("status", "")
    if review_status == "已通过":
        local_review_status = "passed"
    elif review_status == "已打回":
        local_review_status = "rejected"
    else:
        local_review_status = "pending"

    manifest.setdefault("reviews", {})["image_review"] = local_review_status
    manifest["updated_at"] = utc_now_iso()
    manifest["reviews"].setdefault("review_logs", []).append(
        {
            "timestamp": utc_now_iso(),
            "source": "feishu_bitable",
            "status": review_status,
            "note": review_note,
        }
    )
    manifest.setdefault("events", []).append(
        {
            "timestamp": utc_now_iso(),
            "event": "feishu_image_review_synced",
            "detail": {
                "record_id": str(raw_record.get("record_id", raw_record.get("id", "")) or ""),
                "review_status": review_status,
                "note": review_note,
                "local_status": resulting_status,
            },
        }
    )
    write_json(task_dir / "manifest.json", manifest)
    package = ListingPackageBuilder().build(task_dir)
    return {
        "local_workflow_status": resulting_status,
        "local_review_status": local_review_status,
        "listing_package_review_status": package.get("review_status", {}),
    }


def _decide_feishu_task(item: dict[str, Any], mapper: FeishuImageTaskMapper) -> dict[str, Any]:
    summary = _summarize_feishu_record(mapper, item)
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

    if review_status == "已打回" and task_status == "待生成副图":
        return {
            **summary,
            "runnable": True,
            "next_action": "rerun_sub_generation",
            "reason": "副图审核已打回，重跑副图",
            "target_round": latest_round + 1,
        }
    if task_status == "异常":
        return {**summary, "runnable": False, "next_action": "blocked_exception", "reason": "任务状态为异常，需要人工处理"}
    if task_status == "待人工处理":
        return {**summary, "runnable": False, "next_action": "manual_attention", "reason": "任务已标记待人工处理"}
    if task_status == "已交付":
        return {**summary, "runnable": False, "next_action": "delivered", "reason": "任务已交付，无需再次执行"}
    if task_status == "已通过":
        return {**summary, "runnable": False, "next_action": "approved", "reason": "任务已通过，等待交付或后续人工动作"}
    if task_status == "生图中":
        return {**summary, "runnable": False, "next_action": "generation_in_progress", "reason": "任务正在生图中"}
    if task_status == "待审核主图":
        return {**summary, "runnable": False, "next_action": "awaiting_main_review", "reason": "主图已生成，等待审核"}
    if task_status == "待审核副图":
        return {**summary, "runnable": False, "next_action": "awaiting_sub_review", "reason": "副图已生成，等待审核"}
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


def main() -> None:
    bootstrap_runtime_environment()
    parser = build_parser()
    args = parser.parse_args()
    manager = TaskManager(Path("runtime/tasks"))
    image_builder = ImageWorkflowBuilder()

    if args.command == "init-task":
        task_dir = manager.init_task(args.task_id, args.product_id, args.market, args.shop_id, args.category)
        print(task_dir)
        return

    if args.command == "import-feishu-image-task":
        mapper = FeishuImageTaskMapper()
        result = mapper.import_record(Path(args.record), Path(args.tasks_root))
        manager.bootstrap_from_product_brief(Path(result.task_dir))
        image_builder.build_standardized_task(Path(result.task_dir))
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return

    if args.command == "feishu-list-image-tasks":
        client = FeishuBitableClient.from_env()
        mapper = FeishuImageTaskMapper()
        payload = client.list_records(page_size=args.page_size, page_token=args.page_token, view_id=args.view_id)
        result = {
            "has_more": payload["has_more"],
            "page_token": payload["page_token"],
            "total": payload["total"],
            "items": [_summarize_feishu_record(mapper, item) for item in payload.get("items", [])],
        }
        if args.raw:
            result["raw_items"] = payload.get("items", [])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "feishu-list-runnable-image-tasks":
        client = FeishuBitableClient.from_env()
        mapper = FeishuImageTaskMapper()
        items = [_decide_feishu_task(item, mapper) for item in _iter_feishu_records(client, page_size=args.page_size, view_id=args.view_id)]
        if not args.include_non_runnable:
            items = [item for item in items if item.get("runnable")]
        result = {
            "total": len(items),
            "items": items,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "feishu-evaluate-image-task":
        client = FeishuBitableClient.from_env()
        mapper = FeishuImageTaskMapper()
        record = _find_feishu_record(client, task_id=args.task_id, record_id=args.record_id, view_id=args.view_id)
        decision = _decide_feishu_task(record, mapper)
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return

    if args.command == "import-feishu-image-task-live":
        client = FeishuBitableClient.from_env()
        mapper = FeishuImageTaskMapper()
        record = client.get_record(args.record_id)
        result = mapper.import_record_data(
            record.get("fields", {}),
            Path(args.tasks_root),
            source_payload=record,
            source="feishu_bitable",
        )
        task_dir = Path(result.task_dir)
        normalized_with_downloads = _materialize_feishu_attachments(client, mapper, task_dir, record.get("fields", {}))
        write_json(task_dir / "intake" / "feishu_record.json", normalized_with_downloads)
        write_json(task_dir / "intake" / "feishu_record_raw.json", record)
        manager.bootstrap_from_product_brief(task_dir)
        image_builder.build_standardized_task(task_dir)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return

    task_dir = Path(args.task_dir) if hasattr(args, "task_dir") else Path("runtime/tasks")

    if args.command == "backfill-feishu-image-task":
        client = FeishuBitableClient.from_env()
        record_id = _resolve_feishu_record_id(task_dir, args.record_id)
        fields, local_assets = _build_feishu_backfill_payload(args, task_dir)
        payload_path = task_dir / "review" / f"round_{args.round:02d}_feishu_backfill_payload.json"
        payload_snapshot = {
            "record_id": record_id,
            "task_id": task_dir.name,
            "round": args.round,
            "fields": fields,
            "local_assets": {key: str(value) for key, value in local_assets.items()},
            "status_only": args.status_only,
        }
        write_json(payload_path, payload_snapshot)

        uploaded_fields: dict[str, Any] = {}
        if not args.status_only:
            uploaded_fields = {
                "主图预览": [client.upload_media(Path(local_assets["main_preview"]), parent_type="bitable_file")],
                "副图拼图预览": [client.upload_media(Path(local_assets["sub_contact_sheet"]), parent_type="bitable_file")],
            }
            if "suite_bundle" in local_assets:
                uploaded_fields["套图结果"] = [client.upload_media(Path(local_assets["suite_bundle"]), parent_type="bitable_file")]
            fields.update(uploaded_fields)
            payload_snapshot["fields"] = fields
            payload_snapshot["uploaded_fields"] = uploaded_fields
            write_json(payload_path, payload_snapshot)

        updated_record = client.update_record(record_id, fields)
        result = {
            "ok": True,
            "record_id": record_id,
            "task_id": task_dir.name,
            "round": args.round,
            "status_only": args.status_only,
            "updated_fields": list(fields.keys()),
            "uploaded_fields": uploaded_fields,
            "payload_path": str(payload_path),
            "record": updated_record,
        }
        write_json(task_dir / "review" / f"round_{args.round:02d}_feishu_backfill_result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "notify-feishu-image-review":
        client = FeishuBitableClient.from_env()
        notifier = FeishuNotifier.from_env()
        recipient, record = _resolve_notification_recipient(args, client, task_dir)
        payload = notifier.build_image_review_payload(
            task_id=task_dir.name,
            product_name=_load_product_name(task_dir),
            round_number=args.round,
            task_dir=task_dir,
            bundle_link=args.bundle_link,
            review_status=args.review_status,
            workflow_status=args.task_status,
            table_link=args.table_link,
            receiver_open_id=recipient["receive_id"],
            receiver_name=recipient["name"],
        )
        result = notifier.notify_image_review(
            receive_id=recipient["receive_id"],
            receive_id_type=args.receive_id_type,
            payload=payload,
        )
        result["record_id"] = _resolve_feishu_record_id(task_dir, args.record_id) if record else ""
        result["recipient"] = recipient
        result_path = task_dir / "review" / f"round_{args.round:02d}_feishu_notification_result.json"
        write_json(result_path, result)
        print(json.dumps({**result, "result_path": str(result_path)}, ensure_ascii=False, indent=2))
        return

    if args.command == "notify-feishu-image-delivery":
        client = FeishuBitableClient.from_env()
        notifier = FeishuNotifier.from_env()
        recipient, record = _resolve_notification_recipient(args, client, task_dir)
        bundle_path = _build_round_suite_bundle(task_dir, args.round)
        payload = notifier.build_image_delivery_payload(
            task_id=task_dir.name,
            product_name=_load_product_name(task_dir),
            round_number=args.round,
            task_dir=task_dir,
            bundle_link=args.bundle_link,
            bundle_path=str(bundle_path),
            delivery_status=args.delivery_status,
            workflow_status=args.workflow_status,
            receiver_open_id=recipient["receive_id"],
            receiver_name=recipient["name"],
            include_images=args.include_images,
            include_previews=not args.no_preview,
        )
        result = notifier.notify_image_delivery(
            receive_id=recipient["receive_id"],
            receive_id_type=args.receive_id_type,
            payload=payload,
        )
        result["record_id"] = _resolve_feishu_record_id(task_dir, args.record_id) if record else ""
        result["recipient"] = recipient
        result_path = task_dir / "review" / f"round_{args.round:02d}_feishu_delivery_result.json"
        write_json(result_path, result)
        print(json.dumps({**result, "result_path": str(result_path)}, ensure_ascii=False, indent=2))
        return

    if args.command == "sync-feishu-image-review":
        client = FeishuBitableClient.from_env()
        mapper = FeishuImageTaskMapper()
        record_id = _resolve_feishu_record_id(task_dir, args.record_id)
        record = client.get_record(record_id)
        normalized_record = mapper.normalize_record(record.get("fields", {}))
        write_json(task_dir / "intake" / "feishu_record.json", normalized_record)
        write_json(task_dir / "intake" / "feishu_record_raw.json", record)

        review_status = _stringify_feishu_field(record.get("fields", {}).get("图片审核状态")) or _DEFAULT_IMAGE_REVIEW_STATUS
        review_note = _stringify_feishu_field(record.get("fields", {}).get("审核意见"))
        task_status = _stringify_feishu_field(record.get("fields", {}).get("图片任务状态")) or _DEFAULT_IMAGE_TASK_STATUS
        sync_result = _sync_local_image_review_state(manager, task_dir, review_status, review_note, record)
        result = {
            "ok": True,
            "task_id": task_dir.name,
            "record_id": record_id,
            "feishu_task_status": task_status,
            "feishu_review_status": review_status,
            "review_note": review_note,
            **sync_result,
        }
        result_path = task_dir / "review" / "feishu_image_review_sync_result.json"
        write_json(result_path, result)
        print(json.dumps({**result, "result_path": str(result_path)}, ensure_ascii=False, indent=2))
        return

    if args.command == "process-feishu-review-message":
        processor = FeishuMessageReviewProcessor(Path(args.tasks_root))
        event_payload = read_json(Path(args.event_file))
        result = processor.process_event(event_payload)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "serve-feishu-callback":
        FeishuCallbackServer(Path(args.tasks_root), host=args.host, port=args.port, callback_path=args.callback_path, health_path=args.health_path).serve()
        return

    if args.command == "run-feishu-long-connection":
        FeishuLongConnectionReceiver.from_env(Path(args.tasks_root), log_level=args.log_level).serve()
        return

    if args.command == "run-feishu-image-worker":
        from .worker import LocalFeishuImageWorker

        worker = LocalFeishuImageWorker(Path(args.tasks_root), task_id_filter=args.task_id, sync_bitable_record=args.sync_bitable)
        if args.once:
            payload = worker.run_once()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        worker.run_forever(poll_interval_seconds=args.poll_interval)
        return

    if args.command == "build-image-task":
        payload = image_builder.build_standardized_task(task_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "build-oc-input":
        payload = image_builder.build_oc_input(
            task_dir,
            round_number=args.round,
            rework_reason=args.rework_reason,
            rework_scope=args.rework_scope,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "build-oc-output":
        oc_input = read_json(Path(args.oc_input))
        output_path = task_dir / "prompts" / f"round_{args.round:02d}_oc_output.json"
        payload = OpenClawExecutor(task_dir).write_image_prompts(oc_input, output_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "build-seedream-jobs":
        payload = SeedreamJobPlanner().build_jobs(task_dir, Path(args.oc_output), round_number=args.round)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "build-previews":
        payload = PreviewBuilder().build_round_previews(task_dir, args.round)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "sync-image-assets":
        payload = ImageAssetsBuilder().sync_round(task_dir, args.round)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "run-seedream-jobs":
        payload = SeedreamExecutor.from_env().run_jobs(task_dir, Path(args.jobs_file))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "show-task":
        payload = {
            "manifest": manager.load_manifest(task_dir),
            "listing_package": manager.load_listing_package(task_dir),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "advance":
        manager.advance_status(task_dir, args.to, note=args.note)
        print(f"advanced to {args.to}")
        return

    if args.command == "build-package":
        payload = ListingPackageBuilder().build(task_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.command == "preflight":
        issues = run_preflight(task_dir)
        if issues:
            print(json.dumps({"ok": False, "issues": issues}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(json.dumps({"ok": True, "issues": []}, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)







