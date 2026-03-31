"""Search execution helpers for RG Search GUI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from rg_search_gui.models import SearchFileResult, SearchHit, SearchOptions
from rg_search_gui.search_helpers import _clone_results, _matches_exclude, _matches_include, _sorted_results


def _is_rg_engine(engine: str) -> bool:
    basename = os.path.basename(engine).lower()
    return basename == "rg" or basename.startswith("rg.") or basename == "rg.exe"


def _iter_candidate_files(options: SearchOptions):
    max_bytes = options.max_file_size_mb * 1024 * 1024
    for root in options.folders:
        if options.recursive:
            for current_root, dirs, files in os.walk(root, onerror=lambda _exc: None):
                current_path = Path(current_root)
                kept_dirs: list[str] = []
                for directory in dirs:
                    rel_dir = (current_path / directory).relative_to(root).as_posix()
                    if not _matches_exclude(rel_dir, directory, options.exclude_patterns):
                        kept_dirs.append(directory)
                dirs[:] = kept_dirs
                for file_name in files:
                    path = current_path / file_name
                    if _accept_file(path, root, file_name, options, max_bytes):
                        yield root, path
            continue

        try:
            children = list(root.iterdir())
        except OSError:
            continue

        for path in children:
            if path.is_file() and _accept_file(path, root, path.name, options, max_bytes):
                yield root, path


def _accept_file(path: Path, root: Path, name: str, options: SearchOptions, max_bytes: int) -> bool:
    relative_path = path.relative_to(root).as_posix()
    if not _matches_include(relative_path, name, options.include_patterns):
        return False
    if _matches_exclude(relative_path, name, options.exclude_patterns):
        return False
    try:
        if path.stat().st_size > max_bytes:
            return False
    except OSError:
        return False
    return True


def _build_exclude_globs(pattern: str) -> list[str]:
    normalized = pattern.replace("\\", "/").strip()
    if not normalized:
        return []
    if any(char in normalized for char in "*?[]"):
        return ["-g", f"!{normalized}"]
    return ["-g", f"!**/{normalized}", "-g", f"!**/{normalized}/**"]


def _start_rg_process(engine_exec: str, options: SearchOptions, root: Path) -> subprocess.Popen[str]:
    cmd = [engine_exec, "--json", "--line-number", "--color", "never", "--with-filename", "--hidden", "--no-ignore"]
    if not options.case_sensitive:
        cmd.append("--ignore-case")
    if not options.use_regex:
        cmd.append("--fixed-strings")
    if options.encoding.lower() != "auto":
        cmd.extend(["--encoding", options.encoding])
    if options.max_file_size_mb > 0:
        cmd.extend(["--max-filesize", f"{options.max_file_size_mb}M"])
    if not options.recursive:
        cmd.extend(["--max-depth", "1"])
    for include_pattern in options.include_patterns:
        if include_pattern not in {"*", "*.*"}:
            cmd.extend(["-g", include_pattern])
    for exclude_pattern in options.exclude_patterns:
        cmd.extend(_build_exclude_globs(exclude_pattern))
    cmd.extend([options.text, "."])
    return subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _search_with_rg_stream(
    engine_exec: str,
    options: SearchOptions,
    sort_mode: str = "Matches desc",
    emit_result=None,
    stop_event=None,
    process_callback=None,
) -> list[SearchFileResult]:
    grouped_results: dict[str, SearchFileResult] = {}
    match_count = 0
    last_emit = 0.0

    for root in options.folders:
        process = _start_rg_process(engine_exec, options, root)
        if process_callback is not None:
            process_callback(process)

        try:
            for raw_line in iter(process.stdout.readline, ""):
                if stop_event is not None and stop_event.is_set():
                    try:
                        process.terminate()
                    except Exception:
                        pass
                    break
                if not raw_line.strip():
                    continue

                payload = json.loads(raw_line)
                if payload.get("type") != "match":
                    continue

                data = payload.get("data", {})
                relative_path = data.get("path", {}).get("text", "")
                if not relative_path:
                    continue

                full_path = (root / relative_path).resolve()
                key = str(full_path)
                result = grouped_results.get(key)
                if result is None:
                    result = SearchFileResult(
                        source_folder=root,
                        relative_path=Path(relative_path).as_posix(),
                        full_path=full_path,
                        hits=[],
                    )
                    grouped_results[key] = result

                result.hits.append(
                    SearchHit(
                        line_number=int(data.get("line_number", 0)),
                        content=data.get("lines", {}).get("text", "").rstrip("\r\n"),
                    )
                )
                match_count += 1
                now = time.monotonic()
                if emit_result and now - last_emit >= 0.15:
                    emit_result(_clone_results(grouped_results.values()), match_count, relative_path)
                    last_emit = now
        finally:
            try:
                process.wait()
            except Exception:
                pass
            if process_callback is not None:
                process_callback(None)

        stderr = (process.stderr.read() or "").strip()
        if stop_event is not None and stop_event.is_set():
            break
        if process.returncode not in {0, 1}:
            raise RuntimeError(stderr or f"rg exit code {process.returncode}")

    return _sorted_results(grouped_results.values(), sort_mode)


def _search_with_python_fallback_stream(
    options: SearchOptions,
    sort_mode: str = "Matches desc",
    emit_result=None,
    stop_event=None,
) -> list[SearchFileResult]:
    grouped_results: list[SearchFileResult] = []
    match_count = 0
    last_emit = 0.0
    matcher = _build_matcher(options)

    for root_folder, file_path in _iter_candidate_files(options):
        if stop_event is not None and stop_event.is_set():
            break

        hits = _search_single_file_python(file_path, matcher, options.encoding)
        if not hits:
            continue

        grouped_results.append(
            SearchFileResult(
                source_folder=root_folder,
                relative_path=file_path.relative_to(root_folder).as_posix(),
                full_path=file_path,
                hits=hits,
            )
        )
        match_count += len(hits)
        now = time.monotonic()
        if emit_result and now - last_emit >= 0.15:
            emit_result(_sorted_results(grouped_results, sort_mode), match_count, file_path.name)
            last_emit = now

    return _sorted_results(grouped_results, sort_mode)


def _search_with_grep_fallback_stream(
    options: SearchOptions,
    sort_mode: str = "Matches desc",
    emit_result=None,
    stop_event=None,
) -> list[SearchFileResult]:
    return _search_with_python_fallback_stream(
        options,
        sort_mode=sort_mode,
        emit_result=emit_result,
        stop_event=stop_event,
    )


def _iter_searchable_lines(file_path: Path, encoding: str) -> list[str]:
    candidate_encodings = [encoding] if encoding.lower() != "auto" else ["utf-8", "utf-8-sig", "cp950", "big5", "utf-16"]
    for candidate in candidate_encodings:
        try:
            with file_path.open("r", encoding=candidate) as file_handle:
                return file_handle.readlines()
        except UnicodeError:
            continue
        except OSError:
            return []
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as file_handle:
            return file_handle.readlines()
    except Exception:
        return []


def _build_matcher(options: SearchOptions):
    if options.use_regex:
        flags = 0 if options.case_sensitive else re.IGNORECASE
        pattern = re.compile(options.text, flags)
        return lambda content: bool(pattern.search(content))

    needle = options.text if options.case_sensitive else options.text.lower()
    if options.case_sensitive:
        return lambda content: needle in content
    return lambda content: needle in content.lower()


def _search_single_file_python(file_path: Path, matcher, encoding: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for line_number, raw_line in enumerate(_iter_searchable_lines(file_path, encoding), start=1):
        line_text = raw_line.rstrip("\r\n")
        if matcher(line_text):
            hits.append(SearchHit(line_number=line_number, content=line_text))
    return hits

