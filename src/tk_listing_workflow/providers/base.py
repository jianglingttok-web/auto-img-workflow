from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class PromptEngineSpec:
    provider: str
    model_id: str
    temperature: float = 0.3


@dataclass(slots=True)
class ImageModelSpec:
    provider: str
    model_id: str
    name: str
    price_per_image: float

    @property
    def model_key(self) -> str:
        return self.model_id


class ImageProvider(Protocol):
    def run_jobs(self, task_dir: Path, jobs_file: Path, model: ImageModelSpec) -> dict:
        ...
