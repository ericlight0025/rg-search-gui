#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Java Log Analyzer CLI（第一版）

流程：開啟 Log → 輸入操作者 → 找出 Thread ID → 依 Thread ID 篩選 → 搜尋關鍵字 → 顯示前後 N 行。

Windows 用法：
    python log_analyzer.py "C:\\Logs\\application.log"
    python log_analyzer.py "C:\\Logs\\application.log" -o A123456 -i
    python log_analyzer.py "C:\\Logs\\application.log" -e cp950
    python log_analyzer.py --help

設計重點：
- 原始 Log 只以唯讀二進位模式（'rb'）開啟；不寫任何檔案、不建永久索引或快取。
- 讀檔一律串流逐行，不使用 readlines()。
- 記憶體只保存每行的「檔案位置」(array 'q') 與「Thread 編號」(array 'i')，
  100 萬行約 12MB；搜尋結果只保存位置，內容要顯示時才回檔案讀取。
- 不確定屬於哪個 Thread 的行一律不納入，不猜測。
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import shutil
import sys
import time
import unicodedata
from array import array

# ---------------------------------------------------------------------------
# 預設規則（皆可用命令列參數覆寫）
# ---------------------------------------------------------------------------

# 一筆 Log 記錄的開頭：時間戳記 + （可有可無的等級）+ [Thread]
# 支援：2026-10-26 10:00:00.123 [http-nio-8080-exec-37] INFO ...
#       2026-10-26 10:00:00,123 INFO  [http-nio-8080-exec-37] ...
DEFAULT_THREAD_REGEX = (
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?\s+"
    r"(?:\[?[A-Z]{4,5}\]?\s+)?\[(?P<thread>[^\[\]]+)\]"
)

# 「確定屬於前一筆記錄」的續行：Java Stack Trace 與 Oracle 錯誤訊息續行。
# 只有前一行已確定歸屬時才會被納入；其他沒有 Thread ID 的行一律不納入。
DEFAULT_CONT_REGEX = (
    r"^(?:\s+at\s+\S"                                     # \tat com.foo.Bar.baz(Bar.java:10)
    r"|\s*Caused by:"                                     # Caused by: ...
    r"|\s*Suppressed:"                                    # Suppressed: ...
    r"|\s+\.\.\.\s*\d+\s+(?:more|common frames omitted)"  # ... 12 more
    r"|\s*(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?:Exception|Error|Throwable)(?::|\s*$)"  # java.sql.SQLException: ...
    r"|\s*ORA-\d{5}:)"                                    # ORA-06512: at line 45
)

UNSUPPORTED_ENCODINGS = ("utf_16", "utf_32")  # 以換行位元組切行，不支援多位元組換行的編碼
PROGRESS_EVERY = 1 << 17
SEEK_MODE_RATIO = 20  # 篩選行數 × 此值 < 總行數 時，改用依位置直接讀，不掃整份檔案


class AppError(Exception):
    """可預期的操作錯誤：顯示訊息，不顯示 Traceback。"""


class LogChangedError(AppError):
    """分析期間 Log 檔被修改。"""


class QuitApp(Exception):
    """使用者要求離開。"""


# ---------------------------------------------------------------------------
# 終端機輸出（顏色、寬度）
# ---------------------------------------------------------------------------

USE_COLOR = False


def enable_color(disabled: bool) -> bool:
    """嘗試開啟 ANSI 顏色；Windows 10/11 需要開啟 Virtual Terminal 模式，失敗就退回一般文字。"""
    if disabled or os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001 - 任何失敗都退回無色
        return False


