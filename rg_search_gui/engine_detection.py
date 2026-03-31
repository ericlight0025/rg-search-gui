"""Engine detection helpers for RG Search GUI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from rg_search_gui.models import EngineInfo


def _detect_engine_info() -> EngineInfo:
    # Prefer bundled rg in resources (development or PyInstaller _MEIPASS),
    # otherwise fall back to PATH lookup for rg or grep.
    try:
        import sys

        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", "."))
        else:
            base = Path(__file__).resolve().parent
        candidate = base / "resources" / ("rg.exe" if os.name == "nt" else "rg")
        if candidate.exists():
            return EngineInfo(executable=str(candidate), label="rg", version=_detect_engine_version(str(candidate)))
    except Exception:
        pass

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        winget_link_candidates: list[Path] = []
        if local_app_data:
            winget_link_candidates.append(Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "rg.exe")
        winget_link_candidates.append(Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "rg.exe")
        for candidate in winget_link_candidates:
            if candidate.exists():
                return EngineInfo(executable=str(candidate), label="rg", version=_detect_engine_version(str(candidate)))

    if shutil.which("rg"):
        executable = shutil.which("rg")
        return EngineInfo(executable=executable, label="rg", version=_detect_engine_version(executable or "rg"))
    if shutil.which("grep"):
        executable = shutil.which("grep")
        return EngineInfo(executable=executable, label="grep", version=_detect_engine_version(executable or "grep"))
    return EngineInfo(executable=None, label="unavailable")


def _detect_engine_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except Exception:
        return ""
    first_line = (result.stdout or result.stderr).splitlines()
    if not first_line:
        return ""
    version_match = re.search(r"(\d+\.\d+\.\d+)", first_line[0])
    return version_match.group(1) if version_match else first_line[0].strip()

