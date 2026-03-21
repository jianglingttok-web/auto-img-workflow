from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .ai.image_workflow import ImageWorkflowBuilder, SeedreamJobPlanner
from .data.build_listing_package import ListingPackageBuilder
from .executors.openclaw import OpenClawExecutor
from .executors.seedream import SeedreamExecutor
from .intake.feishu_mapper import FeishuImageTaskMapper
from .media.preview_builder import PreviewBuilder
from .preflight import run_preflight
from .storage import read_json
from .task_manager import TaskManager


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


def main() -> None:
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

    task_dir = Path(args.task_dir)

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
    main()