def color(text: str, code: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if USE_COLOR else text


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def clip(text: str, width: int) -> str:
    """依顯示寬度截斷（中文字算 2 格）。"""
    used = 0
    for i, ch in enumerate(text):
        used += char_width(ch)
        if used > width:
            return text[: max(0, i - 1)] + "…"
    return text


def term_width() -> int:
    return max(60, shutil.get_terminal_size((120, 30)).columns)


def rule() -> str:
    return "=" * min(term_width() - 1, 80)


def ask(prompt: str) -> str:
    """讀取使用者輸入；EOF（例如輸入被導向且已讀完）視為離開。"""
    try:
        return input(prompt).strip()
    except EOFError:
        raise QuitApp() from None


def show_progress(label: str, done: int, total: int) -> None:
    if sys.stderr.isatty() and total > 0:
        sys.stderr.write(f"\r{label} {done * 100 // total:3d}%")
        sys.stderr.flush()


def end_progress() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 40 + "\r")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Step 1：開啟 Log、建立輕量記憶體索引
# ---------------------------------------------------------------------------


class LogIndex:
    """一份 Log 的記憶體索引：每行的檔案位置與 Thread 編號（-1 表示無法歸屬）。"""

    def __init__(self, path: str, encoding: str) -> None:
        self.path = path
        self.encoding = encoding
        self.offsets = array("q")  # 第 i 行的起始位元組；最後多放一個結尾位置
        self.tids = array("i")  # 第 i 行的 Thread 編號；-1 = 無法歸屬
        self.is_cont = bytearray()  # 第 i 行是否為歸屬到前一筆記錄的續行（1 byte/行）
        self.names: list[str] = []  # Thread 編號 → 完整 Thread ID
        self.size = 0  # 已索引的位元組數
        self.head = b""  # 檔頭樣本，用來偵測檔案被改
        self.tail = b""  # 已索引範圍結尾樣本
        self.headers = 0  # 有 Thread ID 的記錄行
        self.cont = 0  # 已歸屬的續行（Stack Trace 等）
        self.unattributed = 0  # 無法歸屬的非空白行
        self.unattributed_sample: list[int] = []
        self.decode_errors = 0
        self.first_decode_error = 0
        self.grow_warned = False

    @property
    def line_count(self) -> int:
        return len(self.tids)

    def thread_of(self, line_idx: int) -> str:
        tid = self.tids[line_idx]
        return self.names[tid] if tid >= 0 else "（無法歸屬）"


def check_encoding(name: str) -> str:
    try:
        normalized = codecs.lookup(name).name
    except LookupError:
        raise AppError(f"不認得的字元編碼：{name}（常見：utf-8、cp950、big5、gbk）") from None
    if normalized.replace("-", "_").startswith(UNSUPPORTED_ENCODINGS):
        raise AppError(f"不支援 {name}：本工具以換行字元切行，請先將 Log 轉成 UTF-8 或 Big5 等編碼。")
    return normalized


def compile_regex(pattern: str, label: str, need_thread: bool) -> re.Pattern[str]:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise AppError(f"{label} 語法錯誤：{exc}\n  Regex：{pattern}") from None
    if need_thread and "thread" not in compiled.groupindex:
        raise AppError(f"{label} 必須包含命名群組 (?P<thread>...)\n  Regex：{pattern}")
    return compiled


def open_log_checked(path: str) -> int:
    """確認檔案存在、是檔案、可讀取；回傳檔案大小。"""
    if not os.path.exists(path):
        raise AppError(f"找不到檔案：{path}")
    if not os.path.isfile(path):
        raise AppError(f"這不是檔案：{path}")
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except PermissionError:
        raise AppError(f"沒有讀取權限：{path}（檔案可能被其他程式鎖定）") from None
    except OSError as exc:
        raise AppError(f"無法讀取檔案：{path}（{exc.strerror or exc}）") from None
    return os.path.getsize(path)


