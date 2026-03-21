from __future__ import annotations

from pathlib import Path

from ..storage import read_json


def validate_json_file(path: Path) -> list[str]:
    issues: list[str] = []
    if not path.exists():
        issues.append(f"file not found: {path}")
        return issues
    try:
        read_json(path)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"invalid json: {path} ({exc})")
    return issues
