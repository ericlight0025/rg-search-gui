"""安全性回歸測試。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rg_search_gui.models import SearchOptions
from rg_search_gui.search_service import _start_rg_process
from rg_search_gui.ui import _open_path_safely


class SearchServiceSecurityTests(unittest.TestCase):
    def test_rg_query_starting_with_dash_is_passed_as_pattern(self) -> None:
        options = SearchOptions(
            folders=[Path(r"C:\repo")],
            include_patterns=["*.py"],
            exclude_patterns=[],
            text="--version",
            recursive=True,
            case_sensitive=False,
            use_regex=False,
            encoding="Auto",
            max_file_size_mb=5,
        )
        mock_process = Mock()

        with patch("rg_search_gui.search_service.subprocess.Popen", return_value=mock_process) as popen:
            _start_rg_process("rg", options, Path(r"C:\repo"))

        cmd = popen.call_args.args[0]
        self.assertIn("-e", cmd)
        self.assertIn("--", cmd)
        pattern_index = cmd.index("-e") + 1
        self.assertEqual(cmd[pattern_index], "--version")
        self.assertEqual(cmd[-2:], ["--", "."])


class UiSecurityTests(unittest.TestCase):
    @patch("rg_search_gui.ui.os.startfile")
    @patch("rg_search_gui.ui.subprocess.Popen")
    def test_open_path_safely_reveals_batch_file_in_explorer(self, popen: Mock, startfile: Mock) -> None:
        file_path = Path(r"C:\temp\deploy.bat")

        outcome = _open_path_safely(file_path)

        self.assertEqual(outcome, "reveal")
        popen.assert_called_once_with(["explorer", f"/select,{file_path}"])
        startfile.assert_not_called()

    @patch("rg_search_gui.ui.os.startfile")
    @patch("rg_search_gui.ui.subprocess.Popen")
    def test_open_path_safely_opens_text_file_in_notepad(self, popen: Mock, startfile: Mock) -> None:
        file_path = Path(r"C:\temp\query.sql")

        outcome = _open_path_safely(file_path)

        self.assertEqual(outcome, "open_text")
        popen.assert_called_once_with(["notepad.exe", str(file_path)])
        startfile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