def build_index(path: str, encoding: str, thread_re: re.Pattern[str], cont_re: re.Pattern[str]) -> LogIndex:
    """串流讀一次整份 Log：記錄每行位置，並判斷每行屬於哪個 Thread。

    規則：
    - 符合 Thread Regex 的行 = 一筆新記錄，Thread ID 取 (?P<thread>) 群組的完整字串。
    - 不符合的行，只有「符合續行規則」且「前一行已確定歸屬」時，才歸屬到前一筆記錄的 Thread。
    - 其他行（含空白行）一律標為無法歸屬，且中斷續行鏈。
    """
    total = open_log_checked(path)
    idx = LogIndex(path, encoding)
    offsets, tids, is_cont = idx.offsets, idx.tids, idx.is_cont
    name_to_id: dict[str, int] = {}
    names = idx.names
    t_search = thread_re.search
    c_match = cont_re.match
    current = -1
    pos = 0
    headers = cont = unattributed = decode_errors = 0
    sample = idx.unattributed_sample

    with open(path, "rb") as fh:
        idx.head = fh.read(4096)
        fh.seek(0)
        for i, raw in enumerate(fh):
            offsets.append(pos)
            pos += len(raw)
            try:
                line = raw.decode(encoding)
            except UnicodeDecodeError:
                line = raw.decode(encoding, "replace")
                decode_errors += 1
                if not idx.first_decode_error:
                    idx.first_decode_error = i + 1
            if i == 0 and line.startswith("\ufeff"):
                line = line[1:]

            m = t_search(line)
            thread = m.group("thread") if m else None
            flag = 0
            if thread is not None and thread.strip():
                thread = thread.strip()
                tid = name_to_id.get(thread)
                if tid is None:
                    tid = name_to_id[thread] = len(names)
                    names.append(thread)
                current = tid
                headers += 1
            elif current >= 0 and m is None and c_match(line):
                cont += 1  # 續行：沿用前一筆的 Thread
                flag = 1
            else:
                current = -1
                if line.strip():
                    unattributed += 1
                    if len(sample) < 5:
                        sample.append(i + 1)
            tids.append(current)
            is_cont.append(flag)
            if (i & (PROGRESS_EVERY - 1)) == 0:
                show_progress("讀取中…", pos, total)
        offsets.append(pos)
    end_progress()

    idx.size = pos
    idx.headers, idx.cont, idx.unattributed, idx.decode_errors = headers, cont, unattributed, decode_errors
    with open(path, "rb") as fh:
        fh.seek(max(0, pos - 4096))
        idx.tail = fh.read(min(4096, pos))
    if os.path.getsize(path) < pos:
        raise LogChangedError("讀取期間 Log 檔案變小了（可能被截斷或輪替），請重新執行。")
    return idx


def ensure_unchanged(idx: LogIndex) -> None:
    """每次回檔案讀資料前確認：檔案沒被截斷、已分析範圍的內容沒變。只多出新內容則提示後繼續。"""
    try:
        size = os.path.getsize(idx.path)
        if size < idx.size:
            raise LogChangedError("Log 檔案變小了（可能被截斷或輪替），先前的分析已不正確，請重新執行。")
        with open(idx.path, "rb") as fh:
            head = fh.read(len(idx.head))
            fh.seek(idx.size - len(idx.tail))
            tail = fh.read(len(idx.tail))
    except OSError as exc:
        raise LogChangedError(f"無法再讀取 Log：{exc.strerror or exc}") from None
    if head != idx.head or tail != idx.tail:
        raise LogChangedError("Log 內容在分析期間被修改，先前的分析已不正確，請重新執行。")
    if size > idx.size and not idx.grow_warned:
        idx.grow_warned = True
        print(color(f"注意：Log 仍在寫入（多了 {size - idx.size:,} bytes），本次只分析開啟時的 {idx.line_count:,} 行。", "33"))


def read_line(fh, idx: LogIndex, line_idx: int) -> str:
    """依索引讀出單一行（原始內容，去掉換行）。"""
    start, end = idx.offsets[line_idx], idx.offsets[line_idx + 1]
    fh.seek(start)
    text = fh.read(end - start).decode(idx.encoding, "replace")
    if line_idx == 0 and text.startswith("\ufeff"):
        text = text[1:]
    return text.rstrip("\r\n")


