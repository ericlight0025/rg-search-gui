"""Shared data models for RG Search GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SearchOptions:
    folders: list[Path]
    include_patterns: list[str]
    exclude_patterns: list[str]
    text: str
    recursive: bool
    case_sensitive: bool
    use_regex: bool
    encoding: str
    max_file_size_mb: int


@dataclass
class SearchHit:
    line_number: int
    content: str


@dataclass
class SearchFileResult:
    source_folder: Path
    relative_path: str
    full_path: Path
    hits: list[SearchHit] = field(default_factory=list)


@dataclass
class ContextLine:
    line_number: int
    content: str
    is_hit: bool = False


@dataclass
class EngineInfo:
    executable: str | None
    label: str
    version: str = ""
