"""RG Search TUI 進入點：rg-search-tui [資料夾 ...] [-q 關鍵字] [-g *.log]。"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rg-search-tui",
        description="用 ripgrep 搜尋大量 log 的終端機介面。",
    )
    parser.add_argument("folders", nargs="*", default=["."], help="要搜尋的資料夾，可給多個（預設：目前資料夾）")
    parser.add_argument("-q", "--query", default="", help="開啟後直接搜尋這個關鍵字")
    parser.add_argument("-g", "--include", default="*.log", help="包含的檔案樣式，多個用 ; 分隔（預設：*.log）")
    parser.add_argument("-x", "--exclude", default="*.gz", help="排除的檔案樣式，多個用 ; 分隔（預設：*.gz）")
    parser.add_argument("-e", "--encoding", default="Auto", help="檔案編碼：Auto、utf-8、big5 等（預設：Auto）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        from rg_search_gui.tui import RgSearchTui
    except ImportError:
        print("缺少 textual 套件，請先執行：pip install -e .[tui]  （或 pip install textual）", file=sys.stderr)
        return 1
    app = RgSearchTui(
        folder=";".join(args.folders),
        query=args.query,
        include=args.include,
        exclude=args.exclude,
        encoding=args.encoding,
        auto_search=bool(args.query),
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