def report_index(idx: LogIndex, elapsed: float, thread_regex: str) -> None:
    mb = idx.size / 1048576
    print(
        f"已讀取 {idx.line_count:,} 行（{mb:,.1f} MB），耗時 {elapsed:.2f} 秒；"
        f"Thread 記錄 {idx.headers:,} 行、Stack Trace 續行 {idx.cont:,} 行、"
        f"無法歸屬 {idx.unattributed:,} 行；共 {len(idx.names):,} 個 Thread ID。"
    )
    if idx.decode_errors:
        print(color(
            f"警告：有 {idx.decode_errors:,} 行無法用 {idx.encoding} 解碼（第一個在第 {idx.first_decode_error:,} 行），"
            f"已用 � 取代。若內容亂碼，請用 -e 指定編碼，例如 -e cp950。", "33"))
    if idx.headers == 0:
        with open(idx.path, "rb") as fh:
            samples = [read_line(fh, idx, i) for i in range(min(3, idx.line_count))]
        shown = "\n".join(f"    {clip(s, term_width() - 6)}" for s in samples)
        raise AppError(
            "Thread ID Regex 不符合這份 Log，找不到任何 Thread ID。\n"
            f"  目前 Regex：{thread_regex}\n  檔案開頭：\n{shown}\n"
            "  請用 --thread-regex 指定符合的格式（需包含 (?P<thread>...)）。"
        )
    nonblank = idx.headers + idx.cont + idx.unattributed
    if nonblank and idx.unattributed / nonblank > 0.2:
        lines = "、".join(f"{n:,}" for n in idx.unattributed_sample)
        print(color(
            f"警告：{idx.unattributed / nonblank:.0%} 的行無法判斷 Thread（例如第 {lines} 行），"
            "這些行不會出現在篩選結果。若比例不合理，請檢查 --thread-regex / --cont-regex。", "33"))


# ---------------------------------------------------------------------------
# Step 2：搜尋操作者 → Thread ID
# ---------------------------------------------------------------------------


def find_operator(idx: LogIndex, operator: str) -> tuple[list[tuple[int, int, int]], int, list[int]]:
    """回傳 ([(thread 編號, 命中次數, 首次行號)], 無法歸屬的命中數, 無法歸屬的行號樣本)。

    操作者代碼以完整代碼比對：A123456 不會命中 A1234567 或 XA123456。
    """
    ensure_unchanged(idx)
    pattern = re.compile(r"(?<![0-9A-Za-z])" + re.escape(operator) + r"(?![0-9A-Za-z])")
    try:
        needle = operator.encode(idx.encoding)
    except UnicodeEncodeError:
        raise AppError(f"操作者代碼含有 {idx.encoding} 無法表示的字元：{operator}") from None
    counts: dict[int, list[int]] = {}
    unknown = 0
    unknown_sample: list[int] = []
    tids = idx.tids
    n = idx.line_count
    with open(idx.path, "rb") as fh:
        for i, raw in enumerate(fh):
            if i >= n:
                break
            if needle not in raw:  # 先用位元組快速排除，命中才解碼確認
                continue
            if not pattern.search(raw.decode(idx.encoding, "replace")):
                continue
            tid = tids[i]
            if tid < 0:
                unknown += 1
                if len(unknown_sample) < 5:
                    unknown_sample.append(i + 1)
            elif tid in counts:
                counts[tid][0] += 1
            else:
                counts[tid] = [1, i + 1]
    found = sorted(((tid, c, first) for tid, (c, first) in counts.items()), key=lambda x: (-x[1], x[2]))
    return found, unknown, unknown_sample


def ask_operator(idx: LogIndex, preset: str | None) -> tuple[str, list[tuple[int, int, int]]]:
    operator = preset
    while True:
        if not operator:
            operator = ask("請輸入操作者（Q 離開）：")
            if operator.upper() == "Q":
                raise QuitApp()
            if not operator:
                continue
        print("正在搜尋操作者...")
        start = time.perf_counter()
        found, unknown, sample = find_operator(idx, operator)
        elapsed = time.perf_counter() - start
        total = sum(c for _, c, _ in found) + unknown
        if total == 0:
            print(color(f"找不到操作者：{operator}（耗時 {elapsed:.2f} 秒）", "31"))
            operator = None
            continue
        if unknown:
            lines = "、".join(f"{n:,}" for n in sample)
            print(color(f"注意：有 {unknown:,} 筆操作者命中無法判斷 Thread ID（例如第 {lines} 行），未列入。", "33"))
        if not found:
            print(color(
                "找到操作者，但這些行都沒有可辨識的 Thread ID，無法繼續。"
                "請確認 --thread-regex 是否符合 Log 格式。", "31"))
            operator = None
            continue
        print(f"操作者 {operator} 共命中 {total:,} 筆，耗時 {elapsed:.2f} 秒。")
        return operator, found


