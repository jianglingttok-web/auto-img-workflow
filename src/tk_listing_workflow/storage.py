from __future__ import annotations

import json
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any


def ensure_task_dirs(task_dir: Path) -> None:
    for name in ["intake", "media", "copy", "review", "publish", "logs", "screenshots", "prompts"]:
        (task_dir / name).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data
    if is_dataclass(data):
        payload = data.to_dict()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)
