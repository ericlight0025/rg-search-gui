"""搜尋服務回歸測試：編碼、rg 錯誤處理、glob 與重疊根目錄。"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rg_search_gui.models import SearchOptions
from rg_search_gui.search_helpers import _dedupe_folders, _matches_exclude, _matches_include
from rg_search_gui.search_service import (
    _search_with_python_fallback_stream,
    _search_with_rg_stream,
    _start_rg_process,
)
from rg_search_gui.settings_service import _save_settings_file


def _options(folder: Path, text: str, **overrides) -> SearchOptions:
    values = dict(
        folders=[folder],
        include_patterns=["*"],
        exclude_patterns=[],
        text=text,
        recursive=True,
        case_sensitive=False,
        use_regex=False,
        encoding="Auto",
        max_file_size_mb=10,
    )
    values.update(overrides)
    return SearchOptions(**values)


def _fake_rg_process(stdout_lines: list[dict], stderr: str, returncode: int) -> Mock:
    process = Mock()
    process.stdout = io.StringIO("".join(json.dumps(line) + "\n" for line in stdout_lines))
    process.stderr = io.StringIO(stderr)
    process.returncode = returncode
    process.wait.return_value = returncode
    return process


class RgCommandTests(unittest.TestCase):
    def _command(self, **overrides) -> list[str]:
        options = _options(Path(r"C:\repo"), "hello", **overrides)
        with patch("rg_search_gui.search_service.subprocess.Popen") as popen:
            _start_rg_process("rg", options, Path(r"C:\repo"))
        return popen.call_args.args[0]

    def test_cp950_is_passed_to_rg_as_big5(self) -> None:
        cmd = self._command(encoding="cp950")
        self.assertEqual(cmd[cmd.index("--encoding") + 1], "big5")

    def test_include_pattern_backslash_is_converted_for_rg_glob(self) -> None:
        cmd = self._command(include_patterns=[r"src\*.java", "*.*"])
        self.assertIn("src/*.java", cmd)
        self.assertNotIn("*.*", cmd)
        self.assertIn("--glob-case-insensitive", cmd)


class RgStreamTests(unittest.TestCase):
    def test_partial_read_errors_keep_results_and_report_warning(self) -> None:
        # rg 只要有一個路徑讀不到就回傳 2；有 summary 代表搜尋已跑完，結果要保留。
        lines = [
            {"type": "match", "data": {"path": {"text": "a.txt"}, "lines": {"text": "hello\n"}, "line_number": 3}},
            {"type": "summary", "data": {}},
        ]
        process = _fake_rg_process(lines, "rg: ./locked.pst: Access is denied. (os error 5)\n", 2)
        warnings: list[str] = []
        with patch("rg_search_gui.search_service.subprocess.Popen", return_value=process):
            results = _search_with_rg_stream(
                "rg", _options(Path("."), "hello"), warning_callback=warnings.append
            )

        self.assertEqual([hit.line_number for hit in results[0].hits], [3])
        self.assertIn("Access is denied", warnings[0])

    def test_regex_error_without_summary_is_reported_as_failure(self) -> None:
        process = _fake_rg_process([], "rg: regex parse error:\n    (?=a)\n", 2)
        with patch("rg_search_gui.search_service.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "正規表示式語法錯誤"):
                _search_with_rg_stream("rg", _options(Path("."), "(?=a)", use_regex=True))


@unittest.skipUnless(shutil.which("rg"), "需要 rg 才能驗證實際編碼行為")
class RgEncodingIntegrationTests(unittest.TestCase):
    def test_auto_encoding_finds_chinese_in_big5_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "legacy.sql").write_bytes("-- 客戶資料\nselect 1\n".encode("cp950"))
            (root / "modern.sql").write_text("-- 客戶資料\n", encoding="utf-8")

            results = _search_with_rg_stream(shutil.which("rg"), _options(root, "客戶"))

        self.assertEqual(sorted(result.full_path.name for result in results), ["legacy.sql", "modern.sql"])
        self.assertTrue(all(len(result.hits) == 1 for result in results))


class FallbackAndGlobTests(unittest.TestCase):
    def test_overlapping_roots_do_not_duplicate_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "src" / "a.txt").write_text("hello\n", encoding="utf-8")

            folders = _dedupe_folders([root, root / "src", root])
            results = _search_with_python_fallback_stream(_options(root, "hello", folders=[root, root / "src"]))

        self.assertEqual(folders, [root])
        self.assertEqual(len(results), 1)

    def test_include_all_matches_files_without_extension(self) -> None:
        self.assertTrue(_matches_include("Makefile", "Makefile", ["*.*"]))
        self.assertTrue(_matches_include("src/A.JAVA", "A.JAVA", [r"src\*.java"]))

    def test_exclude_name_matches_whole_path_segment_only(self) -> None:
        self.assertTrue(_matches_exclude("pkg/Node_Modules/x.js", "x.js", ["node_modules"]))
        self.assertFalse(_matches_exclude("src/latest.py", "latest.py", ["test"]))
        self.assertFalse(_matches_exclude("contest/a.py", "a.py", ["test"]))

    def test_fallback_zero_max_size_means_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("hello\n", encoding="utf-8")
            results = _search_with_python_fallback_stream(_options(root, "hello", max_file_size_mb=0))
        self.assertEqual(len(results), 1)


class SettingsTests(unittest.TestCase):
    def test_save_settings_writes_file_and_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            self.assertIsNone(_save_settings_file({"include": "*.py"}, settings_path))
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), {"include": "*.py"})

            error = _save_settings_file({}, Path(temp_dir) / "missing" / "settings.json")
        self.assertIn("無法寫入設定檔", error)


if __name__ == "__main__":
    unittest.main()