def parse_selection(text: str, count: int) -> list[int]:
    """解析「1,2」「1-3」「A（全部）」，回傳 0 起算的索引；格式錯誤拋 ValueError。"""
    text = text.strip().upper()
    if text in ("A", "ALL", "*"):
        return list(range(count))
    chosen: list[int] = []
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                raise ValueError(part)
            values = range(lo, hi + 1)
        else:
            values = [int(part)]
        for v in values:
            if not 1 <= v <= count:
                raise ValueError(part)
            if v - 1 not in chosen:
                chosen.append(v - 1)
    if not chosen:
        raise ValueError(text)
    return chosen


def ask_threads(idx: LogIndex, found: list[tuple[int, int, int]]) -> list[int]:
    print("找到以下 Thread ID：")
    width = max(len(idx.names[tid]) for tid, _, _ in found)
    for n, (tid, c, first) in enumerate(found, 1):
        print(f"[{n}] {idx.names[tid]:<{width}}  操作者命中 {c:,} 次（首次：第 {first:,} 行）")
    while True:
        text = ask("請選擇 Thread ID（可多選，例如 1,2 或 1-3；A 全選；Q 離開）：")
        if text.upper() == "Q":
            raise QuitApp()
        try:
            return [found[i][0] for i in parse_selection(text, len(found))]
        except ValueError:
            print(color(f"輸入錯誤：請輸入 1～{len(found)} 之間的編號。", "31"))


# ---------------------------------------------------------------------------
# Step 3：依 Thread ID 篩選
# ---------------------------------------------------------------------------


class Filtered:
    """篩選結果：只存行號（array），更換關鍵字時直接重用。"""

    def __init__(self, idx: LogIndex, selected: list[int]) -> None:
        self.selected = selected
        wanted = set(selected)
        self.lines = array("i")
        self.mask = bytearray(idx.line_count)
        append = self.lines.append
        mask = self.mask
        for i, tid in enumerate(idx.tids):
            if tid in wanted:
                append(i)
                mask[i] = 1


# ---------------------------------------------------------------------------
# Step 4：在篩選結果中搜尋關鍵字
# ---------------------------------------------------------------------------


def search_keyword(idx: LogIndex, flt: Filtered, keyword: str, ignore_case: bool) -> array:
    """只搜尋篩選後的行；回傳命中在「篩選結果」中的位置（array），不保存內容。"""
    ensure_unchanged(idx)
    hits = array("i")
    encoding = idx.encoding
    kw = keyword.casefold() if ignore_case else keyword
    # UTF-8 且區分大小寫時，直接比對位元組，不必逐行解碼（UTF-8 不會誤判多位元組字元）。
    kw_bytes = keyword.encode(encoding) if (not ignore_case and encoding == "utf-8") else None
    lines = flt.lines
    n = idx.line_count
    with open(idx.path, "rb") as fh:
        if len(lines) * SEEK_MODE_RATIO < n:
            # 篩選結果只占少數：依位置直接讀這些行，不必掃整份檔案。
            offsets = idx.offsets
            for pos, li in enumerate(lines):
                fh.seek(offsets[li])
                raw = fh.read(offsets[li + 1] - offsets[li])
                if kw_bytes is not None:
                    if kw_bytes in raw:
                        hits.append(pos)
                    continue
                text = raw.decode(encoding, "replace")
                if kw in (text.casefold() if ignore_case else text):
                    hits.append(pos)
        else:
            # 篩選結果很多：串流掃一次，只檢查遮罩標記的行。
            mask = flt.mask
            last_len = idx.offsets[n] - idx.offsets[n - 1] if n else 0
            pos = -1
            for i, raw in enumerate(fh):
                if i >= n:
                    break
                if not mask[i]:
                    continue
                pos += 1
                if i == n - 1:
                    raw = raw[:last_len]
                if kw_bytes is not None:
                    if kw_bytes in raw:
                        hits.append(pos)
                    continue
                text = raw.decode(encoding, "replace")
                if kw in (text.casefold() if ignore_case else text):
                    hits.append(pos)
                if (i & (PROGRESS_EVERY - 1)) == 0:
                    show_progress("搜尋中…", idx.offsets[i], idx.size)
            end_progress()
    return hits


