"""顯示層用的路徑脫敏工具。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath


def _looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\") or "\\" in value)


def _path_cls_for(*values: str) -> type[PurePath]:
    return PureWindowsPath if any(_looks_like_windows_path(value) for value in values if value) else PurePosixPath


def _relative_parts(path_text: str, base_text: str) -> tuple[str, ...] | None:
    path_cls = _path_cls_for(path_text, base_text)
    path_obj = path_cls(path_text)
    base_obj = path_cls(base_text)
    path_parts = tuple(part.lower() for part in path_obj.parts)
    base_parts = tuple(part.lower() for part in base_obj.parts)
    if len(path_parts) < len(base_parts) or path_parts[: len(base_parts)] != base_parts:
        return None
    return path_obj.parts[len(base_obj.parts) :]


def _join_display_path(prefix: str, parts: tuple[str, ...], path_cls: type[PurePath]) -> str:
    separator = "\\" if path_cls is PureWindowsPath else "/"
    if not parts:
        return prefix
    return prefix + separator + separator.join(parts)


def _mask_user_profile_segment(path_text: str) -> str:
    return re.sub(r"(?i)^([A-Za-z]:\\Users\\)[^\\]+", r"\1<user>", path_text)


def redact_path_for_display(
    path: str | os.PathLike[str] | None,
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str | None] | None = None,
) -> str:
    if not path:
        return "N/A"

    path_text = os.fspath(path)
    env_map = dict(os.environ) if env is None else dict(env)
    cwd_text = os.fspath(cwd) if cwd is not None else str(Path.cwd())
    home_text = os.fspath(home) if home is not None else str(Path.home())
    replacements: list[tuple[str, str]] = []

    if cwd_text:
        replacements.append((".", cwd_text))
    if env_map.get("APPDATA"):
        replacements.append(("%APPDATA%", str(env_map["APPDATA"])))
    if env_map.get("LOCALAPPDATA"):
        replacements.append(("%LOCALAPPDATA%", str(env_map["LOCALAPPDATA"])))
    if home_text:
        replacements.append(("~", home_text))

    for display_prefix, base_text in replacements:
        parts = _relative_parts(path_text, base_text)
        if parts is not None:
            path_cls = _path_cls_for(path_text, base_text)
            return _join_display_path(display_prefix, parts, path_cls)

    return _mask_user_profile_segment(path_text)


def display_root_label(
    path: str | os.PathLike[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str | None] | None = None,
) -> str:
    redacted = redact_path_for_display(path, cwd=cwd, home=home, env=env)
    if redacted in {".", "~"}:
        return redacted
    if redacted.startswith((".\\", "./", "~\\", "~/", "%APPDATA%\\", "%APPDATA%/", "%LOCALAPPDATA%\\", "%LOCALAPPDATA%/")):
        return redacted
    path_cls = _path_cls_for(redacted)
    return path_cls(redacted).name or redacted
