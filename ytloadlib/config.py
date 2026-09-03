from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

from .models import AppConfig


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ytload" / "config.json"
    return Path(os.environ.get("HOME", str(Path.home()))) / ".config" / "ytload" / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    explicit = path is not None
    path = path or default_config_path()
    if not path.exists():
        if explicit:
            raise ValueError(f'Configuration file does not exist: {path}')
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError('Configuration must be a JSON object.')
    allowed = {f.name for f in fields(AppConfig)}
    values = {k: v for k, v in raw.items() if k in allowed}
    config = AppConfig(**values)
    from .validation import validate_config
    validate_config(config)
    return config
