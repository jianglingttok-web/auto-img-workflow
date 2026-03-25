from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..ai.image_workflow import ImageWorkflowBuilder, SeedreamJobPlanner
from ..config import PROJECT_ROOT, bootstrap_runtime_environment
from ..executors.openclaw import OpenClawExecutor
from ..media import ImageAssetsBuilder, PreviewBuilder
from ..storage import write_json
from ..providers import ImageModelSpec, PromptEngineSpec, VolcengineImageProvider
from .cleaner import ExpiredTaskCleaner
from .usage_service import UsageService

DEFAULT_SITES = ["TH", "ID", "MY", "PH", "VN", "SG"]
FISSION_TYPES = {
    "same_product_fission": {"label": "同一产品裂变", "experimental": False, "use_case": "image-to-image-fission"},
    "same_style_product_swap": {"label": "同风格换产品", "experimental": True, "use_case": "same-style-product-swap"},
}


@dataclass(slots=True)
class FactorySettings:
    sites: list[str]
    groups: dict[str, str]
    prompt_engine: PromptEngineSpec
    image_models: dict[str, ImageModelSpec]
    max_concurrent: int
    cleanup_ttl_hours: int
    cleanup_interval_hours: int
    feishu_enabled: bool
    feishu_webhook: str


class FactoryTaskService:
    def __init__(self) -> None:
        bootstrap_runtime_environment()
        self.project_root = PROJECT_ROOT
        self.runtime_root = self.project_root / "runtime" / "web-tasks"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_root / "factory.db"
        self.settings = self._load_settings()
        self.usage = UsageService(self.db_path)
        self.cleaner = ExpiredTaskCleaner(self.db_path, self.runtime_root)
        self._apply_prompt_env()
        self._init_db()

    def list_options(self) -> dict[str, Any]:
        return {
            "groups": list(self.settings.groups.keys()),
            "sites": self.settings.sites,
            "fission_types": [
                {"value": key, "label": spec["label"], "experimental": bool(spec["experimental"])}
                for key, spec in FISSION_TYPES.items()
            ],
            "models": [
                {
                    "model_id": model.model_id,
                    "label": f"{model.name} - ${model.price_per_image:.2f}/张",
                    "price_per_image": model.price_per_image,
                }
                for model in self.settings.image_models.values()
            ],
        }

    def create_task(
        self,
        *,
        group_name: str,
        group_password: str,
        operator_name: str,
        site: str,
        fission_type: str,
        model_id: str,
        count: int,
        notes: str,
        product_image_bytes: bytes,
        product_image_name: str,
        reference_image_bytes: bytes,
        reference_image_name: str,
    ) -> dict[str, Any]:
        group_name = str(group_name or "").strip()
        group_password = str(group_password or "").strip()
        operator_name = str(operator_name or "").strip()
        site = str(site or "").strip().upper()
        fission_type = str(fission_type or "").strip()
        model_id = str(model_id or "").strip()
        notes = str(notes or "").strip()
        count = int(count)

        if group_name not in self.settings.groups:
            raise ValueError("invalid group_name")
        if group_password != self.settings.groups[group_name]:
            raise ValueError("invalid group_password")
        if site not in self.settings.sites:
            raise ValueError("invalid site")
        if fission_type not in FISSION_TYPES:
            raise ValueError("invalid fission_type")
        if model_id not in self.settings.image_models:
            raise ValueError("invalid model_id")
        if count < 1 or count > 10:
            raise ValueError("count must be between 1 and 10")
        if not product_image_bytes:
            raise ValueError("product_image is required")
        if not reference_image_bytes:
            raise ValueError("reference_image is required")

        model = self.settings.image_models[model_id]
        fingerprint = self._build_request_fingerprint(
            group_name=group_name,
            operator_name=operator_name,
            site=site,
            fission_type=fission_type,
            model_id=model_id,
            count=count,
            notes=notes,
            product_image_bytes=product_image_bytes,
            reference_image_bytes=reference_image_bytes,
        )
        existing = self._find_existing_by_fingerprint(fingerprint)
        if existing:
            return {
                "task_id": existing["task_id"],
                "status": existing["status"],
                "position_in_queue": self._queue_position(existing["task_id"]),
                "deduplicated": True,
                "experimental": self._is_experimental(existing["fission_type"]),
            }

        task_id = self._new_task_id()
        task_dir = self.runtime_root / task_id
        input_dir = task_dir / "input"
        for directory in (input_dir, task_dir / "prompts", task_dir / "media", task_dir / "download", task_dir / "logs"):
            directory.mkdir(parents=True, exist_ok=True)

        product_path = input_dir / self._normalized_filename("product", product_image_name)
        reference_path = input_dir / self._normalized_filename("reference", reference_image_name)
        product_path.write_bytes(product_image_bytes)
        reference_path.write_bytes(reference_image_bytes)

        image_task = self._build_image_task_payload(
            task_id=task_id,
            site=site,
            group_name=group_name,
            operator_name=operator_name,
            fission_type=fission_type,
            count=count,
            notes=notes,
            product_path=product_path,
            reference_path=reference_path,
        )
        write_json(task_dir / "image_task.json", image_task)
        write_json(task_dir / "request.json", {
            "task_id": task_id,
            "group_name": group_name,
            "operator_name": operator_name,
            "site": site,
            "fission_type": fission_type,
            "model_id": model_id,
            "count": count,
            "notes": notes,
            "request_fingerprint": fingerprint,
        })
        write_json(task_dir / "image_assets.json", {
            "product_id": task_id,
            "main_images": [],
            "sub_images": [],
            "detail_images": [],
            "a_plus_images": [],
            "generation_meta": {},
        })
        write_json(task_dir / "manifest.json", {
            "task_id": task_id,
            "status": "pending",
            "created_at": time.time(),
            "updated_at": time.time(),
            "group_name": group_name,
            "operator_name": operator_name,
            "site": site,
            "fission_type": fission_type,
            "model_id": model_id,
            "count": count,
        })

        now = time.time()
        expires_at = now + self.settings.cleanup_ttl_hours * 3600
        estimated_cost = round(model.price_per_image * count, 6)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, request_fingerprint, group_name, operator_name, site, fission_type,
                    provider, model_id, price_per_image, prompt_provider, prompt_model_id,
                    count, estimated_cost, actual_cost, status, product_image_path,
                    reference_image_path, result_zip_path, expires_at, notes, error_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, fingerprint, group_name, operator_name, site, fission_type,
                    model.provider, model.model_id, model.price_per_image,
                    self.settings.prompt_engine.provider, self.settings.prompt_engine.model_id,
                    count, estimated_cost, None, "pending",
                    str(product_path), str(reference_path), None, expires_at, notes, "", now, now,
                ),
            )
            conn.commit()
        return {
            "task_id": task_id,
            "status": "pending",
            "position_in_queue": self._queue_position(task_id),
            "deduplicated": False,
            "experimental": self._is_experimental(fission_type),
        }

    def claim_next_task(self) -> str | None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT task_id FROM tasks
                WHERE status = 'pending' AND expires_at > ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["task_id"])
            conn.execute(
                "UPDATE tasks SET status='running', updated_at=?, error_message='' WHERE task_id=? AND status='pending'",
                (now, task_id),
            )
            conn.commit()
            return task_id

    def process_task(self, task_id: str) -> dict[str, Any]:
        task = self._get_task_row(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        model = self.settings.image_models[str(task["model_id"])]
        task_dir = self.runtime_root / task_id
        try:
            builder = ImageWorkflowBuilder()
            oc_input = builder.build_oc_input(task_dir, round_number=1)
            oc_output_path = task_dir / "prompts" / "round_01_oc_output.json"
            oc_output = OpenClawExecutor(task_dir).write_image_prompts(oc_input, oc_output_path)
            SeedreamJobPlanner().build_jobs(task_dir, oc_output_path, round_number=1)
            jobs_file = task_dir / "prompts" / "round_01_seedream_jobs.json"
            if not jobs_file.exists():
                jobs_file = task_dir / "prompts" / "round_01_seedream_jobs_main_only.json"
            provider = self._build_image_provider(model.provider)
            seedream_result = provider.run_jobs(task_dir, jobs_file, model)
            preview_payload = PreviewBuilder().build_round_previews(task_dir, 1)
            image_assets = ImageAssetsBuilder().sync_round(task_dir, 1)
            zip_path = self._build_result_zip(task_id)
            image_paths = [*image_assets.get("main_images", []), *image_assets.get("sub_images", [])]
            actual_cost = round(len(image_paths) * model.price_per_image, 6)
            self._record_usage(task, image_paths=image_paths, model=model)
            self._update_task_success(task_id, zip_path=zip_path, actual_cost=actual_cost)
            self._write_manifest(task_dir, status="succeeded")
            self._notify(optional_text=f"任务完成：{task_id}，共生成 {len(image_paths)} 张，结果已可下载。")
            return {
                "ok": True,
                "task_id": task_id,
                "status": "succeeded",
                "zip_path": str(zip_path),
                "job_count": int(seedream_result.get("job_count", 0) or 0),
                "result_count": len(image_paths),
                "preview_payload": preview_payload,
                "generator": oc_output.get("generator", {}),
            }
        except Exception as exc:
            self._update_task_failure(task_id, str(exc))
            self._write_manifest(task_dir, status="failed", error_message=str(exc))
            self._notify(optional_text=f"任务失败：{task_id}，原因：{str(exc)}")
            raise

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        row = self._get_task_row(task_id)
        if row is None:
            raise ValueError("task not found")
        result_count = self._count_usage_images(task_id)
        download_url = f"/api/tasks/{task_id}/download" if row["status"] == "succeeded" and row["result_zip_path"] else None
        return {
            "task_id": task_id,
            "status": row["status"],
            "queue_position": self._queue_position(task_id),
            "site": row["site"],
            "fission_type": row["fission_type"],
            "group_name": row["group_name"],
            "operator_name": row["operator_name"] or "",
            "model_id": row["model_id"],
            "count": int(row["count"]),
            "estimated_cost": float(row["estimated_cost"] or 0),
            "actual_cost": float(row["actual_cost"]) if row["actual_cost"] is not None else None,
            "result_count": result_count,
            "download_url": download_url,
            "image_urls": self._result_image_urls(task_id),
            "experimental": self._is_experimental(str(row["fission_type"])),
            "error_message": row["error_message"] or "",
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def get_download_path(self, task_id: str) -> Path:
        row = self._get_task_row(task_id)
        if row is None:
            raise ValueError("task not found")
        zip_path = Path(str(row["result_zip_path"] or ""))
        if not zip_path.is_file():
            raise FileNotFoundError("result zip not found")
        return zip_path

    def get_task_file_path(self, task_id: str, relative_path: str) -> Path:
        task_dir = (self.runtime_root / task_id).resolve()
        if not task_dir.exists():
            raise ValueError("task not found")
        candidate = (task_dir / relative_path).resolve()
        if task_dir not in candidate.parents and candidate != task_dir:
            raise ValueError("invalid task file path")
        if not candidate.is_file():
            raise FileNotFoundError("task file not found")
        return candidate

    def stats_summary(self) -> dict[str, Any]:
        return self.usage.summary()

    def cleanup_expired(self) -> int:
        return self.cleaner.cleanup()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT UNIQUE NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    operator_name TEXT NOT NULL DEFAULT '',
                    site TEXT NOT NULL,
                    fission_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    price_per_image REAL NOT NULL,
                    prompt_provider TEXT NOT NULL,
                    prompt_model_id TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    estimated_cost REAL NOT NULL,
                    actual_cost REAL,
                    status TEXT NOT NULL,
                    product_image_path TEXT NOT NULL,
                    reference_image_path TEXT NOT NULL,
                    result_zip_path TEXT,
                    expires_at REAL NOT NULL,
                    notes TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    operator_name TEXT NOT NULL DEFAULT '',
                    prompt_provider TEXT NOT NULL,
                    prompt_model_id TEXT NOT NULL,
                    image_provider TEXT NOT NULL,
                    image_model_id TEXT NOT NULL,
                    price_estimated REAL NOT NULL,
                    price_actual REAL,
                    image_path TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks(request_fingerprint)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_task_id ON usage(task_id)")
            conn.commit()

    def _load_settings(self) -> FactorySettings:
        config_path = self.project_root / "config.yaml"
        if not config_path.exists():
            config_path = self.project_root / "config.example.yaml"
        payload = yaml.safe_load(self._resolve_placeholders(config_path.read_text(encoding="utf-8-sig"))) or {}
        web = payload.get("web", {}) if isinstance(payload, dict) else {}
        group_payload = payload.get("groups", {}) if isinstance(payload, dict) else {}
        groups = {
            str(key): str((value or {}).get("password", "") if isinstance(value, dict) else value or "")
            for key, value in group_payload.items()
        }
        prompt_payload = payload.get("prompt_engine", {}) if isinstance(payload, dict) else {}
        prompt_engine = PromptEngineSpec(
            provider=str(prompt_payload.get("provider", "volcengine") or "volcengine"),
            model_id=str(prompt_payload.get("model_id", os.environ.get("ARK_TEXT_MODEL", "doubao-seed-2-0-lite-260215")) or "doubao-seed-2-0-lite-260215"),
            temperature=float(prompt_payload.get("temperature", os.environ.get("ARK_TEXT_TEMPERATURE", "0.3")) or 0.3),
        )
        providers = payload.get("providers", {}) if isinstance(payload, dict) else {}
        image_models: dict[str, ImageModelSpec] = {}
        for provider_name, provider_cfg in providers.items():
            if not isinstance(provider_cfg, dict) or not provider_cfg.get("enabled", False):
                continue
            for item in provider_cfg.get("models", []) or []:
                model = ImageModelSpec(
                    provider=str(provider_name),
                    model_id=str(item.get("id", "") or ""),
                    name=str(item.get("name", item.get("id", "")) or ""),
                    price_per_image=float(item.get("price_per_image", 0) or 0),
                )
                if not model.model_id:
                    continue
                if model.model_id in image_models:
                    raise ValueError(f"duplicate model_id in providers config: {model.model_id}")
                image_models[model.model_id] = model
        feishu = payload.get("feishu", {}) if isinstance(payload, dict) else {}
        return FactorySettings(
            sites=[str(item).upper() for item in web.get("site_options", DEFAULT_SITES)],
            groups=groups,
            prompt_engine=prompt_engine,
            image_models=image_models,
            max_concurrent=max(int(web.get("max_concurrent", 4) or 4), 1),
            cleanup_ttl_hours=max(int(web.get("cleanup_ttl_hours", 48) or 48), 1),
            cleanup_interval_hours=max(int(web.get("cleanup_interval_hours", 24) or 24), 1),
            feishu_enabled=bool(feishu.get("enabled", False)),
            feishu_webhook=str(feishu.get("webhook", "") or ""),
        )

    def _apply_prompt_env(self) -> None:
        os.environ["ARK_TEXT_MODEL"] = self.settings.prompt_engine.model_id
        os.environ["ARK_TEXT_TEMPERATURE"] = str(self.settings.prompt_engine.temperature)

    def _resolve_placeholders(self, text: str) -> str:
        rendered = text
        for key, value in os.environ.items():
            rendered = rendered.replace(f"${{{key}}}", value)
        return rendered

    def _build_request_fingerprint(self, **payload: Any) -> str:
        product_hash = hashlib.sha256(payload.pop("product_image_bytes")).hexdigest()
        reference_hash = hashlib.sha256(payload.pop("reference_image_bytes")).hexdigest()
        canonical = json.dumps({**payload, "product_image_hash": product_hash, "reference_image_hash": reference_hash}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _find_existing_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT task_id, status, fission_type
                FROM tasks
                WHERE request_fingerprint = ? AND status IN ('pending', 'running', 'succeeded')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()
            return dict(row) if row is not None else None

    def _get_task_row(self, task_id: str) -> sqlite3.Row | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()

    def _queue_position(self, task_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT created_at, status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None or row["status"] != "pending":
                return 0
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='pending' AND created_at <= ?",
                (row["created_at"],),
            ).fetchone()[0]
            return max(int(count), 0)

    def _new_task_id(self) -> str:
        return f"wf{time.strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6]}"

    def _normalized_filename(self, prefix: str, original_name: str) -> str:
        suffix = Path(str(original_name or "")).suffix.lower() or ".png"
        return f"{prefix}{suffix}"

    def _build_image_task_payload(
        self,
        *,
        task_id: str,
        site: str,
        group_name: str,
        operator_name: str,
        fission_type: str,
        count: int,
        notes: str,
        product_path: Path,
        reference_path: Path,
    ) -> dict[str, Any]:
        variation_scope = "保持同一产品不变，延续参考图的构图、背景、光线和氛围。"
        if fission_type == "same_style_product_swap":
            variation_scope = "保持参考图风格不变，替换为新的产品主体，优先维持整体视觉语言一致。"
        return {
            "task_id": task_id,
            "product_id": task_id,
            "product_name": "",
            "shop_id": group_name,
            "target_market": site,
            "task_mode": "main_only",
            "main_image_count": count,
            "sub_image_count": 0,
            "task_type": fission_type,
            "category_hint": fission_type,
            "use_case": FISSION_TYPES[fission_type]["use_case"],
            "variation_scope": variation_scope,
            "operator_group": group_name,
            "submitter": operator_name,
            "style": [fission_type],
            "selling_points": [],
            "compliance_rules": [],
            "marketing_phrases": [],
            "numeric_claims": [],
            "competitor_links": [],
            "notes": notes,
            "reference_images": {
                "product_white_background": [str(product_path.resolve())],
                "usage_images": [],
                "style_reference_images": [str(reference_path.resolve())],
            },
        }

    def _build_image_provider(self, provider_name: str):
        if provider_name == "volcengine":
            return VolcengineImageProvider.from_env()
        raise ValueError(f"unsupported provider: {provider_name}")

    def _build_result_zip(self, task_id: str) -> Path:
        task_dir = self.runtime_root / task_id
        zip_path = task_dir / "download" / "result.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            media_dir = task_dir / "media"
            for path in sorted(media_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(task_dir))
            for rel in ("prompts/round_01_oc_input.json", "prompts/round_01_oc_output.json"):
                path = task_dir / rel
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(task_dir))
        return zip_path

    def _record_usage(self, task: sqlite3.Row, *, image_paths: list[str], model: ImageModelSpec) -> None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            for image_path in image_paths:
                conn.execute(
                    """
                    INSERT INTO usage (
                        task_id, group_name, operator_name, prompt_provider, prompt_model_id,
                        image_provider, image_model_id, price_estimated, price_actual, image_path, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["task_id"], task["group_name"], task["operator_name"],
                        task["prompt_provider"], task["prompt_model_id"],
                        model.provider, model.model_id, model.price_per_image, model.price_per_image,
                        image_path, now,
                    ),
                )
            conn.commit()

    def _update_task_success(self, task_id: str, *, zip_path: Path, actual_cost: float) -> None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='succeeded', actual_cost=?, result_zip_path=?, updated_at=?, error_message='' WHERE task_id=?",
                (actual_cost, str(zip_path), now, task_id),
            )
            conn.commit()

    def _update_task_failure(self, task_id: str, error_message: str) -> None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='failed', updated_at=?, error_message=? WHERE task_id=?",
                (now, error_message, task_id),
            )
            conn.commit()

    def _count_usage_images(self, task_id: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM usage WHERE task_id = ?", (task_id,)).fetchone()
            return int(row[0] or 0)

    def _result_image_urls(self, task_id: str) -> list[str]:
        task_dir = self.runtime_root / task_id
        media_dir = task_dir / "media"
        if not media_dir.exists():
            return []
        urls: list[str] = []
        for path in sorted(media_dir.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                continue
            if 'preview' in path.parts:
                continue
            relative = path.relative_to(task_dir).as_posix()
            urls.append(f"/api/tasks/{task_id}/files/{relative}")
        return urls

    def _is_experimental(self, fission_type: str) -> bool:
        return bool(FISSION_TYPES.get(fission_type, {}).get("experimental", False))

    def _notify(self, optional_text: str) -> None:
        if not self.settings.feishu_enabled or not self.settings.feishu_webhook or not optional_text:
            return
        body = json.dumps({"msg_type": "text", "content": {"text": optional_text}}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.settings.feishu_webhook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()

    def _write_manifest(self, task_dir: Path, *, status: str, error_message: str = "") -> None:
        write_json(task_dir / "manifest.json", {
            "task_id": task_dir.name,
            "status": status,
            "updated_at": time.time(),
            "error_message": error_message,
        })