def highlight(text: str, keyword: str, ignore_case: bool, base: str) -> str:
    if not USE_COLOR:
        return text
    flags = re.IGNORECASE if ignore_case else 0
    parts: list[str] = []
    last = 0
    for m in re.finditer(re.escape(keyword), text, flags):
        parts.append(color(text[last:m.start()], base))
        parts.append(color(m.group(0), "1;30;43"))
        last = m.end()
    parts.append(color(text[last:], base))
    return "".join(parts)


def summary(text: str, keyword: str, ignore_case: bool, width: int) -> str:
    """命中清單的內容摘要：關鍵字太後面時，從關鍵字前一小段開始顯示。"""
    text = text.replace("\t", "    ")
    flags = re.IGNORECASE if ignore_case else 0
    m = re.search(re.escape(keyword), text, flags)
    if m and sum(char_width(c) for c in text[: m.start()]) > width - 30:
        text = "…" + text[max(0, m.start() - 20):]
    return clip(text, width)


# ---------------------------------------------------------------------------
# Step 5：命中清單與前後文
# ---------------------------------------------------------------------------


def show_hit_page(idx: LogIndex, flt: Filtered, hits: array, page: int, page_size: int,
                  keyword: str, ignore_case: bool) -> None:
    ensure_unchanged(idx)
    pages = (len(hits) - 1) // page_size + 1
    start = page * page_size
    end = min(len(hits), start + page_size)
    num_w = len(str(len(hits)))
    line_w = len(str(flt.lines[hits[end - 1]] + 1))
    width = term_width() - num_w - line_w - 6
    with open(idx.path, "rb") as fh:
        for k in range(start, end):
            li = flt.lines[hits[k]]
            text = summary(read_line(fh, idx, li), keyword, ignore_case, width)
            print(f"[{k + 1:>{num_w}}] {li + 1:>{line_w}}  {highlight(text, keyword, ignore_case, '0')}")
    print(f"—— 第 {page + 1}/{pages} 頁，共 {len(hits):,} 筆 ——")


def show_context(idx: LogIndex, flt: Filtered, hits: array, k: int, keyword: str,
                 ignore_case: bool, context: int) -> None:
    """顯示第 k 筆命中與「篩選後」的前後 context 行，行號為原始檔案行號。"""
    ensure_unchanged(idx)
    pos = hits[k]
    lo = max(0, pos - context)
    hi = min(len(flt.lines), pos + context + 1)
    hit_line = flt.lines[pos]
    line_w = len(str(flt.lines[hi - 1] + 1))
    print(rule())
    print(f"搜尋結果：{k + 1} / {len(hits):,}")
    print(f"Thread：{idx.thread_of(hit_line)}")
    print(f"關鍵字：{keyword}{'（忽略大小寫）' if ignore_case else ''}")
    print(f"原始行號：{hit_line + 1}")
    print(rule())
    with open(idx.path, "rb") as fh:
        for p in range(lo, hi):
            li = flt.lines[p]
            text = read_line(fh, idx, li).replace("\t", "    ")
            if p == pos:
                mark = color(f"{li + 1:>{line_w}}", "1;33") + color(" >", "1;33")
                print(f"{mark} {highlight(text, keyword, ignore_case, '1')}")
            else:
                print(f"{color(f'{li + 1:>{line_w}}', '2')}   {text}")
    print(rule())
    print("[N] 下一筆  [P] 上一筆  [L] 回到清單  [K] 更換關鍵字  [T] 重新選擇 Thread ID  [Q] 離開")
    print(rule())


