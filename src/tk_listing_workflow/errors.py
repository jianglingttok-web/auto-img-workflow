from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class StructuredError:
    status: str = "failed"
    stage: str = ""
    step: str = ""
    error_code: str = ""
    error_message: str = ""
    screenshot_path: str = ""
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
