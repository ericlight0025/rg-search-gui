"""Persistence helpers for RG Search GUI settings."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _get_settings_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata) / "rg-search-gui"
    else:
        base = Path.home() / ".rg-search-gui"
    base.mkdir(parents=True, exist_ok=True)
    return base / "settings.json"


def _load_settings_file() -> dict[str, object]:
    settings_path = _get_settings_path()
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings_file(payload: dict[str, object]) -> None:
    settings_path = _get_settings_path()
    try:
        settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
