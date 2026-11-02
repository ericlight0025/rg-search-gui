"""RG Search TUI：終端機版介面，沿用 rg_search_gui 的搜尋核心（search_service）。

版面：左邊搜尋條件、右上檔案清單、右下命中預覽。
需要 textual：pip install -e .[tui]
"""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Static

from rg_search_gui.engine_detection import _detect_engine_info
from rg_search_gui.models import SearchFileResult, SearchHit, SearchOptions
from rg_search_gui.search_helpers import _find_match_spans, _split_patterns
from rg_search_gui.search_service import (
    _is_rg_engine,
    _search_with_python_fallback_stream,
    _search_with_rg_stream,
)

PAGE = 100                      # 預覽一頁最多幾筆命中
CONTEXTS = [0, 2, 5, 10, 20]    # 前後文行數選項
FULL_VIEW_RADIUS = 300          # 完整檢視時，命中行上下各讀幾行
LEVEL_RE = re.compile(r"\b(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b")
LEVEL_COLORS = {"FATAL": "#ff5370", "ERROR": "#f7768e", "WARN": "#e0af68", "WARNING": "#e0af68",
                "INFO": "#7dcfff", "DEBUG": "#9ece6a", "TRACE": "#565f89"}
LEVEL_FILTERS = {0: ("全部", None), 1: ("ERROR", {"FATAL", "ERROR"}), 2: ("ERROR+WARN", {"FATAL", "ERROR", "WARN", "WARNING"})}
SORTS = ["命中數", "檔名", "修改時間"]
HISTORY_FILE = Path.home() / ".rg_search_tui" / "history.json"


def _detect_file_encoding(path: Path, encoding: str) -> str:
    """預覽讀檔用的編碼：Auto 時看檔頭，UTF-8 解不開就當 Big5（cp950）。"""
    if encoding.lower() != "auto":
        return "cp950" if encoding.lower() == "big5" else encoding
    try:
        with path.open("rb") as fh:
            head = fh.read(65536)
    except OSError:
        return "utf-8"
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return "utf-16"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # 只是剛好切在多位元組字元中間，仍算 UTF-8。
        if exc.start < len(head) - 3:
            return "cp950"
    return "utf-8"


def _open_text(path: Path, encoding: str):
    return path.open("r", encoding=_detect_file_encoding(path, encoding), errors="replace")


def _level_of(content: str) -> str | None:
    m = LEVEL_RE.search(content[:80])
    return m.group(1) if m else None


def _style_line(content: str, base: str, query: str, case: bool, regex: bool, mark: bool) -> Text:
    body = Text(content, style=base)
    m = LEVEL_RE.search(content[:80])
    if m:
        body.stylize(f"bold {LEVEL_COLORS[m.group(1)]}", m.start(), m.end())
    if mark:
        for s, e in _find_match_spans(content, query, case, regex):
            body.stylize("bold #1a1b26 on #e0af68", s, e)
    return body


# ---------------------------------------------------------------- 小元件
class HistoryInput(Input):
    """搜尋框：↑↓ 叫回以前的搜尋。"""

    BINDINGS = [Binding("up", "older", show=False), Binding("down", "newer", show=False)]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = []
        self._pos = -1

    def action_older(self) -> None:
        if self.history and self._pos < len(self.history) - 1:
            self._pos += 1
            self.value = self.history[self._pos]
            self.cursor_position = len(self.value)

    def action_newer(self) -> None:
        if self._pos > 0:
            self._pos -= 1
            self.value = self.history[self._pos]
        else:
            self._pos = -1
            self.value = ""
        self.cursor_position = len(self.value)

    def remember(self, text: str) -> None:
        if text in self.history:
            self.history.remove(text)
        self.history.insert(0, text)
        del self.history[20:]
        self._pos = -1


