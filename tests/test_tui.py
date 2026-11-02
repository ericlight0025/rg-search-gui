"""TUI 測試：用 Textual 的 run_test 實際按鍵，確認搜尋、導覽、篩選與帶走功能。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import textual  # noqa: F401

    HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - 沒裝 textual 時整組略過
    HAS_TEXTUAL = False

from rg_search_gui.models import EngineInfo
from rg_search_gui.tui_main import _build_parser

if HAS_TEXTUAL:
    from textual.widgets import Checkbox, DataTable, Input

    from rg_search_gui import tui
    from rg_search_gui.tui import FullView, HelpScreen, RgSearchTui, _detect_file_encoding

HAS_RG = shutil.which("rg") is not None


def _write_logs(folder: Path) -> None:
    lines_a = []
    for i in range(1, 201):
        if i % 20 == 0:
            lines_a.append(f"2026-10-26 10:00:{i % 60:02d} ERROR [OrderService] Connection timeout #{i}")
        elif i % 7 == 0:
            lines_a.append(f"2026-10-26 10:00:{i % 60:02d} WARN  [OrderService] slow response {i}ms")
        elif i % 5 == 0:
            lines_a.append(f"2026-10-26 10:00:{i % 60:02d} INFO  [OrderService] health-check ok")
        else:
            lines_a.append(f"2026-10-26 10:00:{i % 60:02d} INFO  [UserAuth] request handled {i}")
    (folder / "a.log").write_text("\n".join(lines_a) + "\n", encoding="utf-8")
    lines_b = [f"2026-10-27 09:00:00 INFO  [Other] line {i}" for i in range(1, 51)]
    lines_b[9] = "2026-10-27 09:00:10 ERROR [OrderService] Payment declined"
    (folder / "b.log").write_text("\n".join(lines_b) + "\n", encoding="utf-8")
    (folder / "big5.log").write_bytes("2026-10-28 08:00:00 ERROR 訂單逾時 OrderService\n".encode("cp950"))
    (folder / "skip.txt").write_text("OrderService should be ignored by *.log\n", encoding="utf-8")


@unittest.skipUnless(HAS_TEXTUAL, "需要 textual")
class TuiTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.logs = self.tmp / "logs"
        self.logs.mkdir()
        _write_logs(self.logs)
        self.history_patch = patch.object(tui, "HISTORY_FILE", self.tmp / "history.json")
        self.history_patch.start()

    def tearDown(self) -> None:
        self.history_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _app(self, query: str = "OrderService", **kwargs) -> RgSearchTui:
        return RgSearchTui(str(self.logs), query, **kwargs)

    async def _search(self, app, pilot) -> None:
        app.query_one("#query", Input).focus()
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.05)
            if not app._searching and app._summary.startswith("完成"):
                break
        await pilot.pause(0.1)
        app.query_one("#files", DataTable).focus()
        await pilot.pause()

    def _title(self, app) -> str:
        return str(app.query_one("#preview-title").render())

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_search_lists_files_by_hits_and_respects_include(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            names = [r.relative_path for r, _ in app._view]
            self.assertEqual(names[0], "a.log")
            self.assertIn("b.log", names)
            self.assertNotIn("skip.txt", names)
            self.assertIn("第 1 / ", self._title(app))

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_next_and_prev_hit_cross_file_boundary(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            first_count = len(app._view[0][1])
            await pilot.press("n")
            self.assertEqual((app._file_idx, app._hit_idx), (0, 1))
            self.assertIn("第 2 / ", self._title(app))
            for _ in range(first_count - 1):
                await pilot.press("n")
            await pilot.pause()
            self.assertEqual((app._file_idx, app._hit_idx), (1, 0))
            await pilot.press("N")
            await pilot.pause()
            self.assertEqual((app._file_idx, app._hit_idx), (0, first_count - 1))
            await pilot.press("right_square_bracket")
            await pilot.pause()
            self.assertEqual((app._file_idx, app._hit_idx), (1, 0))

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_level_and_refine_filters(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            await pilot.press("1")
            await pilot.pause()
            contents = [h.content for _, hits in app._view for h in hits]
            self.assertTrue(contents)
            self.assertTrue(all("ERROR" in c for c in contents))
            await pilot.press("0")
            await pilot.press("f")
            await pilot.pause()
            for key in ["minus", "h", "e", "a", "l", "t", "h"]:
                await pilot.press(key)
            await pilot.press("enter")
            await pilot.pause()
            contents = [h.content for _, hits in app._view for h in hits]
            self.assertFalse(any("health" in c for c in contents))
            self.assertIn("篩選後", str(app.query_one("#status").render()))

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_context_sort_and_sidebar_toggle(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            self.assertEqual(tui.CONTEXTS[app._context_i], 2)
            await pilot.press("plus")
            self.assertEqual(tui.CONTEXTS[app._context_i], 5)
            await pilot.press("minus")
            await pilot.press("minus")
            self.assertEqual(tui.CONTEXTS[app._context_i], 0)
            await pilot.press("s")
            await pilot.pause()
            self.assertEqual(tui.SORTS[app._sort_i], "檔名")
            self.assertEqual([r.relative_path for r, _ in app._view], sorted(r.relative_path for r, _ in app._view))
            await pilot.press("ctrl+b")
            self.assertFalse(app.query_one("#sidebar").display)
            await pilot.press("ctrl+b")
            self.assertTrue(app.query_one("#sidebar").display)

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_copy_open_and_export(self) -> None:
        app = self._app()
        old_cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            async with app.run_test(size=(160, 40)) as pilot:
                await self._search(app, pilot)
                with patch.object(app, "copy_to_clipboard") as copy:
                    await pilot.press("y")
                    copied = copy.call_args.args[0]
                self.assertIn(app._current()[1].content, copied)
                with patch.object(tui.shutil, "which", return_value="code"), patch.object(tui.subprocess, "Popen") as popen:
                    await pilot.press("o")
                    target = popen.call_args.args[0]
                self.assertEqual(target[:2], ["code", "-g"])
                self.assertTrue(target[2].endswith(f"a.log:{app._current()[1].line_number}"))
                await pilot.press("e")
                exported = list(self.tmp.glob("rg-search-*.log"))
                self.assertEqual(len(exported), 1)
                total = sum(len(h) for _, h in app._view)
                self.assertEqual(len(exported[0].read_text(encoding="utf-8").splitlines()), total)
        finally:
            os.chdir(old_cwd)

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_full_view_and_help_screens(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, FullView)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, FullView)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_history_is_saved_and_recalled(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
        self.assertIn("OrderService", (self.tmp / "history.json").read_text(encoding="utf-8"))
        app2 = self._app(query="")
        async with app2.run_test(size=(160, 40)) as pilot:
            box = app2.query_one("#query", Input)
            box.focus()
            await pilot.press("up")
            self.assertEqual(box.value, "OrderService")
            await pilot.press("down")
            self.assertEqual(box.value, "")

    @unittest.skipUnless(HAS_RG, "需要 rg")
    async def test_big5_file_found_and_previewed(self) -> None:
        app = self._app(query="訂單逾時")
        async with app.run_test(size=(160, 40)) as pilot:
            await self._search(app, pilot)
            self.assertEqual([r.relative_path for r, _ in app._view], ["big5.log"])
            preview = "\n".join(line.text for line in app.query_one("#preview").lines)
            self.assertIn("訂單逾時", preview)

    async def test_python_fallback_without_rg(self) -> None:
        with patch.object(tui, "_detect_engine_info", return_value=EngineInfo(executable=None, label="none")):
            app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            self.assertFalse(app._use_rg())
            await self._search(app, pilot)
            names = sorted(r.relative_path for r, _ in app._view)
            self.assertIn("a.log", names)
            self.assertNotIn("skip.txt", names)

    async def test_regex_and_case_toggles(self) -> None:
        app = self._app()
        async with app.run_test(size=(160, 40)) as pilot:
            app.query_one("#files", DataTable).focus()
            await pilot.press("f2")
            await pilot.press("f3")
            self.assertTrue(app.query_one("#regex", Checkbox).value)
            self.assertTrue(app.query_one("#case", Checkbox).value)


@unittest.skipUnless(HAS_TEXTUAL, "需要 textual")
class EncodingDetectionTest(unittest.TestCase):
    def test_detects_utf8_big5_and_explicit(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "u.log").write_text("中文 utf8\n", encoding="utf-8")
            (tmp / "b.log").write_bytes("中文 big5\n".encode("cp950"))
            self.assertEqual(_detect_file_encoding(tmp / "u.log", "Auto"), "utf-8")
            self.assertEqual(_detect_file_encoding(tmp / "b.log", "Auto"), "cp950")
            self.assertEqual(_detect_file_encoding(tmp / "b.log", "big5"), "cp950")
            self.assertEqual(_detect_file_encoding(tmp / "u.log", "utf-16"), "utf-16")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TuiMainArgsTest(unittest.TestCase):
    def test_parser_defaults_and_options(self) -> None:
        args = _build_parser().parse_args([])
        self.assertEqual((args.folders, args.query, args.include, args.encoding), (["."], "", "*.log", "Auto"))
        args = _build_parser().parse_args(["D:/logs", "E:/old", "-q", "timeout", "-g", "*.txt", "-e", "big5"])
        self.assertEqual(args.folders, ["D:/logs", "E:/old"])
        self.assertEqual((args.query, args.include, args.encoding), ("timeout", "*.txt", "big5"))


if __name__ == "__main__":
    unittest.main()
