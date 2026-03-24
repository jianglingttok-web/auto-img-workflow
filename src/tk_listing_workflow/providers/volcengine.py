from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..executors.seedream import DEFAULT_BASE_URL, DEFAULT_SIZE, SeedreamConfig, SeedreamExecutor
from .base import ImageModelSpec


@dataclass(slots=True)
class VolcengineProviderConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    size: str = DEFAULT_SIZE
    response_format: str = "b64_json"
    stream: bool = False
    watermark: bool = False
    http_retries: int = 3
    retry_delay_seconds: float = 2.0


class VolcengineImageProvider:
    def __init__(self, config: VolcengineProviderConfig) -> None:
        if not config.api_key:
            raise ValueError("VOLCANO_ENGINE_API_KEY / ARK_API_KEY is required")
        self.config = config

    @classmethod
    def from_env(cls) -> "VolcengineImageProvider":
        api_key = os.environ.get("VOLCANO_ENGINE_API_KEY", "").strip() or os.environ.get("ARK_API_KEY", "").strip()
        return cls(
            VolcengineProviderConfig(
                api_key=api_key,
                base_url=os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
                size=os.environ.get("SEEDREAM_SIZE", DEFAULT_SIZE).strip() or DEFAULT_SIZE,
                response_format=os.environ.get("SEEDREAM_RESPONSE_FORMAT", "b64_json").strip() or "b64_json",
                stream=os.environ.get("SEEDREAM_STREAM", "false").lower() == "true",
                watermark=os.environ.get("SEEDREAM_WATERMARK", "false").lower() == "true",
                http_retries=max(int(os.environ.get("SEEDREAM_HTTP_RETRIES", "3") or "3"), 1),
                retry_delay_seconds=max(float(os.environ.get("SEEDREAM_RETRY_DELAY_SECONDS", "2") or "2"), 0.0),
            )
        )

    def create_executor(self, model: ImageModelSpec) -> SeedreamExecutor:
        return SeedreamExecutor(
            SeedreamConfig(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                model=model.model_id,
                size=self.config.size,
                response_format=self.config.response_format,
                stream=self.config.stream,
                watermark=self.config.watermark,
                http_retries=self.config.http_retries,
                retry_delay_seconds=self.config.retry_delay_seconds,
            )
        )

    def run_jobs(self, task_dir: Path, jobs_file: Path, model: ImageModelSpec) -> dict:
        return self.create_executor(model).run_jobs(task_dir, jobs_file)
