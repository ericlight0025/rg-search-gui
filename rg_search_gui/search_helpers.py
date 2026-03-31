"""Pure search and preview helpers for RG Search GUI."""

from __future__ import annotations

import fnmatch
import keyword
import re
from collections.abc import Iterable
from pathlib import Path

from rg_search_gui.models import ContextLine, SearchFileResult, SearchHit
from rg_search_gui.privacy_helpers import display_root_label


def _split_patterns(raw_text: str) -> list[str]:
    normalized = raw_text.replace("\n", ";").replace(",", ";")
    return [part.strip() for part in normalized.split(";") if part.strip()]


def _parse_positive_int(raw_text: str, default: int, minimum: int = 1, maximum: int = 999) -> int:
    try:
        value = int(raw_text.strip())
    except (AttributeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _matches_filter_text(candidate: str, filter_text: str) -> bool:
    normalized_filter = filter_text.strip().lower()
    if not normalized_filter:
        return True
    return normalized_filter in candidate.lower()


def _matches_include(relative_path: str, name: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(name, normalized) or fnmatch.fnmatch(relative_path, normalized):
            return True
    return False


def _matches_exclude(relative_path: str, name: str, patterns: Iterable[str]) -> bool:
    path_lower = relative_path.lower()
    name_lower = name.lower()
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").lower()
        if fnmatch.fnmatch(relative_path, normalized) or fnmatch.fnmatch(name, normalized):
            return True
        if normalized in path_lower or normalized in name_lower:
            return True
    return False


def _filter_file_results(
    results: list[SearchFileResult],
    filter_text: str,
    root_filter: str,
    extension_filter: str,
    min_hits: int,
    sort_mode: str,
) -> list[SearchFileResult]:
    filtered_results: list[SearchFileResult] = []
    for result in results:
        haystack = f"{result.full_path.name} {result.relative_path} {result.full_path}"
        if not _matches_filter_text(haystack, filter_text):
            continue
        if root_filter not in {"", "All"} and display_root_label(result.source_folder) != root_filter:
            continue
        suffix = result.full_path.suffix or "<none>"
        if extension_filter not in {"", "All"} and suffix != extension_filter:
            continue
        if len(result.hits) < max(1, min_hits):
            continue
        filtered_results.append(result)
    return _sorted_results(filtered_results, sort_mode)


def _unique_result_roots(results: list[SearchFileResult]) -> set[Path]:
    return {result.source_folder for result in results}


def _display_file_name(result: SearchFileResult, include_root: bool) -> str:
    if include_root:
        return f"{display_root_label(result.source_folder)} / {result.full_path.name}"
    return result.full_path.name


def _build_context_lines(result: SearchFileResult, display_lines: int) -> list[ContextLine]:
    try:
        with result.full_path.open("r", encoding="utf-8", errors="replace") as file_handle:
            lines = file_handle.readlines()
    except Exception:
        lines = []
    return _build_context_lines_from_lines(lines, result.hits, display_lines)


def _build_context_lines_from_lines(lines: list[str], hits: list[SearchHit], display_lines: int) -> list[ContextLine]:
    total_lines = max(1, display_lines)
    lines_before = total_lines // 2
    lines_after = total_lines - lines_before - 1
    hit_lines = {int(hit.line_number) for hit in hits}

    context_line_numbers: set[int] = set()
    for hit_line in hit_lines:
        start = max(1, hit_line - lines_before)
        end = hit_line + lines_after
        for line_number in range(start, end + 1):
            context_line_numbers.add(line_number)

    built_lines: list[ContextLine] = []
    for line_number in sorted(context_line_numbers):
        content = ""
        index = line_number - 1
        if 0 <= index < len(lines):
            content = lines[index].rstrip("\r\n")
        built_lines.append(ContextLine(line_number=line_number, content=content, is_hit=line_number in hit_lines))
    return built_lines


def _find_match_spans(content: str, query: str, case_sensitive: bool, use_regex: bool) -> list[tuple[int, int]]:
    if not content or not query:
        return []

    if use_regex:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            return [(match.start(), match.end()) for match in re.finditer(query, content, flags)]
        except re.error:
            return []

    source = content if case_sensitive else content.lower()
    needle = query if case_sensitive else query.lower()
    spans: list[tuple[int, int]] = []
    start_index = 0
    while True:
        match_index = source.find(needle, start_index)
        if match_index == -1:
            break
        end_index = match_index + len(needle)
        spans.append((match_index, end_index))
        start_index = end_index if end_index > match_index else match_index + 1
    return spans


def _find_syntax_spans(content: str, suffix: str) -> list[tuple[str, int, int]]:
    if not content:
        return []

    spans: list[tuple[str, int, int]] = []
    if suffix == ".py":
        comment_match = re.search(r"#.*$", content)
        if comment_match:
            spans.append(("syntax_comment", comment_match.start(), comment_match.end()))
        for match in re.finditer(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", content):
            spans.append(("syntax_string", match.start(), match.end()))
        for match in re.finditer(r"\b\d+(?:\.\d+)?\b", content):
            spans.append(("syntax_number", match.start(), match.end()))
        keyword_pattern = r"\b(" + "|".join(re.escape(word) for word in keyword.kwlist) + r")\b"
        for match in re.finditer(keyword_pattern, content):
            spans.append(("syntax_keyword", match.start(), match.end()))
        return spans

    if suffix in {".java", ".js", ".ts", ".tsx", ".jsx", ".cs", ".cpp", ".c", ".h"}:
        comment_match = re.search(r"//.*$", content)
        if comment_match:
            spans.append(("syntax_comment", comment_match.start(), comment_match.end()))
        for match in re.finditer(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")", content):
            spans.append(("syntax_string", match.start(), match.end()))
        for match in re.finditer(r"\b\d+(?:\.\d+)?\b", content):
            spans.append(("syntax_number", match.start(), match.end()))
        c_like_keywords = {
            "abstract", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue",
            "default", "do", "double", "else", "enum", "extends", "false", "final", "finally", "float",
            "for", "if", "implements", "import", "int", "interface", "long", "native", "new", "null",
            "package", "private", "protected", "public", "return", "short", "static", "super", "switch",
            "this", "throw", "throws", "true", "try", "void", "while", "var", "let", "function", "async",
            "await", "using", "namespace", "string", "decimal", "bool", "record",
        }
        keyword_pattern = r"\b(" + "|".join(re.escape(word) for word in sorted(c_like_keywords)) + r")\b"
        for match in re.finditer(keyword_pattern, content):
            spans.append(("syntax_keyword", match.start(), match.end()))
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9_]*\b", content):
            spans.append(("syntax_type", match.start(), match.end()))
        return spans

    if suffix in {".sql", ".ddl", ".dml"}:
        comment_match = re.search(r"--.*$", content)
        if comment_match:
            spans.append(("syntax_comment", comment_match.start(), comment_match.end()))
        for match in re.finditer(r"('([^'\\]|\\.)*')", content):
            spans.append(("syntax_string", match.start(), match.end()))
        for match in re.finditer(r"\b\d+(?:\.\d+)?\b", content):
            spans.append(("syntax_number", match.start(), match.end()))
        sql_keywords = {
            "select", "from", "where", "join", "left", "right", "inner", "outer", "on", "and", "or",
            "insert", "into", "update", "delete", "create", "alter", "drop", "table", "view", "index",
            "group", "by", "order", "having", "distinct", "case", "when", "then", "else", "end",
            "union", "all", "null", "is", "not", "as", "top", "set", "begin", "commit", "rollback",
        }
        keyword_pattern = r"\b(" + "|".join(re.escape(word) for word in sorted(sql_keywords)) + r")\b"
        for match in re.finditer(keyword_pattern, content, re.IGNORECASE):
            spans.append(("syntax_keyword", match.start(), match.end()))
        return spans

    return spans


def _clone_results(results: Iterable[SearchFileResult]) -> list[SearchFileResult]:
    cloned: list[SearchFileResult] = []
    for result in results:
        cloned.append(
            SearchFileResult(
                source_folder=result.source_folder,
                relative_path=result.relative_path,
                full_path=result.full_path,
                hits=list(result.hits),
            )
        )
    return cloned


def _sorted_results(results: Iterable[SearchFileResult], sort_mode: str) -> list[SearchFileResult]:
    materialized = list(results)
    if sort_mode == "Matches asc":
        materialized.sort(key=lambda item: (len(item.hits), item.full_path.name.lower()))
    elif sort_mode == "Name asc":
        materialized.sort(key=lambda item: (item.full_path.name.lower(), str(item.source_folder).lower()))
    elif sort_mode == "Name desc":
        materialized.sort(key=lambda item: (item.full_path.name.lower(), str(item.source_folder).lower()), reverse=True)
    elif sort_mode == "Root asc":
        materialized.sort(key=lambda item: (item.source_folder.name.lower(), item.full_path.name.lower()))
    else:
        materialized.sort(key=lambda item: (-len(item.hits), item.full_path.name.lower()))
    return materialized