def browse_hits(idx: LogIndex, flt: Filtered, hits: array, keyword: str, ignore_case: bool,
                context: int, page_size: int) -> str:
    """命中清單與前後文的互動迴圈；回傳 'K'（換關鍵字）或 'T'（換 Thread）。"""
    page = 0
    pages = (len(hits) - 1) // page_size + 1
    current: int | None = None
    while True:
        if current is None:
            show_hit_page(idx, flt, hits, page, page_size, keyword, ignore_case)
            cmd = ask("請選擇結果（編號；N/P 換頁；K 換關鍵字；T 換 Thread；Q 離開）：").upper().lstrip(":")
            if cmd == "N":
                if page + 1 < pages:
                    page += 1
                else:
                    print(color("已經是最後一頁。", "33"))
            elif cmd == "P":
                if page > 0:
                    page -= 1
                else:
                    print(color("已經是第一頁。", "33"))
            elif cmd in ("K", "T"):
                return cmd
            elif cmd == "Q":
                raise QuitApp()
            elif cmd.isdigit() and 1 <= int(cmd) <= len(hits):
                current = int(cmd) - 1
            else:
                print(color(f"輸入錯誤：請輸入 1～{len(hits)} 的編號，或 N/P/K/T/Q。", "31"))
            continue

        show_context(idx, flt, hits, current, keyword, ignore_case, context)
        while True:
            cmd = ask("請選擇：").upper().lstrip(":")  # 也接受 :k / :t / :q，與關鍵字提示一致
            if cmd == "N":
                if current + 1 < len(hits):
                    current += 1
                    break
                print(color("已經是最後一筆。", "33"))
            elif cmd == "P":
                if current > 0:
                    current -= 1
                    break
                print(color("已經是第一筆。", "33"))
            elif cmd == "L":
                page = current // page_size
                current = None
                break
            elif cmd in ("K", "T"):
                return cmd
            elif cmd == "Q":
                raise QuitApp()
            elif cmd.isdigit() and 1 <= int(cmd) <= len(hits):
                current = int(cmd) - 1
                break
            else:
                print(color("輸入錯誤：請輸入 N、P、L、K、T、Q 或結果編號。", "31"))


