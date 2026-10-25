"""Search execution helpers for RG Search GUI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from rg_search_gui.models import SearchFileResult, SearchHit, SearchOptions
from rg_search_gui.search_helpers import (
    MATCH_ALL_INCLUDE_PATTERNS,
    _clone_results,
    _matches_exclude,
    _matches_include,
    _normalize_glob,
    _sorted_results,
)

# rg 只認 WHATWG 編碼名稱，不認得 cp950；cp950 是 Big5 的超集，改傳 big5。
RG_ENCODING_LABELS = {"cp950": "big5"}


def _is_rg_engine(engine: str) -> bool:
    basename = os.path.basename(engine).lower()
    return basename == "rg" or basename.startswith("rg.") or basename == "rg.exe"


def _rg_encoding_passes(options: SearchOptions) -> list[str | None]:
    # Auto 時 rg 只認 UTF-8／UTF-16 BOM；查詢含中文時再用 Big5 掃一次，才找得到舊系統的 Big5 檔案。
    if options.encoding.lower() == "auto" and not options.text.isascii():
        return [None, "big5"]
    return [None]


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
        # 與 rg 端一致：0 代表不限制大小。
        if max_bytes > 0 and path.stat().st_size > max_bytes:
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


def _start_rg_process(
    engine_exec: str, options: SearchOptions, root: Path, encoding: str | None = None
) -> subprocess.Popen[str]:
    # --glob-case-insensitive：Windows 檔名不分大小寫，排除 Node_Modules 也要生效。
    cmd = [
        engine_exec, "--json", "--line-number", "--color", "never", "--with-filename", "--hidden", "--no-ignore",
        "--glob-case-insensitive",
    ]
    if not options.case_sensitive:
        cmd.append("--ignore-case")
    if not options.use_regex:
        cmd.append("--fixed-strings")
    encoding = encoding or options.encoding
    if encoding.lower() != "auto":
        cmd.extend(["--encoding", RG_ENCODING_LABELS.get(encoding.lower(), encoding)])
    if options.max_file_size_mb > 0:
        cmd.extend(["--max-filesize", f"{options.max_file_size_mb}M"])
    if not options.recursive:
        cmd.extend(["--max-depth", "1"])
    for include_pattern in options.include_patterns:
        # rg 的 glob 把反斜線當跳脫字元，Windows 習慣的 src\*.java 要先轉成 src/*.java。
        normalized = _normalize_glob(include_pattern)
        if normalized and normalized not in MATCH_ALL_INCLUDE_PATTERNS:
            cmd.extend(["-g", normalized])
    for exclude_pattern in options.exclude_patterns:
        cmd.extend(_build_exclude_globs(exclude_pattern))
    # 使用 -e 與 -- 隔開查詢字串，避免以 - 開頭的文字被 rg 當成旗標。
    cmd.extend(["-e", options.text, "--", "."])
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
    warning_callback=None,
) -> list[SearchFileResult]:
    grouped_results: dict[str, SearchFileResult] = {}
    seen_lines: set[tuple[str, int]] = set()
    match_count = 0
    last_emit = 0.0
    passes = _rg_encoding_passes(options)

    for root in options.folders:
        for encoding in passes:
            if stop_event is not None and stop_event.is_set():
                break
            process = _start_rg_process(engine_exec, options, root, encoding)
            if process_callback is not None:
                process_callback(process)
            # stderr 必須同時讀；錯誤訊息塞滿管線後 rg 會停住，畫面就卡在「搜尋中」。
            stderr_chunks: list[str] = []
            stderr_reader = threading.Thread(
                target=lambda stream=process.stderr, sink=stderr_chunks: sink.append(stream.read() or ""),
                daemon=True,
            )
            stderr_reader.start()
            searched = False
            last_relative_path = ""
            full_path = root

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

                    try:
                        payload = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "summary":
                        searched = True
                        continue
                    if payload.get("type") != "match":
                        continue

                    data = payload.get("data", {})
                    relative_path = data.get("path", {}).get("text", "")
                    if not relative_path:
                        continue

                    # 同一檔案的命中連續輸出，只在換檔時 resolve。
                    if relative_path != last_relative_path:
                        last_relative_path = relative_path
                        full_path = (root / relative_path).resolve()
                    key = str(full_path)
                    line_number = int(data.get("line_number", 0))
                    # Big5 補掃可能再次找到同一行，不重複計算。
                    if (key, line_number) in seen_lines:
                        continue
                    seen_lines.add((key, line_number))
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
                            line_number=line_number,
                            content=data.get("lines", {}).get("text", "").rstrip("\r\n"),
                        )
                    )
                    match_count += 1
                    now = time.monotonic()
                    # 結果越多，複製與重畫越貴；拉長更新間隔，避免 UI 被串流更新拖垮。
                    if emit_result and now - last_emit >= min(1.0, 0.15 + len(grouped_results) / 5000):
                        emit_result(_clone_results(grouped_results.values()), match_count, relative_path)
                        last_emit = now
            finally:
                try:
                    process.wait()
                except Exception:
                    pass
                stderr_reader.join(timeout=5)
                if process_callback is not None:
                    process_callback(None)

            stderr = "".join(stderr_chunks).strip()
            if stop_event is not None and stop_event.is_set():
                break
            if process.returncode in {0, 1}:
                continue
            if searched:
                # rg 有跑完（有 summary），只是部分路徑讀不到；保留結果，改為提示。
                if warning_callback is not None and stderr:
                    warning_callback(stderr)
                continue
            if "regex parse error" in stderr:
                raise RuntimeError(f"正規表示式語法錯誤（rg 不支援 look-around 等語法）：\n{stderr}")
            raise RuntimeError(stderr or f"rg exit code {process.returncode}")

    for result in grouped_results.values():
        result.hits.sort(key=lambda hit: hit.line_number)
    return _sorted_results(grouped_results.values(), sort_mode)


def _search_with_python_fallback_stream(
    options: SearchOptions,
    sort_mode: str = "Matches desc",
    emit_result=None,
    stop_event=None,
) -> list[SearchFileResult]:
    grouped_results: list[SearchFileResult] = []
    seen_paths: set[str] = set()
    match_count = 0
    last_emit = 0.0
    matcher = _build_matcher(options)

    for root_folder, file_path in _iter_candidate_files(options):
        if stop_event is not None and stop_event.is_set():
            break

        hits = _search_single_file_python(file_path, matcher, options.encoding)
        if not hits:
            continue
        # 同一檔案不可出現兩次，否則結果清單以路徑當 iid 時會出錯。
        key = str(file_path.resolve())
        if key in seen_paths:
            continue
        seen_paths.add(key)

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