class FullView(ModalScreen):
    """以命中行為中心看原始檔，像 less。"""

    BINDINGS = [Binding("escape,q", "dismiss", "返回"), Binding("y", "copy", "複製這段")]
    CSS = """
    FullView { align: center middle; }
    #fv { width: 96%; height: 92%; border: thick #7aa2f7; background: #0c0e12; }
    #fv-title { height: 1; background: #1f2430; color: #7aa2f7; padding: 0 1; }
    #fv-log { background: #0c0e12; }
    """

    def __init__(self, path: Path, line: int, painter, encoding: str = "Auto") -> None:
        super().__init__()
        self._path, self._line, self._paint, self._encoding = path, line, painter, encoding
        self._lines: list[tuple[int, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="fv"):
            yield Static(id="fv-title")
            yield RichLog(id="fv-log", auto_scroll=False, wrap=False)
            yield Footer()

    def on_mount(self) -> None:
        start = max(1, self._line - FULL_VIEW_RADIUS)
        end = self._line + FULL_VIEW_RADIUS
        with _open_text(self._path, self._encoding) as fh:
            for n, raw in enumerate(fh, 1):
                if n >= start:
                    self._lines.append((n, raw.rstrip("\r\n")))
                if n >= end:
                    break
        self.query_one("#fv-title", Static).update(
            f"完整檢視  {self._path.name}  第 {self._line:,} 行（上下各 {FULL_VIEW_RADIUS} 行）   Esc 返回"
        )
        log = self.query_one("#fv-log", RichLog)
        target = 0
        for i, (n, content) in enumerate(self._lines):
            if n == self._line:
                target = i
            log.write(self._paint(n, content, n == self._line, n == self._line))
        self.call_after_refresh(lambda: log.scroll_to(y=max(0, target - log.size.height // 2), animate=False))

    def action_copy(self) -> None:
        text = "\n".join(c for n, c in self._lines if abs(n - self._line) <= 10)
        self.app.copy_to_clipboard(text)
        self.notify("已複製命中行前後 10 行")


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "關閉")]
    CSS = """
    HelpScreen { align: center middle; }
    #help { width: 72; height: auto; border: thick #7aa2f7; background: #161a21; padding: 1 2; }
    """
    KEYS = [
        ("搜尋", [("Enter", "開始搜尋（在搜尋框）"), ("↑ / ↓", "叫回最近 20 次搜尋"), ("Esc", "停止搜尋"),
                ("/", "回到搜尋框"), ("F2 / F3", "切換 Regex / 區分大小寫")]),
        ("瀏覽", [("n / N", "下一筆 / 上一筆命中（跨檔案）"), ("] / [", "下一個 / 上一個檔案"),
                ("+ / -", "前後文 0・2・5・10・20 行"), ("Enter", "完整檢視（以命中行為中心看原檔）"),
                ("s", "檔案排序：命中數・檔名・修改時間"), ("Ctrl+B", "收起 / 展開左側條件")]),
        ("篩選", [("1 / 2 / 0", "只看 ERROR / ERROR+WARN / 全部"),
                ("f", "在結果裡再篩：空白分隔，-字 表示排除")]),
        ("帶走", [("o", "用 VS Code 開到那一行"), ("y", "複製目前命中與前後文"), ("e", "把篩選後的結果存成 .log")]),
    ]

    def compose(self) -> ComposeResult:
        t = Text()
        for group, rows in self.KEYS:
            t.append(f"{group}\n", style="bold #7aa2f7")
            for k, d in rows:
                t.append(f"  {k:<10}", style="bold #e0af68")
                t.append(f"{d}\n", style="#c0caf5")
            t.append("\n")
        t.append("按 Esc 或 ? 關閉", style="#5c6578")
        yield Static(t, id="help")


# ---------------------------------------------------------------- 主程式
class RgSearchTui(App):
    TITLE = "RG Search TUI"
    CSS = """
    Screen { background: #0f1115; }
    #sidebar { width: 36; padding: 0 1; background: #161a21; border-right: tall #2a303b; }
    #sidebar Label { color: #8b95a7; margin-top: 1; }
    #sidebar Input { border: tall #2a303b; background: #0f1115; }
    #sidebar Input:focus { border: tall #7aa2f7; }
    #sidebar Checkbox { background: #161a21; border: none; padding: 0; }
    #engine { color: #5c6578; margin-top: 1; }
    #main { width: 1fr; }
    #filterbar { height: 1; background: #161a21; color: #8b95a7; padding: 0 1; }
    #refine { display: none; border: tall #e0af68; background: #0f1115; height: 3; }
    #files { height: 1fr; max-height: 12; border: tall #2a303b; }
    #files:focus { border: tall #7aa2f7; }
    #preview-title { height: 1; background: #1f2430; color: #7aa2f7; padding: 0 1; }
    #preview { height: 1fr; border: tall #2a303b; background: #0c0e12; }
    #preview:focus { border: tall #7aa2f7; }
    #status { height: 1; background: #1f2430; color: #c0caf5; padding: 0 1; }
    """
    BINDINGS = [
        Binding("n", "next_hit", "下一筆"),
        Binding("N", "prev_hit", "上一筆"),
        Binding("right_square_bracket", "next_file", "下個檔", key_display="]"),
        Binding("left_square_bracket", "prev_file", "上個檔", show=False),
        Binding("f", "refine", "再篩選"),
        Binding("1", "level(1)", "ERROR", show=False),
        Binding("2", "level(2)", "ERROR+WARN", show=False),
        Binding("0", "level(0)", "全部", show=False),
        Binding("plus,equals_sign", "context(1)", "前後文", key_display="+/-"),
        Binding("minus", "context(-1)", "前後文-", show=False),
        Binding("s", "sort", "排序", show=False),
        Binding("o", "open_editor", "開啟"),
        Binding("y", "copy_hit", "複製"),
        Binding("e", "export", "匯出", show=False),
        Binding("escape", "stop", "停止", show=False),
        Binding("slash", "focus_query", "搜尋框", key_display="/"),
        Binding("ctrl+b", "toggle_sidebar", "側欄", show=False),
        Binding("f2", "toggle_regex", "Regex", show=False),
        Binding("f3", "toggle_case", "大小寫", show=False),
        Binding("question_mark", "help", "說明", key_display="?"),
        Binding("ctrl+q", "quit", "離開"),
    ]

    def __init__(
        self,
        folder: str = ".",
        query: str = "",
        include: str = "*.log",
        exclude: str = "*.gz",
        encoding: str = "Auto",
        auto_search: bool = False,
    ) -> None:
        super().__init__()
        self._folder, self._query, self._include, self._exclude = folder, query, include, exclude
        self._encoding = encoding or "Auto"
        self._auto_search = auto_search
        self._results: list[SearchFileResult] = []
        self._view: list[tuple[SearchFileResult, list[SearchHit]]] = []
        self._file_idx = 0
        self._hit_idx = 0
        self._pending_hit: int | None = None
        self._level = 0
        self._refine: list[str] = []
        self._context_i = 1
        self._sort_i = 0
        self._line_cache: tuple[Path | None, dict[int, str]] = (None, {})
        self._stop = threading.Event()
        self._engine = _detect_engine_info()
        self._started = 0.0
        self._options: SearchOptions | None = None
        self._summary = ""
        self._rendered_key: tuple | None = None
        self._searching = False
        self._refresh_pending = False
        self._ignore_highlight = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with VerticalScroll(id="sidebar"):
                yield Label("搜尋文字（↑↓ 歷史）")
                yield HistoryInput(self._query, placeholder="ERROR timeout ...", id="query")
                yield Label("資料夾（多個用 ; 分隔）")
                yield Input(self._folder, id="folders")
                yield Label("包含檔案")
                yield Input(self._include, id="include")
                yield Label("排除檔案")
                yield Input(self._exclude, id="exclude")
                yield Checkbox("Regex  (F2)", id="regex")
                yield Checkbox("區分大小寫  (F3)", id="case")
                yield Checkbox("包含子資料夾", True, id="recursive")
                yield Static(f"引擎：{self._engine_label()}\n編碼：{self._encoding}\n按 ? 看所有快捷鍵", id="engine")
            with Vertical(id="main"):
                yield Static(id="filterbar")
                yield Input(placeholder="再篩選：空白分隔多個字，-字 表示排除（例：timeout -health）", id="refine")
                yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                yield Static(id="preview-title")
                yield RichLog(id="preview", auto_scroll=False, wrap=False, markup=False, highlight=False)
        yield Static("就緒。輸入關鍵字後按 Enter 開始搜尋。", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#files", DataTable)
        table.add_column("檔案", key="file", width=48)
        table.add_column("命中", key="hits", width=8)
        table.add_column("大小", key="size", width=9)
        table.add_column("修改時間", key="mtime", width=16)
        query = self.query_one("#query", HistoryInput)
        try:
            query.history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))[:20]
        except Exception:  # noqa: BLE001
            pass
        query.focus()
        self._update_filterbar()
        if self._auto_search and self._query:
            self.action_search()

    def _use_rg(self) -> bool:
        return bool(self._engine.executable) and _is_rg_engine(self._engine.executable)

    def _engine_label(self) -> str:
        if self._use_rg():
            return f"{self._engine.label} {self._engine.version}".strip()
        return "Python 備援（找不到 rg，大量 log 會很慢）"

    # ---------------------------------------------------------------- 搜尋
    @on(Input.Submitted, "#query")
    def _submit_query(self) -> None:
        self.action_search()

    def action_search(self) -> None:
        query = self.query_one("#query", HistoryInput)
        text = query.value.strip()
        if not text:
            return
        query.remember(text)
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(query.history, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        self._options = SearchOptions(
            folders=[Path(p.strip()).expanduser() for p in self.query_one("#folders", Input).value.split(";") if p.strip()],
            include_patterns=_split_patterns(self.query_one("#include", Input).value),
            exclude_patterns=_split_patterns(self.query_one("#exclude", Input).value),
            text=text,
            recursive=self.query_one("#recursive", Checkbox).value,
            case_sensitive=self.query_one("#case", Checkbox).value,
            use_regex=self.query_one("#regex", Checkbox).value,
            encoding=self._encoding,
            max_file_size_mb=0,
        )
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._file_idx = self._hit_idx = 0
        self._set_status(f"搜尋中… [{text}]")
        self._searching = True
        self._rendered_key = None
        self._run_search(self._options)

    @work(thread=True, exclusive=True)
    def _run_search(self, options: SearchOptions) -> None:
        def emit(results, count, current):
            self.call_from_thread(self._set_results, results, f"搜尋中… {count:,} 筆  {current}")

        try:
            if self._use_rg():
                results = _search_with_rg_stream(
                    self._engine.executable, options, "Matches desc", emit_result=emit, stop_event=self._stop
                )
            else:
                results = _search_with_python_fallback_stream(
                    options, "Matches desc", emit_result=emit, stop_event=self._stop
                )
        except Exception as exc:  # noqa: BLE001
            self._searching = False
            self.call_from_thread(self._set_status, f"錯誤：{exc}")
            return
        total = sum(len(r.hits) for r in results)
        size = sum(r.full_path.stat().st_size for r in results if r.full_path.exists())
        elapsed = time.monotonic() - self._started
        done = "已停止" if self._stop.is_set() else "完成"
        self._searching = False
        self.call_from_thread(
            self._set_results, results,
            f"{done}：{len(results)} 個檔案、{total:,} 筆命中、{size / 1048576:,.1f} MB、{elapsed:.2f} 秒", True,
        )

    # ---------------------------------------------------------------- 篩選與清單
    def _keep(self, content: str) -> bool:
        allowed = LEVEL_FILTERS[self._level][1]
        if allowed is not None and _level_of(content) not in allowed:
            return False
        low = content.lower()
        for token in self._refine:
            if token.startswith("-") and len(token) > 1:
                if token[1:].lower() in low:
                    return False
            elif token.lower() not in low:
                return False
        return True

    def _set_results(self, results: list[SearchFileResult], summary: str, final: bool = False) -> None:
        self._results = results
        self._summary = summary
        if final:
            self._view, self._file_idx, self._hit_idx = [], -1, 0  # 搜完從第一個檔案開始
            self._rendered_key = None
            self._rebuild()
            return
        # 串流中只更新狀態列；檔案清單最多每 0.5 秒重建一次，預覽等搜完再畫。
        self._set_status(summary)
        if not self._refresh_pending:
            self._refresh_pending = True
            self.set_timer(0.5, self._flush_streaming)

    def _flush_streaming(self) -> None:
        self._refresh_pending = False
        if self._searching:
            self._rebuild()

    def _rebuild(self) -> None:
        view = []
        for r in self._results:
            hits = [h for h in r.hits if self._keep(h.content)]
            if hits:
                view.append((r, hits))
        sort = SORTS[self._sort_i]
        if sort == "命中數":
            view.sort(key=lambda v: len(v[1]), reverse=True)
        elif sort == "檔名":
            view.sort(key=lambda v: v[0].relative_path)
        else:
            view.sort(key=lambda v: v[0].full_path.stat().st_mtime if v[0].full_path.exists() else 0, reverse=True)
        current = self._view[self._file_idx][0].full_path if self._view and 0 <= self._file_idx < len(self._view) else None
        self._view = view
        table = self.query_one("#files", DataTable)
        table.clear()
        new_idx = 0
        for i, (r, hits) in enumerate(view):
            try:
                st = r.full_path.stat()
                size, mtime = f"{st.st_size / 1048576:,.1f} MB", datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
            except OSError:
                size = mtime = "-"
            table.add_row(r.relative_path, f"{len(hits):,}", size, mtime, key=str(i))
            if r.full_path == current:
                new_idx = i
        shown = sum(len(h) for _, h in view)
        total = sum(len(r.hits) for r in self._results)
        extra = f"   篩選後 {shown:,} / {total:,} 筆" if shown != total else ""
        self._set_status(self._summary + extra)
        self._update_filterbar()
        if view:
            same = new_idx == self._file_idx
            self._file_idx = new_idx
            self._hit_idx = self._hit_idx if same else 0
            self._ignore_highlight = True
            table.move_cursor(row=new_idx)
            self.call_after_refresh(self._end_ignore)
            self._render_preview()
        else:
            self.query_one("#preview", RichLog).clear()
            self.query_one("#preview-title", Static).update("（沒有符合的命中）")

    def _end_ignore(self) -> None:
        self._ignore_highlight = False

    def _update_filterbar(self) -> None:
        t = Text()
        t.append("層級 ", style="#5c6578")
        for k in (1, 2, 0):
            name = LEVEL_FILTERS[k][0]
            if k == self._level:
                t.append(f" {k}:{name} ", style="bold #1a1b26 on #7aa2f7")
            else:
                t.append(f" {k}:{name} ", style="#8b95a7")
        t.append("   篩選 ", style="#5c6578")
        t.append(" ".join(self._refine) or "（無，按 f）", style="bold #e0af68" if self._refine else "#5c6578")
        t.append("   前後文 ", style="#5c6578")
        t.append(f"±{CONTEXTS[self._context_i]}", style="bold #c0caf5")
        t.append("   排序 ", style="#5c6578")
        t.append(SORTS[self._sort_i], style="bold #c0caf5")
        self.query_one("#filterbar", Static).update(t)

    # ---------------------------------------------------------------- 導覽
    def _goto(self, file_idx: int, hit_idx: int) -> None:
        if not self._view:
            return
        table = self.query_one("#files", DataTable)
        if file_idx != table.cursor_row:
            self._pending_hit = hit_idx
            table.move_cursor(row=file_idx)  # 觸發 RowHighlighted
        else:
            self._file_idx, self._hit_idx = file_idx, hit_idx
            self._render_preview()

    @on(DataTable.RowHighlighted, "#files")
    def _on_file(self, event: DataTable.RowHighlighted) -> None:
        if self._ignore_highlight or event.row_key is None or event.row_key.value is None:
            return
        if int(event.row_key.value) == self._file_idx and self._pending_hit is None:
            return
        self._file_idx = int(event.row_key.value)
        self._hit_idx = self._pending_hit if self._pending_hit is not None else 0
        self._pending_hit = None
        self._render_preview()

    @on(DataTable.RowSelected, "#files")
    def _on_file_enter(self) -> None:
        self.action_full_view()

    def action_next_hit(self) -> None:
        if not self._view:
            return
        if self._hit_idx + 1 < len(self._view[self._file_idx][1]):
            self._goto(self._file_idx, self._hit_idx + 1)
        elif self._file_idx + 1 < len(self._view):
            self._goto(self._file_idx + 1, 0)
        else:
            self.notify("已經是最後一筆", severity="warning")

    def action_prev_hit(self) -> None:
        if not self._view:
            return
        if self._hit_idx > 0:
            self._goto(self._file_idx, self._hit_idx - 1)
        elif self._file_idx > 0:
            self._goto(self._file_idx - 1, len(self._view[self._file_idx - 1][1]) - 1)

    def action_next_file(self) -> None:
        if self._view and self._file_idx + 1 < len(self._view):
            self._goto(self._file_idx + 1, 0)

    def action_prev_file(self) -> None:
        if self._view and self._file_idx > 0:
            self._goto(self._file_idx - 1, 0)

    # ---------------------------------------------------------------- 預覽
    def _paint(self, n: int, content: str, is_hit: bool, is_current: bool) -> Text:
        opts = self._options
        row = Text(style="on #232a3d" if is_current else "")
        if is_current:
            row.append(f"{n:>8} ", style="bold #1a1b26 on #e0af68")
            row.append("▶ ", style="bold #e0af68")
        else:
            row.append(f"{n:>8} ", style="#e0af68" if is_hit else "#4c5566")
            row.append("▌ " if is_hit else "  ", style="#e0af68")
        row.append_text(_style_line(content, "#c0caf5" if is_hit else "#6b7385",
                                    opts.text if opts else "", opts.case_sensitive if opts else False,
                                    opts.use_regex if opts else False, is_hit))
        if is_current:
            row.pad_right(max(0, 400 - len(row)))  # 整行底色延伸到右邊
        return row

    def _read_lines(self, path: Path, wanted: set[int]) -> dict[int, str]:
        cached_path, cache = self._line_cache
        if cached_path != path:
            cache = {}
        missing = wanted - cache.keys()
        if missing:
            last = max(missing)
            with _open_text(path, self._encoding) as fh:
                for n, line in enumerate(fh, 1):
                    if n in missing:
                        cache[n] = line.rstrip("\r\n")
                    if n >= last:
                        break
            if len(cache) > 200_000:
                cache = {k: cache[k] for k in wanted if k in cache}
        self._line_cache = (path, cache)
        return cache

    def _render_preview(self, force: bool = False) -> None:
        if self._searching or self._file_idx < 0 or self._file_idx >= len(self._view):
            return
        result, hits = self._view[self._file_idx]
        self._hit_idx = min(self._hit_idx, len(hits) - 1)
        key = (result.full_path, self._hit_idx // PAGE if not force else None, self._hit_idx, CONTEXTS[self._context_i],
               self._level, tuple(self._refine), self._searching or len(hits))
        if key == self._rendered_key and not force:
            return
        self._rendered_key = key
        page = self._hit_idx // PAGE
        page_hits = hits[page * PAGE:(page + 1) * PAGE]
        ctx = CONTEXTS[self._context_i]
        wanted: dict[int, bool] = {}
        for hit in page_hits:
            for n in range(max(1, hit.line_number - ctx), hit.line_number + ctx + 1):
                wanted.setdefault(n, False)
            wanted[hit.line_number] = True
        lines = self._read_lines(result.full_path, set(wanted))
        current_line = hits[self._hit_idx].line_number
        log = self.query_one("#preview", RichLog)
        log.clear()
        target_y = 0
        prev = 0
        y = 0
        for n in sorted(wanted):
            if prev and n > prev + 1:
                log.write(Text("         ⋯", style="#3b4252"))
                y += 1
            prev = n
            if n == current_line:
                target_y = y
            log.write(self._paint(n, lines.get(n, ""), wanted[n], n == current_line))
            y += 1
        pages = (len(hits) - 1) // PAGE + 1
        title = Text()
        title.append(f"{result.relative_path}", style="bold #7aa2f7")
        title.append(f"   第 {self._hit_idx + 1:,} / {len(hits):,} 筆", style="bold #e0af68")
        title.append(f"   第 {current_line:,} 行", style="#8b95a7")
        if pages > 1:
            title.append(f"   頁 {page + 1}/{pages}", style="#8b95a7")
        title.append("    n/N 上下筆 · Enter 完整檢視", style="#5c6578")
        self.query_one("#preview-title", Static).update(title)
        self.call_after_refresh(lambda: log.scroll_to(y=max(0, target_y - 4), animate=False))

    # ---------------------------------------------------------------- 其他動作
    def _current(self) -> tuple[SearchFileResult, SearchHit] | None:
        if not self._view or self._file_idx < 0:
            return None
        result, hits = self._view[self._file_idx]
        return result, hits[self._hit_idx]

    def action_full_view(self) -> None:
        cur = self._current()
        if cur:
            self.push_screen(FullView(cur[0].full_path, cur[1].line_number, self._paint, self._encoding))

    def on_key(self, event) -> None:
        if event.key == "enter" and self.focused is self.query_one("#preview", RichLog):
            self.action_full_view()

    def action_level(self, level: int) -> None:
        self._level = level
        self._rebuild()

    def action_context(self, step: int) -> None:
        self._context_i = max(0, min(len(CONTEXTS) - 1, self._context_i + step))
        self._update_filterbar()
        if self._current():
            self._render_preview()

    def action_sort(self) -> None:
        self._sort_i = (self._sort_i + 1) % len(SORTS)
        self._rebuild()

    def action_refine(self) -> None:
        box = self.query_one("#refine", Input)
        box.display = True
        box.value = " ".join(self._refine)
        box.focus()

    @on(Input.Submitted, "#refine")
    def _apply_refine(self, event: Input.Submitted) -> None:
        self._refine = event.value.split()
        event.input.display = False
        self.query_one("#files", DataTable).focus()
        self._rebuild()

    def action_stop(self) -> None:
        box = self.query_one("#refine", Input)
        if box.display:
            box.display = False
            self.query_one("#files", DataTable).focus()
            return
        self._stop.set()

    def action_open_editor(self) -> None:
        cur = self._current()
        if not cur:
            return
        code = shutil.which("code")
        if code:
            subprocess.Popen([code, "-g", f"{cur[0].full_path}:{cur[1].line_number}"])
            self.notify(f"已用 VS Code 開啟第 {cur[1].line_number:,} 行")
        elif os.name == "nt":
            os.startfile(cur[0].full_path)  # type: ignore[attr-defined]
        else:
            self.notify("找不到 VS Code（code 指令）", severity="warning")

    def action_copy_hit(self) -> None:
        cur = self._current()
        if not cur:
            return
        ctx = max(2, CONTEXTS[self._context_i])
        n0 = cur[1].line_number
        lines = self._read_lines(cur[0].full_path, set(range(max(1, n0 - ctx), n0 + ctx + 1)))
        text = "\n".join(lines[n] for n in sorted(lines) if abs(n - n0) <= ctx)
        self.copy_to_clipboard(text)
        self.notify(f"已複製第 {n0:,} 行與前後 {ctx} 行")

    def action_export(self) -> None:
        if not self._view:
            return
        out = Path.cwd() / f"rg-search-{datetime.now():%Y%m%d-%H%M%S}.log"
        with out.open("w", encoding="utf-8") as fh:
            for result, hits in self._view:
                for h in hits:
                    fh.write(f"{result.relative_path}:{h.line_number}: {h.content}\n")
        self.notify(f"已匯出 {sum(len(h) for _, h in self._view):,} 筆到 {out}")

    def action_focus_query(self) -> None:
        self.query_one("#query", Input).focus()

    def action_toggle_sidebar(self) -> None:
        bar = self.query_one("#sidebar")
        bar.display = not bar.display

    def action_toggle_regex(self) -> None:
        self.query_one("#regex", Checkbox).toggle()

    def action_toggle_case(self) -> None:
        self.query_one("#case", Checkbox).toggle()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