def keyword_loop(idx: LogIndex, flt: Filtered, args: argparse.Namespace) -> None:
    """關鍵字搜尋迴圈；回到這裡時沿用同一份 Thread 篩選結果。T 會拋出以重新選 Thread。"""
    ignore_case = args.ignore_case
    while True:
        mode = "忽略大小寫" if ignore_case else "區分大小寫"
        keyword = ask(f"請輸入搜尋關鍵字（目前{mode}；:i 切換、:t 重新選 Thread、:q 離開）：")
        low = keyword.lower()
        if not keyword:
            continue
        if low == ":q":
            raise QuitApp()
        if low == ":t":
            return
        if low == ":i":
            ignore_case = not ignore_case
            continue
        start = time.perf_counter()
        try:
            hits = search_keyword(idx, flt, keyword, ignore_case)
        except KeyboardInterrupt:
            end_progress()
            print(color("\n已取消搜尋。", "33"))
            continue
        elapsed = time.perf_counter() - start
        if not hits:
            print(color(f"找不到關鍵字「{keyword}」（搜尋 {len(flt.lines):,} 行，耗時 {elapsed:.2f} 秒）。", "31"))
            continue
        print(f"搜尋完成，共找到 {len(hits):,} 筆（搜尋 {len(flt.lines):,} 行，耗時 {elapsed:.2f} 秒）。")
        action = browse_hits(idx, flt, hits, keyword, ignore_case, args.context, args.page_size)
        if action == "T":
            return


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log_analyzer.py",
        description="Java Log 分析：操作者 → Thread ID → 篩選 → 關鍵字 → 前後文。原始 Log 唯讀，不建立任何檔案。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "範例：\n"
            '  python log_analyzer.py "C:\\Logs\\application.log"\n'
            '  python log_analyzer.py "C:\\Logs\\application.log" -o A123456 -i -C 15\n'
            '  python log_analyzer.py app.log --thread-regex "^\\S+ \\S+ \\[(?P<thread>[^\\]]+)\\]"\n'
        ),
    )
    parser.add_argument("log", nargs="?", help="Log 檔案路徑（沒給就互動式詢問）")
    parser.add_argument("-e", "--encoding", default="utf-8", help="Log 字元編碼（預設 utf-8；舊系統常見 cp950）")
    parser.add_argument("-o", "--operator", help="操作者代碼（沒給就互動式詢問）")
    parser.add_argument("--thread-regex", default=DEFAULT_THREAD_REGEX,
                        help="辨識一筆記錄開頭與 Thread ID 的 Regex，需含 (?P<thread>...)")
    parser.add_argument("--cont-regex", default=DEFAULT_CONT_REGEX,
                        help="可確定屬於前一筆記錄的續行 Regex（預設：Java Stack Trace、ORA- 訊息）")
    parser.add_argument("-C", "--context", type=int, default=10, help="命中前後各顯示幾行（預設 10）")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="關鍵字搜尋忽略大小寫")
    parser.add_argument("--page-size", type=int, default=20, help="命中清單每頁筆數（預設 20）")
    parser.add_argument("--no-color", action="store_true", help="不使用 ANSI 顏色")
    return parser


def run(args: argparse.Namespace) -> None:
    global USE_COLOR
    USE_COLOR = enable_color(args.no_color)
    if args.context < 0 or args.page_size < 1:
        raise AppError("--context 不可小於 0，--page-size 至少 1。")
    encoding = check_encoding(args.encoding)
    thread_re = compile_regex(args.thread_regex, "Thread ID Regex", need_thread=True)
    cont_re = compile_regex(args.cont_regex, "續行 Regex", need_thread=False)

    path = args.log
    while not path:
        path = ask("請輸入 Log 檔案路徑：")
    path = os.path.expanduser(path.strip().strip('"').strip("'"))

    print(f"開啟：{path}")
    start = time.perf_counter()
    idx = build_index(path, encoding, thread_re, cont_re)
    report_index(idx, time.perf_counter() - start, args.thread_regex)

    operator, found = ask_operator(idx, args.operator)
    while True:
        selected = ask_threads(idx, found)
        start = time.perf_counter()
        flt = Filtered(idx, selected)
        elapsed = time.perf_counter() - start
        is_cont = idx.is_cont
        cont = sum(is_cont[li] for li in flt.lines)
        names = "、".join(idx.names[t] for t in selected)
        print(rule())
        print(f"操作者：{operator}")
        print(f"Thread：{names}")
        print(f"已篩選：{len(flt.lines):,} 行（含 Stack Trace 等續行 {cont:,} 行），耗時 {elapsed:.2f} 秒")
        print(color("提醒：Thread Pool 會重複使用 Thread ID，以上是整份檔案中這些 Thread 的記錄，"
                    "不保證屬於同一次交易。", "36"))
        print(rule())
        keyword_loop(idx, flt, args)  # 回傳代表要重新選 Thread


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")  # 主控台編碼不支援的字元以 ? 顯示，不中斷
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except QuitApp:
        print("再見。")
        return 0
    except KeyboardInterrupt:
        end_progress()
        print("\n已中斷（Ctrl+C）。")
        return 130
    except LogChangedError as exc:
        print(color(f"錯誤：{exc}", "31"), file=sys.stderr)
        return 3
    except AppError as exc:
        print(color(f"錯誤：{exc}", "31"), file=sys.stderr)
        return 1
    except MemoryError:
        print("錯誤：記憶體不足，無法處理這份 Log。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
