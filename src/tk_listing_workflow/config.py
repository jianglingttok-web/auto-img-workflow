from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_BOOTSTRAPPED = False

_CONFIG_ENV_MAPPING: dict[tuple[str, str], str] = {
    ("ark", "api_key"): "ARK_API_KEY",
    ("ark", "base_url"): "ARK_BASE_URL",
    ("ark", "seedream_model"): "SEEDREAM_MODEL",
    ("ark", "text_model"): "ARK_TEXT_MODEL",
    ("ark", "text_temperature"): "ARK_TEXT_TEMPERATURE",
    ("ark", "text_http_retries"): "ARK_TEXT_HTTP_RETRIES",
    ("ark", "text_retry_delay_seconds"): "ARK_TEXT_RETRY_DELAY_SECONDS",
    ("ark", "text_timeout_seconds"): "ARK_TEXT_TIMEOUT_SECONDS",
    ("ark", "image_size"): "SEEDREAM_SIZE",
    ("ark", "response_format"): "SEEDREAM_RESPONSE_FORMAT",
    ("ark", "stream"): "SEEDREAM_STREAM",
    ("ark", "watermark"): "SEEDREAM_WATERMARK",
    ("ziniu", "base_url"): "ZINIU_BASE_URL",
    ("ziniu", "api_key"): "ZINIU_API_KEY",
    ("ziniu", "browser_profile_id"): "ZINIU_BROWSER_PROFILE_ID",
    ("feishu", "base_url"): "FEISHU_BASE_URL",
    ("feishu", "webhook_url"): "FEISHU_WEBHOOK_URL",
    ("feishu", "app_id"): "FEISHU_APP_ID",
    ("feishu", "app_secret"): "FEISHU_APP_SECRET",
    ("feishu", "image_task_app_token"): "FEISHU_IMAGE_TASK_APP_TOKEN",
    ("feishu", "image_task_table_id"): "FEISHU_IMAGE_TASK_TABLE_ID",
    ("feishu", "image_task_view_id"): "FEISHU_IMAGE_TASK_VIEW_ID",
}


@dataclass(slots=True)
class RuntimeEnvironment:
    project_root: Path
    dotenv_path: Path
    config_path: Path


def bootstrap_runtime_environment() -> RuntimeEnvironment:
    global _BOOTSTRAPPED

    project_root = PROJECT_ROOT
    dotenv_path = project_root / ".env"
    config_path = project_root / "config.yaml"

    os.chdir(project_root)

    if not _BOOTSTRAPPED:
        if dotenv_path.exists():
            _load_dotenv(dotenv_path)
        if config_path.exists():
            _load_config_yaml(config_path)
        _BOOTSTRAPPED = True

    return RuntimeEnvironment(
        project_root=project_root,
        dotenv_path=dotenv_path,
        config_path=config_path,
    )


def _load_dotenv(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        os.environ[key] = _strip_quotes(value)


def _load_config_yaml(path: Path) -> None:
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(payload, dict):
        return

    for key_path, env_name in _CONFIG_ENV_MAPPING.items():
        value = _get_nested(payload, *key_path)
        normalized = _normalize_config_value(value)
        if normalized is None:
            continue
        os.environ[env_name] = normalized


def _get_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _normalize_config_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"

    text = _resolve_placeholders(str(value).strip())
    return text or None


def _resolve_placeholders(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return _PLACEHOLDER_PATTERN.sub(replace, text)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
