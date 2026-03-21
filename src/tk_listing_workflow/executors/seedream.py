from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..storage import read_json, write_json


DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedream-4-5-251128"
DEFAULT_SIZE = "2K"


@dataclass(slots=True)
class SeedreamConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    size: str = DEFAULT_SIZE
    response_format: str = "url"
    stream: bool = False
    watermark: bool = False
    sequential_image_generation: str = "disabled"


class SeedreamExecutor:
    def __init__(self, config: SeedreamConfig) -> None:
        if not config.api_key:
            raise ValueError("Seedream API key is required")
        self.config = config

    @classmethod
    def from_env(cls) -> "SeedreamExecutor":
        config = SeedreamConfig(
            api_key=os.environ.get("ARK_API_KEY", ""),
            base_url=os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("SEEDREAM_MODEL", DEFAULT_MODEL),
            size=os.environ.get("SEEDREAM_SIZE", DEFAULT_SIZE),
            response_format=os.environ.get("SEEDREAM_RESPONSE_FORMAT", "url"),
            stream=os.environ.get("SEEDREAM_STREAM", "false").lower() == "true",
            watermark=os.environ.get("SEEDREAM_WATERMARK", "false").lower() == "true",
        )
        return cls(config)

    def run_jobs(self, task_dir: Path, jobs_file: Path) -> dict[str, Any]:
        payload = read_json(jobs_file)
        task_id = payload["task_id"]
        round_number = int(payload["round"])
        jobs = payload.get("jobs", [])
        results: list[dict[str, Any]] = []

        for job in jobs:
            api_payload = self._build_api_payload(task_dir, job)
            response = self._call_images_api(api_payload)
            saved_files = self._save_response_images(task_dir, round_number, job, response)
            results.append(
                {
                    "slot": job["slot"],
                    "image_type": job["image_type"],
                    "request": api_payload,
                    "response": response,
                    "saved_files": saved_files,
                }
            )

        result_payload = {
            "task_id": task_id,
            "round": round_number,
            "job_count": len(jobs),
            "results": results,
        }
        write_json(task_dir / "media" / f"round_{round_number:02d}_seedream_results.json", result_payload)
        return result_payload

    def _build_api_payload(self, task_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
        references = self._normalize_references(task_dir, job.get("reference_images", {}), job["image_type"])
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": job["prompt"],
            "size": self.config.size,
            "response_format": self.config.response_format,
            "stream": self.config.stream,
            "watermark": self.config.watermark,
            "sequential_image_generation": self.config.sequential_image_generation,
        }
        if references:
            payload["image"] = references if len(references) > 1 else references[0]
        return payload

    def _normalize_references(self, task_dir: Path, reference_images: dict[str, Any], image_type: str) -> list[str]:
        candidates: list[str] = []
        for key in ("product_white_background", "usage_images", "style_reference_images"):
            values = reference_images.get(key, [])
            if image_type == "main" and key == "style_reference_images":
                continue
            for value in values:
                normalized = self._coerce_reference(task_dir, value)
                if normalized:
                    candidates.append(normalized)
        return candidates

    def _coerce_reference(self, task_dir: Path, value: Any) -> str:
        text = str(value).strip()
        if not text:
            return ""
        if self._is_url(text):
            return text
        path = Path(text)
        if not path.is_absolute():
            path = task_dir / "intake" / text if not text.startswith("runtime/") else Path(text)
        if not path.exists():
            raise FileNotFoundError(f"reference image not found: {value}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _call_images_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=f"{self.config.base_url.rstrip('/')}/images/generations",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Seedream API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Seedream API network error: {exc}") from exc

    def _save_response_images(self, task_dir: Path, round_number: int, job: dict[str, Any], response: dict[str, Any]) -> list[str]:
        items = response.get("data", [])
        output_dir = task_dir / "media" / f"round_{round_number:02d}" / ("main" if job["image_type"] == "main" else "sub")
        output_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []

        for index, item in enumerate(items, start=1):
            slot = job["slot"]
            if "b64_json" in item and item["b64_json"]:
                path = output_dir / f"{slot}_{index:02d}.png"
                path.write_bytes(base64.b64decode(item["b64_json"]))
                saved.append(str(path))
                continue
            if "url" in item and item["url"]:
                ext = self._guess_extension(item["url"])
                path = output_dir / f"{slot}_{index:02d}{ext}"
                self._download_file(item["url"], path)
                saved.append(str(path))
                continue
            raise RuntimeError(f"unsupported Seedream response item for {slot}: {item}")
        return saved

    def _download_file(self, url: str, path: Path) -> None:
        request = Request(url, method="GET")
        with urlopen(request, timeout=300) as response:
            path.write_bytes(response.read())

    def _guess_extension(self, url: str) -> str:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix.lower()
        return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".png"

    def _is_url(self, value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"}
