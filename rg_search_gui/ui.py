"""Standalone dark-mode ripgrep/grep search GUI."""

from __future__ import annotations

import os
import json
import queue
import re
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from rg_search_gui import __version__
from rg_search_gui.engine_detection import _detect_engine_info
from rg_search_gui.installer_service import install_ripgrep_with_winget
from rg_search_gui.models import ContextLine, EngineInfo, SearchFileResult, SearchHit, SearchOptions
from rg_search_gui.privacy_helpers import display_root_label, redact_path_for_display
from rg_search_gui.search_helpers import (
    _build_context_lines,
    _build_context_lines_from_lines,
    _clone_results,
    _display_file_name,
    _filter_file_results,
    _find_match_spans,
    _find_syntax_spans,
    _matches_exclude,
    _matches_include,
    _parse_positive_int,
    _sorted_results,
    _split_patterns,
    _unique_result_roots,
)
from rg_search_gui.search_service import (
    _is_rg_engine as service_is_rg_engine,
    _search_with_grep_fallback_stream as service_search_with_grep_fallback_stream,
    _search_with_rg_stream as service_search_with_rg_stream,
)
from rg_search_gui.settings_service import _get_settings_path, _load_settings_file, _save_settings_file


DARK_BG = "#0b1017"
DARK_PANEL = "#121924"
DARK_PANEL_ALT = "#192230"
DARK_ENTRY = "#0f1620"
DARK_TABLE = "#0f1722"
DARK_TEXT = "#e6edf3"
ACCENT = "#4fb3ff"
ACCENT_ALT = "#ffb454"
MUTED = "#8da1b9"
BORDER = "#273244"
OBSIDIAN_PREVIEW_BG = "#1e1f22"
OBSIDIAN_PREVIEW_FG = "#d8dee9"
OBSIDIAN_COMMENT = "#7f848e"
OBSIDIAN_STRING = "#98c379"
OBSIDIAN_KEYWORD = "#c678dd"
OBSIDIAN_NUMBER = "#d19a66"
OBSIDIAN_TYPE = "#61afef"
MATCH_BG = "#f6d365"
MATCH_FG = "#000000"
TEXT_EDITOR_SUFFIXES = {
    ".cfg", ".conf", ".csv", ".env", ".ini", ".java", ".js", ".json", ".jsx", ".log", ".md", ".py", ".pyi",
    ".pyw", ".sql", ".text", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
DANGEROUS_OPEN_SUFFIXES = {
    ".appref-ms", ".bat", ".cmd", ".com", ".cpl", ".exe", ".hta", ".ins", ".isp", ".jse", ".lnk",
    ".msc", ".msi", ".msp", ".ps1", ".ps1xml", ".ps2", ".ps2xml", ".psc1", ".psc2", ".py", ".pyw",
    ".reg", ".scf", ".scr", ".sct", ".shb", ".url", ".vb", ".vbe", ".vbs", ".ws", ".wsc", ".wsf", ".wsh",
}

DEFAULT_PREVIEW_THEME = "Obsidian"
PREVIEW_THEMES: dict[str, dict[str, str]] = {
    "Obsidian": {
        "bg": OBSIDIAN_PREVIEW_BG,
        "fg": OBSIDIAN_PREVIEW_FG,
        "line_number": MUTED,
        "selection_bg": ACCENT,
        "selection_fg": "#000000",
        "match_bg": MATCH_BG,
        "match_fg": MATCH_FG,
        "active_match_bg": "#ff9e64",
        "active_match_fg": "#000000",
        "syntax_comment": OBSIDIAN_COMMENT,
        "syntax_string": OBSIDIAN_STRING,
        "syntax_keyword": OBSIDIAN_KEYWORD,
        "syntax_number": OBSIDIAN_NUMBER,
        "syntax_type": OBSIDIAN_TYPE,
    },
    "Monokai": {
        "bg": "#272822",
        "fg": "#f8f8f2",
        "line_number": "#8f908a",
        "selection_bg": "#49483e",
        "selection_fg": "#f8f8f2",
        "match_bg": "#ffd866",
        "match_fg": "#1f1f1f",
        "active_match_bg": "#ff9f43",
        "active_match_fg": "#111111",
        "syntax_comment": "#75715e",
        "syntax_string": "#e6db74",
        "syntax_keyword": "#f92672",
        "syntax_number": "#ae81ff",
        "syntax_type": "#66d9ef",
    },
    "Light": {
        "bg": "#fbfbfb",
        "fg": "#1f2328",
        "line_number": "#7a7f87",
        "selection_bg": "#cce5ff",
        "selection_fg": "#1f2328",
        "match_bg": "#fff1a8",
        "match_fg": "#1f2328",
        "active_match_bg": "#ffcc66",
        "active_match_fg": "#1f2328",
        "syntax_comment": "#6a737d",
        "syntax_string": "#0a7b34",
        "syntax_keyword": "#8250df",
        "syntax_number": "#0550ae",
        "syntax_type": "#953800",
    },
}


def _normalize_preview_theme_name(name: str) -> str:
    return name if name in PREVIEW_THEMES else DEFAULT_PREVIEW_THEME


def apply_dark_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(size=10)
    tkfont.nametofont("TkTextFont").configure(size=10)
    tkfont.nametofont("TkFixedFont").configure(size=11)

    title_font = default_font.copy()
    title_font.configure(size=16, weight="bold")
    section_font = default_font.copy()
    section_font.configure(weight="bold")
    metric_font = default_font.copy()
    metric_font.configure(size=11, weight="bold")
    root._title_font = title_font
    root._section_font = section_font
    root._metric_font = metric_font

    root.configure(bg=DARK_BG)
    root.option_add("*tearOff", False)
    style.configure("TFrame", background=DARK_BG)
    style.configure("Sidebar.TFrame", background=DARK_PANEL)
    style.configure("Hero.TFrame", background=DARK_PANEL_ALT)
    style.configure("Panel.TFrame", background=DARK_PANEL)
    style.configure("Status.TFrame", background=DARK_PANEL_ALT)
    style.configure("TLabel", background=DARK_BG, foreground=DARK_TEXT, font=default_font)
    style.configure("Sidebar.TLabel", background=DARK_PANEL, foreground=DARK_TEXT, font=default_font)
    style.configure("SidebarMuted.TLabel", background=DARK_PANEL, foreground=MUTED, font=default_font)
    style.configure("Hero.TLabel", background=DARK_PANEL_ALT, foreground=DARK_TEXT, font=default_font)
    style.configure("HeroMuted.TLabel", background=DARK_PANEL_ALT, foreground=MUTED, font=default_font)
    style.configure(
        "HeroTitle.TLabel",
        background=DARK_PANEL_ALT,
        foreground=DARK_TEXT,
        font=title_font,
    )
    style.configure(
        "HeroBadge.TLabel",
        background=ACCENT_ALT,
        foreground="#121924",
        font=metric_font,
        padding=(10, 4),
    )
    style.configure("Panel.TLabel", background=DARK_PANEL, foreground=DARK_TEXT, font=default_font)
    style.configure("PanelMuted.TLabel", background=DARK_PANEL, foreground=MUTED, font=default_font)
    style.configure(
        "PanelSection.TLabel",
        background=DARK_PANEL,
        foreground=DARK_TEXT,
        font=section_font,
    )
    style.configure("Status.TLabel", background=DARK_PANEL_ALT, foreground=DARK_TEXT, font=default_font)
    style.configure("StatusMuted.TLabel", background=DARK_PANEL_ALT, foreground=MUTED, font=default_font)
    style.configure(
        "TButton",
        background=DARK_PANEL_ALT,
        foreground=DARK_TEXT,
        borderwidth=0,
        padding=(10, 8),
        focusthickness=1,
        focuscolor=ACCENT,
    )
    style.map(
        "TButton",
        background=[("active", "#233043"), ("pressed", "#131a24"), ("disabled", DARK_PANEL_ALT)],
        foreground=[("active", DARK_TEXT)],
    )
    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground="#081018",
        borderwidth=0,
        padding=(10, 8),
    )
    style.map(
        "Primary.TButton",
        background=[("active", "#7fcbff"), ("pressed", "#2395e8"), ("disabled", "#41546a")],
        foreground=[("active", "#081018"), ("pressed", "#081018"), ("disabled", "#c8d4df")],
    )
    style.configure(
        "Danger.TButton",
        background="#c55d57",
        foreground="#fff7f6",
        borderwidth=0,
        padding=(10, 8),
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#d8746f"), ("pressed", "#9c4440"), ("disabled", "#4a3433")],
        foreground=[("active", "#fff7f6"), ("pressed", "#fff7f6"), ("disabled", "#d6c8c5")],
    )
    style.configure(
        "Tool.TButton",
        background=DARK_PANEL_ALT,
        foreground="#d3e8ff",
        borderwidth=1,
        padding=(10, 8),
    )
    style.map(
        "Tool.TButton",
        background=[("active", "#243040"), ("pressed", "#131a24"), ("disabled", "#1f2530")],
        foreground=[("active", "#eef6ff"), ("pressed", "#d3e8ff"), ("disabled", "#8594a7")],
    )
    style.configure(
        "TEntry",
        fieldbackground=DARK_ENTRY,
        foreground=DARK_TEXT,
        insertcolor=DARK_TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(8, 7),
    )
    style.configure(
        "TCombobox",
        fieldbackground=DARK_ENTRY,
        background=DARK_ENTRY,
        foreground=DARK_TEXT,
        arrowsize=16,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", DARK_ENTRY)],
        selectbackground=[("readonly", DARK_ENTRY)],
        selectforeground=[("readonly", DARK_TEXT)],
    )
    style.configure("TCheckbutton", background=DARK_PANEL, foreground=DARK_TEXT)
    style.map("TCheckbutton", background=[("active", DARK_PANEL)])
    style.configure(
        "TLabelframe",
        background=DARK_PANEL,
        foreground=DARK_TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.configure("TLabelframe.Label", background=DARK_PANEL, foreground=DARK_TEXT, font=section_font)
    style.configure(
        "Panel.TLabelframe",
        background=DARK_PANEL,
        foreground=DARK_TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        relief="flat",
    )
    style.configure(
        "Panel.TLabelframe.Label",
        background=DARK_PANEL,
        foreground=DARK_TEXT,
        font=section_font,
    )
    style.configure("TProgressbar", troughcolor=DARK_PANEL, background=ACCENT)
    style.configure("App.TNotebook", background=DARK_PANEL, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "App.TNotebook",
        background=DARK_PANEL,
        borderwidth=0,
        tabmargins=(0, 0, 0, 0),
        lightcolor=DARK_PANEL,
        darkcolor=DARK_PANEL,
        bordercolor=BORDER,
    )
    style.configure(
        "App.TNotebook.Tab",
        background=DARK_PANEL,
        foreground=MUTED,
        padding=(16, 10),
        borderwidth=0,
        font=section_font,
        lightcolor=DARK_PANEL,
        darkcolor=DARK_PANEL,
        bordercolor=BORDER,
        focuscolor=DARK_PANEL,
    )
    style.map(
        "App.TNotebook.Tab",
        background=[("selected", DARK_PANEL_ALT), ("active", "#223043"), ("!selected", DARK_PANEL)],
        foreground=[("selected", DARK_TEXT), ("active", DARK_TEXT)],
    )
    style.configure(
        "Treeview",
        background=DARK_TABLE,
        fieldbackground=DARK_TABLE,
        foreground=DARK_TEXT,
        borderwidth=1,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
    )
    style.configure(
        "Treeview.Heading",
        background=DARK_PANEL_ALT,
        foreground=DARK_TEXT,
        padding=(8, 8),
        font=section_font,
    )
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#000000")])
    style.configure(
        "Results.Treeview",
        background=DARK_TABLE,
        fieldbackground=DARK_TABLE,
        foreground=DARK_TEXT,
        borderwidth=0,
        rowheight=28,
    )
    style.configure(
        "Results.Treeview.Heading",
        background=DARK_PANEL_ALT,
        foreground=DARK_TEXT,
        padding=(8, 8),
        font=section_font,
    )

    root.option_add("*Text.background", DARK_TABLE)
    root.option_add("*Text.foreground", DARK_TEXT)
    root.option_add("*Text.insertBackground", DARK_TEXT)
    root.option_add("*Listbox.background", DARK_ENTRY)
    root.option_add("*Listbox.foreground", DARK_TEXT)
    root.option_add("*Listbox.selectBackground", ACCENT)
    root.option_add("*Listbox.selectForeground", "#000000")


def _open_path_safely(file_path: Path) -> str | None:
    suffix = file_path.suffix.lower()
    try:
        # 可執行或腳本類型只在檔案總管中定位，避免直接執行。
        if suffix in DANGEROUS_OPEN_SUFFIXES:
            subprocess.Popen(["explorer", f"/select,{file_path}"])
            return "reveal"
        # 常見文字檔改由文字編輯器開啟，避免 Windows 檔案關聯誤觸執行。
        if suffix in TEXT_EDITOR_SUFFIXES:
            subprocess.Popen(["notepad.exe", str(file_path)])
            return "open_text"
        os.startfile(str(file_path))
        return "open"
    except Exception:
        return None


def _create_dark_menu(master: tk.Misc) -> tk.Menu:
    return tk.Menu(
        master,
        tearoff=False,
        bg=DARK_PANEL,
        fg=DARK_TEXT,
        activebackground=ACCENT,
        activeforeground="#081018",
        disabledforeground=MUTED,
        relief="flat",
        borderwidth=0,
    )


class RgSearchApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"RG Search GUI {__version__}")
        self.geometry("1500x920")
        self.minsize(1200, 720)
        apply_dark_theme(self)

        self._folder_paths: list[str] = [str(Path.cwd())]
        self.include_var = tk.StringVar(value="*.*")
        self.exclude_var = tk.StringVar(value=".git;node_modules;__pycache__")
        self.text_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.case_sensitive_var = tk.BooleanVar(value=False)
        self.regex_var = tk.BooleanVar(value=False)
        self.encoding_var = tk.StringVar(value="Auto")
        self.max_file_size_var = tk.StringVar(value="10")
        self.display_lines_var = tk.StringVar(value="7")
        self.font_size_var = tk.StringVar(value="11")
        self.code_theme_var = tk.StringVar(value=DEFAULT_PREVIEW_THEME)
        self.file_filter_var = tk.StringVar()
        self.root_filter_var = tk.StringVar(value="All")
        self.extension_filter_var = tk.StringVar(value="All")
        self.min_hits_var = tk.StringVar(value="1")
        self.sort_var = tk.StringVar(value="Matches desc")
        self.status_var = tk.StringVar(value="等待搜尋")
        self.summary_var = tk.StringVar(value="尚未執行搜尋")
        self.engine_var = tk.StringVar(value="引擎：未檢測")
        self.file_count_var = tk.StringVar(value="檔案：0/0")
        self.preview_info_var = tk.StringVar(value="預覽：尚未選擇檔案")
        self.progress_var = tk.DoubleVar(value=0)
        self.diagnostics_info_var = tk.StringVar(value="尚未顯示診斷資訊")

        self._ui_task_queue: queue.Queue[tuple[str, tuple[object, ...]]] = queue.Queue()
        self._stop_event = threading.Event()
        self._search_thread: threading.Thread | None = None
        self._active_process: subprocess.Popen[str] | None = None
        self._all_results: list[SearchFileResult] = []
        self._result_by_path: dict[str, SearchFileResult] = {}
        self._current_context_lines: list[ContextLine] = []
        self._cached_file_lines: OrderedDict[str, tuple[int, int, list[str]]] = OrderedDict()
        self._preview_hit_ranges: list[tuple[str, str]] = []
        self._active_hit_index: int = -1
        self._style = ttk.Style(self)
        self._table_font = tkfont.nametofont("TkDefaultFont").copy()
        self._preview_font = tkfont.nametofont("TkFixedFont").copy()
        self._engine_info = EngineInfo(executable=None, label="未檢測")
        self._install_log_window: tk.Toplevel | None = None
        self._install_log_text: tk.Text | None = None
        self._settings_panel_visible = False

        self._load_settings()
        self._build_menu()
        self._build_ui()
        self.file_filter_var.trace_add("write", lambda *_args: self._refresh_file_tree())
        self.root_filter_var.trace_add("write", lambda *_args: self._refresh_file_tree())
        self.extension_filter_var.trace_add("write", lambda *_args: self._refresh_file_tree())
        self.min_hits_var.trace_add("write", lambda *_args: self._refresh_file_tree())
        self.sort_var.trace_add("write", lambda *_args: self._refresh_file_tree())
        self.display_lines_var.trace_add("write", lambda *_args: self._refresh_preview())
        self.font_size_var.trace_add("write", lambda *_args: self._apply_font_size())
        self.code_theme_var.trace_add("write", lambda *_args: self._on_code_theme_changed())
        self._apply_font_size()
        self._apply_preview_theme()
        self._refresh_engine_info()
        self.bind("<F3>", lambda _event: self._focus_next_hit())
        self.bind("<Shift-F3>", lambda _event: self._focus_previous_hit())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._drain_ui_task_queue)

    def _queue_ui_action(self, action: str, *payload: object) -> None:
        self._ui_task_queue.put((action, payload))

    def _drain_ui_task_queue(self) -> None:
        while True:
            try:
                action, payload = self._ui_task_queue.get_nowait()
            except queue.Empty:
                break

            if action == "stream_results":
                self._apply_stream_results(*payload)
            elif action == "apply_results":
                self._apply_results(*payload)
            elif action == "show_error":
                self._show_search_error(*payload)
            elif action == "finish_search":
                self._finish_search(*payload)
            elif action == "install_done":
                self._finish_install_rg(*payload)
            elif action == "install_log":
                self._append_install_log(*payload)

        if self.winfo_exists():
            self.after(50, self._drain_ui_task_queue)

    def _finish_install_rg(self, success: bool, message: str) -> None:
        if hasattr(self, "install_rg_button"):
            self.install_rg_button.config(state="normal")
        if success:
            self._refresh_engine_info()
            self.status_var.set("安裝完成")
            self.summary_var.set(message)
        else:
            self.status_var.set("安裝失敗")
            self.summary_var.set(message)

    def _ensure_install_log_window(self) -> None:
        if self._install_log_window and self._install_log_window.winfo_exists() and self._install_log_text is not None:
            return

        window = tk.Toplevel(self)
        window.title("安裝日誌")
        window.geometry("900x420")
        window.configure(bg=DARK_BG)
        window.transient(self)

        frame = ttk.Frame(window, padding=8)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap="none",
            relief="flat",
            borderwidth=1,
            background=OBSIDIAN_PREVIEW_BG,
            foreground=OBSIDIAN_PREVIEW_FG,
            insertbackground=DARK_TEXT,
        )
        ysb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        self._install_log_window = window
        self._install_log_text = text
        self._append_install_log("=== Install rg log ===")

    def _append_install_log(self, line: str) -> None:
        self._ensure_install_log_window()
        if self._install_log_text is None:
            return
        self._install_log_text.insert("end", f"{line}\n")
        self._install_log_text.see("end")

    def _build_menu(self) -> None:
        menubar = _create_dark_menu(self)
        settings_menu = _create_dark_menu(menubar)
        settings_menu.add_command(label="開啟設定頁籤", command=self._open_settings_dialog)
        settings_menu.add_separator()
        settings_menu.add_command(label="診斷資訊", command=self._show_diagnostics)
        settings_menu.add_command(label="安裝 ripgrep (rg)", command=self.install_rg)
        menubar.add_cascade(label="設定", menu=settings_menu)
        self.config(menu=menubar)

    def _open_settings_dialog(self) -> None:
        self._toggle_settings_panel()

    def _toggle_settings_panel(self) -> None:
        self._set_settings_panel_visible(not self._settings_panel_visible)

    def _set_settings_panel_visible(self, visible: bool) -> None:
        if not hasattr(self, "sidebar_notebook") or not hasattr(self, "settings_panel"):
            return
        self._refresh_diagnostics_info()
        if visible:
            self.sidebar_notebook.select(self.settings_panel)
            self._settings_panel_visible = True
            if hasattr(self, "settings_recursive_check"):
                self.settings_recursive_check.focus_set()
            return
        if hasattr(self, "search_panel"):
            self.sidebar_notebook.select(self.search_panel)
        self._settings_panel_visible = False

    def _on_sidebar_tab_changed(self, _event: tk.Event) -> None:
        if not hasattr(self, "sidebar_notebook") or not hasattr(self, "settings_panel"):
            return
        self._settings_panel_visible = self.sidebar_notebook.select() == str(self.settings_panel)
        if self._settings_panel_visible:
            self._refresh_diagnostics_info()

    def _build_diagnostics_lines(self) -> list[str]:
        return [
            f"App Version: {__version__}",
            f"Engine: {self._engine_info.label}",
            f"Executable: {redact_path_for_display(self._engine_info.executable)}",
            f"Version: {self._engine_info.version or 'unknown'}",
            f"Settings file: {redact_path_for_display(_get_settings_path())}",
            f"Folders: {len(self._folder_paths)}",
            f"Code Theme: {self.code_theme_var.get()}",
        ]

    def _refresh_diagnostics_info(self) -> None:
        self.diagnostics_info_var.set("\n".join(self._build_diagnostics_lines()))

    def _show_diagnostics(self) -> None:
        self._refresh_diagnostics_info()
        self._set_settings_panel_visible(True)

    def _refresh_engine_info(self) -> None:
        self._engine_info = _detect_engine_info()
        if self._engine_info.executable:
            if self._engine_info.version:
                self.engine_var.set(f"引擎：{self._engine_info.label} {self._engine_info.version}")
            else:
                self.engine_var.set(f"引擎：{self._engine_info.label}")
        else:
            self.engine_var.set("引擎：不可用")
        self._refresh_diagnostics_info()

    def _load_settings(self) -> None:
        settings = _load_settings_file()
        self._folder_paths = settings.get("folders") or self._folder_paths
        self.include_var.set(settings.get("include", self.include_var.get()))
        self.exclude_var.set(settings.get("exclude", self.exclude_var.get()))
        self.recursive_var.set(settings.get("recursive", self.recursive_var.get()))
        self.case_sensitive_var.set(settings.get("case_sensitive", self.case_sensitive_var.get()))
        self.regex_var.set(settings.get("use_regex", self.regex_var.get()))
        self.encoding_var.set(settings.get("encoding", self.encoding_var.get()))
        self.max_file_size_var.set(str(settings.get("max_file_size_mb", self.max_file_size_var.get())))
        self.display_lines_var.set(str(settings.get("display_lines", self.display_lines_var.get())))
        self.font_size_var.set(str(settings.get("font_size", self.font_size_var.get())))
        self.code_theme_var.set(_normalize_preview_theme_name(str(settings.get("code_theme", self.code_theme_var.get()))))
        self.file_filter_var.set(settings.get("file_filter", self.file_filter_var.get()))
        self.root_filter_var.set(settings.get("root_filter", self.root_filter_var.get()))
        self.extension_filter_var.set(settings.get("extension_filter", self.extension_filter_var.get()))
        self.min_hits_var.set(str(settings.get("min_hits", self.min_hits_var.get())))
        self.sort_var.set(settings.get("sort", self.sort_var.get()))

    def _save_settings(self) -> None:
        payload = {
            "folders": self._folder_paths,
            "include": self.include_var.get(),
            "exclude": self.exclude_var.get(),
            "recursive": self.recursive_var.get(),
            "case_sensitive": self.case_sensitive_var.get(),
            "use_regex": self.regex_var.get(),
            "encoding": self.encoding_var.get(),
            "max_file_size_mb": self.max_file_size_var.get(),
            "display_lines": self.display_lines_var.get(),
            "font_size": self.font_size_var.get(),
            "code_theme": self.code_theme_var.get(),
            "file_filter": self.file_filter_var.get(),
            "root_filter": self.root_filter_var.get(),
            "extension_filter": self.extension_filter_var.get(),
            "min_hits": self.min_hits_var.get(),
            "sort": self.sort_var.get(),
        }
        _save_settings_file(payload)

    def _get_preview_theme(self) -> dict[str, str]:
        theme_name = _normalize_preview_theme_name(self.code_theme_var.get())
        return PREVIEW_THEMES[theme_name]

    def _apply_preview_theme(self) -> None:
        if not hasattr(self, "line_preview"):
            return
        theme = self._get_preview_theme()
        self.line_preview.configure(
            background=theme["bg"],
            foreground=theme["fg"],
            insertbackground=theme["fg"],
            selectbackground=theme["selection_bg"],
            selectforeground=theme["selection_fg"],
        )
        self.line_preview.tag_configure("line_number", foreground=theme["line_number"])
        self.line_preview.tag_configure("match", background=theme["match_bg"], foreground=theme["match_fg"])
        self.line_preview.tag_configure(
            "active_match",
            background=theme["active_match_bg"],
            foreground=theme["active_match_fg"],
        )
        self.line_preview.tag_configure("syntax_comment", foreground=theme["syntax_comment"])
        self.line_preview.tag_configure("syntax_string", foreground=theme["syntax_string"])
        self.line_preview.tag_configure("syntax_keyword", foreground=theme["syntax_keyword"])
        self.line_preview.tag_configure("syntax_number", foreground=theme["syntax_number"])
        self.line_preview.tag_configure("syntax_type", foreground=theme["syntax_type"])

    def _on_code_theme_changed(self) -> None:
        normalized = _normalize_preview_theme_name(self.code_theme_var.get())
        if normalized != self.code_theme_var.get():
            self.code_theme_var.set(normalized)
            return
        self._apply_preview_theme()
        self._refresh_preview()
        self._refresh_diagnostics_info()
        self._save_settings()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        workspace = ttk.Frame(outer)
        workspace.grid(row=0, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)

        shell_paned = tk.PanedWindow(
            workspace,
            orient=tk.HORIZONTAL,
            bg=DARK_BG,
            bd=0,
            sashrelief=tk.FLAT,
            sashwidth=6,
            opaqueresize=True,
        )
        shell_paned.grid(row=0, column=0, sticky="nsew")

        self._build_sidebar_area(shell_paned)
        self._build_results_area(shell_paned)
        self._build_status_bar(outer)

        self._refresh_diagnostics_info()
        self.sidebar_notebook.select(self.search_panel)

    def _build_sidebar_area(self, shell_paned: tk.PanedWindow) -> None:
        sidebar = ttk.Frame(shell_paned, style="Sidebar.TFrame", padding=10)
        sidebar.configure(width=420)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        shell_paned.add(sidebar, minsize=360)

        hero = ttk.Frame(sidebar, style="Hero.TFrame", padding=(18, 16))
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        ttk.Label(hero, text="Workspace Search", style="HeroTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            hero,
            text="左側定義查詢，右側快速瀏覽檔案與內容。",
            style="HeroMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(hero, textvariable=self.engine_var, style="HeroBadge.TLabel").grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(12, 0),
        )

        self.sidebar_notebook = ttk.Notebook(sidebar, style="App.TNotebook")
        self.sidebar_notebook.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.search_panel = ttk.Frame(self.sidebar_notebook, style="Sidebar.TFrame", padding=14)
        self.settings_panel = ttk.Frame(self.sidebar_notebook, style="Sidebar.TFrame", padding=14)
        self.sidebar_notebook.add(self.search_panel, text="搜尋")
        self.sidebar_notebook.add(self.settings_panel, text="設定")
        self.sidebar_notebook.bind("<<NotebookTabChanged>>", self._on_sidebar_tab_changed)

        self._build_search_tab()
        self._build_settings_tab()

    def _build_search_tab(self) -> None:
        self.search_panel.columnconfigure(0, weight=1)
        self.search_panel.rowconfigure(1, weight=1)

        query_card = ttk.LabelFrame(self.search_panel, text="查詢", padding=12, style="Panel.TLabelframe")
        query_card.grid(row=0, column=0, sticky="ew")
        query_card.columnconfigure(0, weight=1)
        ttk.Label(query_card, text="搜尋文字", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")
        self.search_entry = ttk.Entry(query_card, textvariable=self.text_var)
        self.search_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.search_entry.bind("<Return>", lambda _event: self.start_search())
        ttk.Label(
            query_card,
            text="支援一般關鍵字與正規表示式，按 Enter 可直接開始搜尋。",
            style="PanelMuted.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        scope_card = ttk.LabelFrame(self.search_panel, text="搜尋範圍", padding=12, style="Panel.TLabelframe")
        scope_card.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        scope_card.columnconfigure(0, weight=1)
        scope_card.rowconfigure(2, weight=1)
        ttk.Label(scope_card, text="根目錄", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w")

        folder_buttons = ttk.Frame(scope_card, style="Panel.TFrame")
        folder_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 10))
        folder_buttons.columnconfigure(0, weight=1)
        folder_buttons.columnconfigure(1, weight=1)
        ttk.Button(folder_buttons, text="新增資料夾", command=self._browse_folder).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 6),
            pady=(0, 6),
        )
        ttk.Button(folder_buttons, text="加入目前目錄", command=self._add_cwd_folder).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(0, 6),
        )
        ttk.Button(folder_buttons, text="上移", command=self._move_selected_folder_up).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 6),
            pady=(0, 6),
        )
        ttk.Button(folder_buttons, text="下移", command=self._move_selected_folder_down).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(0, 6),
        )
        ttk.Button(folder_buttons, text="移除", command=self._remove_selected_folders).grid(
            row=2,
            column=0,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(folder_buttons, text="全部清空", command=self._clear_folders).grid(row=2, column=1, sticky="ew")

        folder_frame = ttk.Frame(scope_card, style="Panel.TFrame")
        folder_frame.grid(row=2, column=0, sticky="nsew")
        folder_frame.columnconfigure(0, weight=1)
        folder_frame.rowconfigure(0, weight=1)
        self.folder_listbox = tk.Listbox(
            folder_frame,
            height=8,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            relief="flat",
            borderwidth=0,
        )
        folder_vsb = ttk.Scrollbar(folder_frame, orient="vertical", command=self.folder_listbox.yview)
        self.folder_listbox.configure(yscrollcommand=folder_vsb.set)
        self.folder_listbox.grid(row=0, column=0, sticky="nsew")
        folder_vsb.grid(row=0, column=1, sticky="ns")
        self._refresh_folder_listbox()
        self.folder_listbox.bind("<Button-3>", self._show_folder_context_menu)

        ttk.Label(scope_card, text="包含檔案", style="PanelMuted.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(scope_card, textvariable=self.include_var).grid(row=4, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(scope_card, text="排除檔案", style="PanelMuted.TLabel").grid(row=5, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(scope_card, textvariable=self.exclude_var).grid(row=6, column=0, sticky="ew", pady=(6, 0))

        actions_card = ttk.LabelFrame(self.search_panel, text="操作", padding=12, style="Panel.TLabelframe")
        actions_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions_card.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(
            actions_card,
            text="開始搜尋",
            command=self.start_search,
            style="Primary.TButton",
        )
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.cancel_button = ttk.Button(
            actions_card,
            text="取消搜尋",
            command=self.cancel_search,
            state="disabled",
            style="Danger.TButton",
        )
        self.cancel_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.install_rg_button = ttk.Button(
            actions_card,
            text="安裝 rg",
            command=self.install_rg,
            style="Tool.TButton",
        )
        self.install_rg_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions_card, text="清除結果", command=self._clear_results).grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Label(actions_card, textvariable=self.status_var, style="Panel.TLabel").grid(
            row=4,
            column=0,
            sticky="w",
            pady=(12, 0),
        )
        ttk.Label(
            actions_card,
            textvariable=self.summary_var,
            style="PanelMuted.TLabel",
            justify="left",
            wraplength=320,
        ).grid(row=5, column=0, sticky="w", pady=(4, 0))

    def _build_settings_tab(self) -> None:
        self.settings_panel.columnconfigure(0, weight=1)
        self.settings_panel.rowconfigure(2, weight=1)

        behavior_card = ttk.LabelFrame(self.settings_panel, text="搜尋設定", padding=12, style="Panel.TLabelframe")
        behavior_card.grid(row=0, column=0, sticky="ew")
        behavior_card.columnconfigure(0, weight=1)
        behavior_card.columnconfigure(1, weight=1)
        self.settings_recursive_check = ttk.Checkbutton(behavior_card, text="遞迴搜尋", variable=self.recursive_var)
        self.settings_recursive_check.grid(row=0, column=0, sticky="w", pady=(0, 6), padx=(0, 12))
        ttk.Checkbutton(behavior_card, text="大小寫敏感", variable=self.case_sensitive_var).grid(
            row=0,
            column=1,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Checkbutton(behavior_card, text="使用正規表示式", variable=self.regex_var).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Label(behavior_card, text="檔案編碼", style="PanelMuted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 4))
        ttk.Label(behavior_card, text="最大檔案大小 (MB)", style="PanelMuted.TLabel").grid(row=2, column=1, sticky="w", pady=(8, 4))
        ttk.Combobox(
            behavior_card,
            textvariable=self.encoding_var,
            values=["Auto", "utf-8", "cp950", "big5", "utf-16"],
            state="readonly",
        ).grid(row=3, column=0, sticky="ew", padx=(0, 12))
        ttk.Combobox(
            behavior_card,
            textvariable=self.max_file_size_var,
            values=["1", "5", "10", "20", "50", "100"],
            state="readonly",
        ).grid(row=3, column=1, sticky="ew")

        preview_card = ttk.LabelFrame(self.settings_panel, text="預覽設定", padding=12, style="Panel.TLabelframe")
        preview_card.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        preview_card.columnconfigure(0, weight=1)
        preview_card.columnconfigure(1, weight=1)
        ttk.Label(preview_card, text="顯示行數", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(preview_card, text="字體大小", style="PanelMuted.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 4))
        ttk.Combobox(
            preview_card,
            textvariable=self.display_lines_var,
            values=["1", "3", "5", "7", "9", "11", "15", "21"],
        ).grid(row=1, column=0, sticky="ew", padx=(0, 12))
        ttk.Combobox(
            preview_card,
            textvariable=self.font_size_var,
            values=["9", "10", "11", "12", "14", "16", "18", "20"],
        ).grid(row=1, column=1, sticky="ew")
        ttk.Label(preview_card, text="程式碼主題", style="PanelMuted.TLabel").grid(
            row=2,
            column=0,
            sticky="w",
            pady=(10, 4),
        )
        ttk.Combobox(
            preview_card,
            textvariable=self.code_theme_var,
            values=list(PREVIEW_THEMES.keys()),
            state="readonly",
        ).grid(row=3, column=0, columnspan=2, sticky="ew")

        diagnostics_card = ttk.LabelFrame(self.settings_panel, text="診斷資訊", padding=12, style="Panel.TLabelframe")
        diagnostics_card.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        diagnostics_card.columnconfigure(0, weight=1)
        diagnostics_card.rowconfigure(1, weight=1)
        ttk.Label(
            diagnostics_card,
            text="這裡只顯示狀態資訊，不更動搜尋邏輯。",
            style="PanelMuted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            diagnostics_card,
            textvariable=self.diagnostics_info_var,
            justify="left",
            style="PanelMuted.TLabel",
            wraplength=320,
        ).grid(row=1, column=0, sticky="nw", pady=(8, 0))
        diagnostics_actions = ttk.Frame(diagnostics_card, style="Panel.TFrame")
        diagnostics_actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        diagnostics_actions.columnconfigure(0, weight=1)
        diagnostics_actions.columnconfigure(1, weight=1)
        ttk.Button(diagnostics_actions, text="回到搜尋", command=self._toggle_settings_panel).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 6),
        )
        ttk.Button(
            diagnostics_actions,
            text="安裝 rg",
            command=self.install_rg,
            style="Tool.TButton",
        ).grid(row=0, column=1, sticky="ew")

    def _build_results_area(self, shell_paned: tk.PanedWindow) -> None:
        content = ttk.Frame(shell_paned)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        shell_paned.add(content, minsize=760)

        content_paned = tk.PanedWindow(
            content,
            orient=tk.VERTICAL,
            bg=DARK_BG,
            bd=0,
            sashrelief=tk.FLAT,
            sashwidth=6,
            opaqueresize=True,
        )
        content_paned.grid(row=0, column=0, sticky="nsew", padx=(10, 0))

        self._build_file_list_panel(content_paned)
        self._build_preview_panel(content_paned)

    def _build_status_bar(self, outer: ttk.Frame) -> None:
        status_frame = ttk.Frame(outer, style="Status.TFrame", padding=(14, 10))
        status_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        status_frame.columnconfigure(1, weight=1)
        status_frame.columnconfigure(2, weight=1)
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 12),
        )
        ttk.Label(status_frame, textvariable=self.summary_var, style="StatusMuted.TLabel").grid(
            row=0,
            column=1,
            sticky="w",
        )
        ttk.Label(status_frame, textvariable=self.engine_var, style="StatusMuted.TLabel").grid(
            row=0,
            column=2,
            sticky="e",
        )
        self.progress = ttk.Progressbar(status_frame, variable=self.progress_var, mode="determinate")
        self.progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

    def _build_file_list_panel(self, content_paned: tk.PanedWindow) -> None:
        file_panel = ttk.LabelFrame(content_paned, text="檔案列表", padding=12, style="Panel.TLabelframe")
        content_paned.add(file_panel, minsize=280)
        file_panel.columnconfigure(0, weight=1)
        file_panel.rowconfigure(1, weight=1)

        file_filter_frame = ttk.Frame(file_panel, style="Panel.TFrame")
        file_filter_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        file_filter_frame.columnconfigure(1, weight=1)
        file_filter_frame.columnconfigure(3, weight=1)
        file_filter_frame.columnconfigure(5, weight=1)
        ttk.Label(file_filter_frame, text="篩選", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(file_filter_frame, textvariable=self.file_filter_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(file_filter_frame, text="根目錄", style="PanelMuted.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.root_filter_combo = ttk.Combobox(
            file_filter_frame,
            textvariable=self.root_filter_var,
            state="readonly",
        )
        self.root_filter_combo.grid(row=0, column=3, sticky="ew")
        ttk.Label(file_filter_frame, textvariable=self.file_count_var, style="PanelMuted.TLabel").grid(
            row=0,
            column=5,
            sticky="e",
        )
        ttk.Label(file_filter_frame, text="副檔名", style="PanelMuted.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 6),
            pady=(8, 0),
        )
        self.extension_filter_combo = ttk.Combobox(
            file_filter_frame,
            textvariable=self.extension_filter_var,
            state="readonly",
        )
        self.extension_filter_combo.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(file_filter_frame, text="最少命中", style="PanelMuted.TLabel").grid(
            row=1,
            column=2,
            sticky="w",
            padx=(12, 6),
            pady=(8, 0),
        )
        ttk.Combobox(
            file_filter_frame,
            textvariable=self.min_hits_var,
            values=["1", "2", "3", "5", "10", "20"],
            state="readonly",
        ).grid(row=1, column=3, sticky="ew", pady=(8, 0))
        ttk.Label(file_filter_frame, text="排序", style="PanelMuted.TLabel").grid(
            row=1,
            column=4,
            sticky="w",
            padx=(12, 6),
            pady=(8, 0),
        )
        ttk.Combobox(
            file_filter_frame,
            textvariable=self.sort_var,
            values=["Matches desc", "Matches asc", "Name asc", "Name desc", "Root asc"],
            state="readonly",
        ).grid(row=1, column=5, sticky="ew", pady=(8, 0))

        self.file_tree = ttk.Treeview(file_panel, columns=("filename", "matches"), show="headings", style="Results.Treeview")
        self.file_tree.heading("filename", text="檔案")
        self.file_tree.heading("matches", text="命中數")
        self.file_tree.column("filename", width=420, anchor="w")
        self.file_tree.column("matches", width=100, anchor="center")
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_selected)

        file_vsb = ttk.Scrollbar(file_panel, orient="vertical", command=self.file_tree.yview)
        file_hsb = ttk.Scrollbar(file_panel, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(yscroll=file_vsb.set, xscroll=file_hsb.set)
        self.file_tree.grid(row=1, column=0, sticky="nsew")
        file_vsb.grid(row=1, column=1, sticky="ns")
        file_hsb.grid(row=2, column=0, sticky="ew")

    def _build_preview_panel(self, content_paned: tk.PanedWindow) -> None:
        preview_panel = ttk.LabelFrame(content_paned, text="內容預覽", padding=12, style="Panel.TLabelframe")
        content_paned.add(preview_panel, minsize=340)
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(1, weight=1)

        preview_header = ttk.Frame(preview_panel, style="Panel.TFrame")
        preview_header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        preview_header.columnconfigure(0, weight=1)
        ttk.Label(preview_header, textvariable=self.preview_info_var, style="PanelMuted.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        preview_actions = ttk.Frame(preview_header, style="Panel.TFrame")
        preview_actions.grid(row=0, column=1, sticky="e")
        ttk.Button(preview_actions, text="上一筆", command=self._focus_previous_hit, style="Tool.TButton").pack(
            side="left",
            padx=(0, 6),
        )
        ttk.Button(preview_actions, text="下一筆", command=self._focus_next_hit, style="Tool.TButton").pack(
            side="left",
            padx=(0, 6),
        )
        ttk.Button(preview_actions, text="開啟檔案", command=self._open_current_file, style="Tool.TButton").pack(side="left")

        preview_frame = ttk.Frame(preview_panel, style="Panel.TFrame")
        preview_frame.grid(row=1, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.line_preview = tk.Text(
            preview_frame,
            wrap="none",
            relief="flat",
            borderwidth=0,
            background=OBSIDIAN_PREVIEW_BG,
            foreground=OBSIDIAN_PREVIEW_FG,
            insertbackground=DARK_TEXT,
            selectbackground=ACCENT,
            selectforeground="#000000",
        )
        preview_vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.line_preview.yview)
        preview_hsb = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.line_preview.xview)
        self.line_preview.configure(yscrollcommand=preview_vsb.set, xscrollcommand=preview_hsb.set)
        self.line_preview.grid(row=0, column=0, sticky="nsew")
        preview_vsb.grid(row=0, column=1, sticky="ns")
        preview_hsb.grid(row=1, column=0, sticky="ew")
        self.line_preview.bind("<Double-1>", lambda _event: self._open_current_file())
        self._apply_preview_theme()
        self.line_preview.configure(state="disabled")

    def _browse_folder(self) -> None:
        initial_dir = self._folder_paths[0] if self._folder_paths else str(Path.cwd())
        folder = filedialog.askdirectory(initialdir=initial_dir)
        if folder:
            self._add_folder(folder)

    def _add_folder(self, folder: str) -> None:
        normalized = str(Path(folder))
        if normalized not in self._folder_paths:
            self._folder_paths.append(normalized)
            self._refresh_folder_listbox()
            self._save_settings()

    def _add_cwd_folder(self) -> None:
        self._add_folder(str(Path.cwd()))

    def _remove_selected_folders(self) -> None:
        if not hasattr(self, "folder_listbox"):
            return
        selected_indexes = list(self.folder_listbox.curselection())
        if not selected_indexes:
            return
        remaining = [folder for index, folder in enumerate(self._folder_paths) if index not in selected_indexes]
        self._folder_paths = remaining
        self._refresh_folder_listbox()
        self._save_settings()

    def _move_selected_folder_up(self) -> None:
        selection = list(self.folder_listbox.curselection()) if hasattr(self, "folder_listbox") else []
        if len(selection) != 1 or selection[0] == 0:
            return
        index = selection[0]
        self._folder_paths[index - 1], self._folder_paths[index] = self._folder_paths[index], self._folder_paths[index - 1]
        self._refresh_folder_listbox(select_indexes=[index - 1])
        self._save_settings()

    def _move_selected_folder_down(self) -> None:
        selection = list(self.folder_listbox.curselection()) if hasattr(self, "folder_listbox") else []
        if len(selection) != 1 or selection[0] >= len(self._folder_paths) - 1:
            return
        index = selection[0]
        self._folder_paths[index + 1], self._folder_paths[index] = self._folder_paths[index], self._folder_paths[index + 1]
        self._refresh_folder_listbox(select_indexes=[index + 1])
        self._save_settings()

    def _show_folder_context_menu(self, event: tk.Event) -> None:
        if not hasattr(self, "folder_listbox"):
            return
        nearest = self.folder_listbox.nearest(event.y)
        if nearest >= 0:
            self.folder_listbox.selection_clear(0, "end")
            self.folder_listbox.selection_set(nearest)
        menu = _create_dark_menu(self)
        menu.add_command(label="Move Up", command=self._move_selected_folder_up)
        menu.add_command(label="Move Down", command=self._move_selected_folder_down)
        menu.add_separator()
        menu.add_command(label="Remove", command=self._remove_selected_folders)
        menu.tk_popup(event.x_root, event.y_root)

    def _clear_folders(self) -> None:
        self._folder_paths = []
        self._refresh_folder_listbox()
        self._save_settings()

    def _refresh_folder_listbox(self, select_indexes: list[int] | None = None) -> None:
        if not hasattr(self, "folder_listbox"):
            return
        self.folder_listbox.delete(0, "end")
        for folder in self._folder_paths:
            self.folder_listbox.insert("end", redact_path_for_display(folder))
        if select_indexes:
            for index in select_indexes:
                if 0 <= index < len(self._folder_paths):
                    self.folder_listbox.selection_set(index)
        self._refresh_diagnostics_info()

    def _clear_results(self) -> None:
        if self._search_thread and self._search_thread.is_alive():
            return
        self._all_results = []
        self._result_by_path.clear()
        self._current_context_lines = []
        self._refresh_file_tree()
        self._clear_preview()
        self.progress_var.set(0)
        self.status_var.set("等待搜尋")
        self.summary_var.set("尚未執行搜尋")

    def start_search(self) -> None:
        if self._search_thread and self._search_thread.is_alive():
            return
        try:
            options = self._build_options()
        except ValueError as exc:
            self.status_var.set(f"設定錯誤：{exc}")
            self.summary_var.set("請修正搜尋條件")
            self.engine_var.set("引擎：未啟動")
            return

        self._refresh_engine_info()
        engine = self._engine_info.executable
        if engine is None:
            self.status_var.set("找不到 rg 或 grep")
            self.summary_var.set("請先安裝 ripgrep (rg) 或 grep，或按右側『安裝 rg』")
            self.engine_var.set("引擎：不可用")
            should_install = messagebox.askyesno(
                "找不到 rg",
                "目前查不到 rg（也找不到可用 grep）。\n要現在安裝 ripgrep (rg) 嗎？",
                parent=self,
            )
            if should_install:
                self.install_rg(skip_confirm=True)
            return

        self._clear_results()
        self._save_settings()
        self._stop_event.clear()
        self.start_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.engine_var.set(f"引擎：{self._engine_info.label} {self._engine_info.version}".strip())
        self.status_var.set("搜尋中")
        self.summary_var.set("正在串流接收結果")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._search_thread = threading.Thread(target=self._search_worker, args=(options, engine), daemon=True)
        self._search_thread.start()

    def cancel_search(self) -> None:
        if not self._search_thread or not self._search_thread.is_alive():
            return
        self._stop_event.set()
        if self._active_process is not None:
            try:
                self._active_process.terminate()
            except Exception:
                pass
        self.status_var.set("取消中")
        self.summary_var.set("等待目前搜尋程序停止")

    def install_rg(self, skip_confirm: bool = False) -> None:
        if self._search_thread and self._search_thread.is_alive():
            messagebox.showinfo("Install rg", "請先等待目前搜尋完成或取消後再安裝。", parent=self)
            return

        if self._engine_info.executable and service_is_rg_engine(self._engine_info.executable):
            self.status_var.set("已偵測到 rg")
            self.summary_var.set("目前不需要安裝")
            return

        if not skip_confirm:
            confirmed = messagebox.askyesno(
                "Install rg",
                "將使用 winget 安裝 ripgrep (BurntSushi.ripgrep)。\n是否繼續？",
                parent=self,
            )
            if not confirmed:
                return

        self.install_rg_button.config(state="disabled")
        self.status_var.set("安裝中")
        self.summary_var.set("正在安裝 ripgrep（自動嘗試 MSVC/GNU 套件）")
        self._append_install_log("準備安裝 ripgrep（候選 ID: BurntSushi.ripgrep.MSVC, BurntSushi.ripgrep.GNU）")
        threading.Thread(target=self._install_rg_worker, daemon=True).start()

    def _install_rg_worker(self) -> None:
        result = install_ripgrep_with_winget(
            lambda line: self._queue_ui_action("install_log", line)
        )
        self._queue_ui_action("install_done", result.success, result.message)

    def _build_options(self) -> SearchOptions:
        if not self._folder_paths:
            raise ValueError("至少要有一個資料夾")
        folders = [Path(folder) for folder in self._folder_paths]
        for folder in folders:
            if not folder.exists() or not folder.is_dir():
                raise ValueError(f"資料夾不存在：{folder}")
        text = self.text_var.get().strip()
        if not text:
            raise ValueError("Containing Text 不可為空")
        try:
            max_file_size_mb = int(self.max_file_size_var.get().strip())
        except ValueError as exc:
            raise ValueError("Max file size 必須是整數") from exc
        return SearchOptions(
            folders=folders,
            include_patterns=_split_patterns(self.include_var.get()) or ["*"],
            exclude_patterns=_split_patterns(self.exclude_var.get()),
            text=text,
            recursive=self.recursive_var.get(),
            case_sensitive=self.case_sensitive_var.get(),
            use_regex=self.regex_var.get(),
            encoding=self.encoding_var.get().strip() or "Auto",
            max_file_size_mb=max_file_size_mb,
        )

    def _search_worker(self, options: SearchOptions, engine: str) -> None:
        try:
            if self._stop_event.is_set():
                self._queue_ui_action("finish_search", True)
                return

            if service_is_rg_engine(engine):
                try:
                    results = service_search_with_rg_stream(
                        engine,
                        options,
                        sort_mode=self.sort_var.get(),
                        emit_result=self._schedule_stream_results,
                        stop_event=self._stop_event,
                        process_callback=lambda process: setattr(self, "_active_process", process),
                    )
                except OSError:
                    results = service_search_with_grep_fallback_stream(
                        options,
                        sort_mode=self.sort_var.get(),
                        emit_result=self._schedule_stream_results,
                        stop_event=self._stop_event,
                    )
            else:
                results = service_search_with_grep_fallback_stream(
                    options,
                    sort_mode=self.sort_var.get(),
                    emit_result=self._schedule_stream_results,
                    stop_event=self._stop_event,
                )

            if self._stop_event.is_set():
                self._queue_ui_action("finish_search", True)
                return

            total_hits = sum(len(result.hits) for result in results)
            self._queue_ui_action("apply_results", results, total_hits)
        except Exception as exc:
            error_message = str(exc)
            self._queue_ui_action("show_error", error_message)

    def _apply_results(self, results: list[SearchFileResult], total_hits: int) -> None:
        self._all_results = results
        self._result_by_path = {str(result.full_path): result for result in results}
        self._update_filter_options()

        if results:
            self._refresh_file_tree(preferred_path=str(results[0].full_path))
            self.status_var.set(f"完成，共 {len(results)} 個檔案有命中")
            self.summary_var.set(f"找到 {total_hits} 筆結果")
        else:
            self._refresh_file_tree()
            self.status_var.set("完成，沒有命中結果")
            self.summary_var.set("沒有符合條件的結果")

        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_var.set(0)
        self._restore_buttons()

    def _show_search_error(self, message: str) -> None:
        self._active_process = None
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.status_var.set("搜尋失敗")
        self.summary_var.set(message)
        self._restore_buttons()

    def _finish_search(self, cancelled: bool) -> None:
        self._active_process = None
        self.progress.stop()
        self.progress.configure(mode="determinate")
        if cancelled:
            self.status_var.set("已取消")
            self.summary_var.set("搜尋已停止")
        self._restore_buttons()

    def _restore_buttons(self) -> None:
        self.start_button.config(state="normal")
        self.cancel_button.config(state="disabled")

    def _apply_font_size(self) -> None:
        size = _parse_positive_int(self.font_size_var.get(), default=11, minimum=8, maximum=32)
        self._table_font.configure(size=size)
        self._preview_font.configure(size=size)
        self._style.configure("Results.Treeview", font=self._table_font, rowheight=max(size + 12, 24))
        self._style.configure("Results.Treeview.Heading", font=self._table_font)
        if hasattr(self, "line_preview"):
            self.line_preview.configure(font=self._preview_font)

    def _search_with_rg_stream(self, engine_exec: str, options: SearchOptions) -> list[SearchFileResult]:
        return service_search_with_rg_stream(
            engine_exec,
            options,
            sort_mode=self.sort_var.get(),
            emit_result=self._schedule_stream_results,
            stop_event=self._stop_event,
            process_callback=lambda process: setattr(self, "_active_process", process),
        )

    def _search_with_grep_fallback_stream(self, options: SearchOptions) -> list[SearchFileResult]:
        return service_search_with_grep_fallback_stream(
            options,
            sort_mode=self.sort_var.get(),
            emit_result=self._schedule_stream_results,
            stop_event=self._stop_event,
        )

    def _schedule_stream_results(self, results: list[SearchFileResult], total_hits: int, current_item: str) -> None:
        self._queue_ui_action("stream_results", results, total_hits, current_item)

    def _apply_stream_results(self, results: list[SearchFileResult], total_hits: int, current_item: str) -> None:
        self._all_results = results
        self._result_by_path = {str(result.full_path): result for result in results}
        self._update_filter_options()
        self._refresh_file_tree()
        self.status_var.set(f"搜尋中，已找到 {len(results)} 個檔案")
        self.summary_var.set(f"目前 {total_hits} 筆結果 | {redact_path_for_display(current_item)}")

    def _update_filter_options(self) -> None:
        roots = ["All", *sorted({display_root_label(result.source_folder) for result in self._all_results})]
        extensions = ["All", *sorted({result.full_path.suffix or "<none>" for result in self._all_results})]
        if hasattr(self, "root_filter_combo"):
            self.root_filter_combo.configure(values=roots)
        if hasattr(self, "extension_filter_combo"):
            self.extension_filter_combo.configure(values=extensions)
        if self.root_filter_var.get() not in roots:
            self.root_filter_var.set("All")
        if self.extension_filter_var.get() not in extensions:
            self.extension_filter_var.set("All")

    def _refresh_file_tree(self, preferred_path: str | None = None) -> None:
        if not hasattr(self, "file_tree"):
            return

        selection = self.file_tree.selection()
        current_path = preferred_path or (selection[0] if selection else None)
        visible_results = _filter_file_results(
            self._all_results,
            self.file_filter_var.get(),
            self.root_filter_var.get(),
            self.extension_filter_var.get(),
            _parse_positive_int(self.min_hits_var.get(), default=1, minimum=1, maximum=9999),
            self.sort_var.get(),
        )

        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

        multiple_roots = len(_unique_result_roots(self._all_results)) > 1
        for result in visible_results:
            self.file_tree.insert(
                "",
                "end",
                iid=str(result.full_path),
                values=(_display_file_name(result, multiple_roots), len(result.hits)),
            )

        self.file_count_var.set(f"檔案：{len(visible_results)}/{len(self._all_results)}")

        if not visible_results:
            self._clear_preview()
            return

        visible_paths = {str(result.full_path) for result in visible_results}
        target_path = current_path if current_path in visible_paths else str(visible_results[0].full_path)
        self.file_tree.selection_set(target_path)
        self.file_tree.focus(target_path)
        self._render_preview_for_key(target_path)

    def _refresh_preview(self) -> None:
        selection = self.file_tree.selection()
        if selection:
            self._render_preview_for_key(selection[0])

    def _clear_preview(self) -> None:
        self._current_context_lines = []
        self.preview_info_var.set("預覽：尚未選擇檔案")
        if hasattr(self, "line_preview"):
            self.line_preview.configure(state="normal")
            self.line_preview.delete("1.0", "end")
            self.line_preview.configure(state="disabled")

    def _on_file_selected(self, _event: tk.Event) -> None:
        selection = self.file_tree.selection()
        if selection:
            self._render_preview_for_key(selection[0])

    def _render_preview_for_key(self, result_key: str) -> None:
        result = self._result_by_path.get(result_key)
        if result is None:
            self._clear_preview()
            return
        all_context_lines = self._get_cached_context_lines(result, self._get_display_lines())
        self._current_context_lines = all_context_lines
        self.preview_info_var.set(
            f"{display_root_label(result.source_folder)} / {result.relative_path} | hits: {len(result.hits)} | lines: {len(all_context_lines)}"
        )
        self._render_line_preview(result, all_context_lines, select_first_hit=True)

    def _render_line_preview(self, result: SearchFileResult, context_lines: list[ContextLine], select_first_hit: bool = False) -> None:
        self.line_preview.configure(state="normal")
        self.line_preview.delete("1.0", "end")
        self._preview_hit_ranges = []
        self._active_hit_index = -1

        query = self.text_var.get().strip()
        for context_line in context_lines:
            prefix = f"{context_line.line_number:>6} | "
            prefix_start = self.line_preview.index("end-1c")
            self.line_preview.insert("end", prefix)
            self.line_preview.tag_add("line_number", prefix_start, f"{prefix_start}+{len(prefix)}c")

            content_start = self.line_preview.index("end-1c")
            self.line_preview.insert("end", context_line.content)

            for tag_name, start, end in _find_syntax_spans(context_line.content, result.full_path.suffix.lower()):
                self.line_preview.tag_add(tag_name, f"{content_start}+{start}c", f"{content_start}+{end}c")

            for start, end in _find_match_spans(
                context_line.content,
                query,
                case_sensitive=self.case_sensitive_var.get(),
                use_regex=self.regex_var.get(),
            ):
                start_index = f"{content_start}+{start}c"
                end_index = f"{content_start}+{end}c"
                self.line_preview.tag_add("match", start_index, end_index)
                self._preview_hit_ranges.append((start_index, end_index))

            self.line_preview.insert("end", "\n")

        self.line_preview.configure(state="disabled")
        if select_first_hit:
            self._focus_hit(0)

    def _get_cached_context_lines(self, result: SearchFileResult, display_lines: int) -> list[ContextLine]:
        cached_lines = self._get_cached_file_lines(result.full_path)
        return _build_context_lines_from_lines(cached_lines, result.hits, display_lines)

    def _get_cached_file_lines(self, file_path: Path) -> list[str]:
        key = str(file_path)
        try:
            stats = file_path.stat()
            stamp = (stats.st_mtime_ns, stats.st_size)
        except OSError:
            return []

        cached = self._cached_file_lines.get(key)
        if cached and cached[0] == stamp[0] and cached[1] == stamp[1]:
            self._cached_file_lines.move_to_end(key)
            return cached[2]

        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as file_handle:
                lines = file_handle.readlines()
        except Exception:
            lines = []

        self._cached_file_lines[key] = (stamp[0], stamp[1], lines)
        self._cached_file_lines.move_to_end(key)
        while len(self._cached_file_lines) > 64:
            self._cached_file_lines.popitem(last=False)
        return lines

    def _focus_hit(self, index: int) -> None:
        if not self._preview_hit_ranges:
            return
        index = max(0, min(index, len(self._preview_hit_ranges) - 1))
        self._active_hit_index = index
        self.line_preview.configure(state="normal")
        self.line_preview.tag_remove("active_match", "1.0", "end")
        start_index, end_index = self._preview_hit_ranges[index]
        self.line_preview.tag_add("active_match", start_index, end_index)
        self.line_preview.see(start_index)
        self.line_preview.configure(state="disabled")

    def _focus_next_hit(self) -> None:
        if not self._preview_hit_ranges:
            return
        next_index = (self._active_hit_index + 1) % len(self._preview_hit_ranges)
        self._focus_hit(next_index)

    def _focus_previous_hit(self) -> None:
        if not self._preview_hit_ranges:
            return
        prev_index = self._active_hit_index - 1
        if prev_index < 0:
            prev_index = len(self._preview_hit_ranges) - 1
        self._focus_hit(prev_index)

    def _open_current_file(self) -> None:
        selection = self.file_tree.selection()
        if selection:
            self._open_result_key(selection[0])

    def _open_result_key(self, result_key: str) -> None:
        result = self._result_by_path.get(result_key)
        if result is None:
            return
        outcome = _open_path_safely(result.full_path)
        if outcome == "reveal":
            self.status_var.set("已在檔案總管定位")
            self.summary_var.set("偵測到可執行或腳本檔，已改為定位檔案以避免直接執行")
        elif outcome == "open_text":
            self.status_var.set("已開啟文字檔")
            self.summary_var.set("已使用文字編輯器開啟目前檔案")

    def _get_display_lines(self) -> int:
        return _parse_positive_int(self.display_lines_var.get(), default=7, minimum=1, maximum=101)

    def _open_selected_file(self, _event: tk.Event) -> None:
        selection = self.file_tree.selection()
        if not selection:
            return
        self._open_result_key(selection[0])

    def _on_close(self) -> None:
        self._save_settings()
        self.cancel_search()
        self.destroy()


def launch_app() -> None:
    app = RgSearchApp()
    app.mainloop()










