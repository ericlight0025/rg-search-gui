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
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings_file(payload: dict[str, object], settings_path: Path | None = None) -> str | None:
    """寫入暫存檔後再替換，寫到一半失敗不會弄壞原設定；失敗時回傳原因讓畫面提示。"""
    try:
        settings_path = settings_path or _get_settings_path()
        temporary = settings_path.with_name(settings_path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, settings_path)
    except OSError as exc:
        # 只顯示原因，不帶完整路徑，避免畫面露出使用者名稱。
        return f"無法寫入設定檔：{exc.strerror or type(exc).__name__}"
    return None
