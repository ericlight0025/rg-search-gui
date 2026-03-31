"""顯示層個資脫敏測試。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from rg_search_gui.models import SearchFileResult, SearchHit
from rg_search_gui.privacy_helpers import display_root_label, redact_path_for_display
from rg_search_gui.search_helpers import _display_file_name, _filter_file_results


class PrivacyRedactionTests(unittest.TestCase):
    def test_redact_path_for_display_uses_known_windows_aliases(self) -> None:
        env = {
            "APPDATA": r"C:\Users\alice\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
        }

        redacted = redact_path_for_display(
            r"C:\Users\alice\AppData\Roaming\rg-search-gui\settings.json",
            cwd=r"C:\DevWorkspace\codexcli\小工具\rg-search-gui",
            home=r"C:\Users\alice",
            env=env,
        )

        self.assertEqual(redacted, r"%APPDATA%\rg-search-gui\settings.json")

    def test_redact_path_for_display_masks_user_name_when_home_alias_does_not_apply(self) -> None:
        redacted = redact_path_for_display(
            r"C:\Users\alice\Secrets\notes.txt",
            cwd=r"D:\workspace",
            home=r"D:\profiles\other-user",
            env={},
        )

        self.assertEqual(redacted, r"C:\Users\<user>\Secrets\notes.txt")

    def test_display_root_label_prefers_redacted_home_style(self) -> None:
        label = display_root_label(
            r"C:\Users\alice\Documents",
            cwd=r"C:\DevWorkspace\codexcli\小工具\rg-search-gui",
            home=r"C:\Users\alice",
            env={},
        )

        self.assertEqual(label, r"~\Documents")

    def test_display_file_name_uses_redacted_root_label(self) -> None:
        result = SearchFileResult(
            source_folder=Path(r"C:\Users\alice\Documents"),
            relative_path="notes.txt",
            full_path=Path(r"C:\Users\alice\Documents\notes.txt"),
            hits=[SearchHit(line_number=1, content="secret")],
        )

        with patch("rg_search_gui.privacy_helpers.Path.cwd", return_value=Path(r"C:\DevWorkspace\codexcli\小工具\rg-search-gui")), patch("rg_search_gui.privacy_helpers.Path.home", return_value=Path(r"C:\Users\alice")), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_display_file_name(result, include_root=True), r"~\Documents / notes.txt")

    def test_filter_file_results_accepts_redacted_root_filter(self) -> None:
        result = SearchFileResult(
            source_folder=Path(r"C:\Users\alice\Documents"),
            relative_path="notes.txt",
            full_path=Path(r"C:\Users\alice\Documents\notes.txt"),
            hits=[SearchHit(line_number=1, content="secret")],
        )

        with patch("rg_search_gui.privacy_helpers.Path.cwd", return_value=Path(r"C:\DevWorkspace\codexcli\小工具\rg-search-gui")), patch("rg_search_gui.privacy_helpers.Path.home", return_value=Path(r"C:\Users\alice")), patch.dict(os.environ, {}, clear=True):
            filtered = _filter_file_results([result], filter_text="", root_filter=r"~\Documents", extension_filter="All", min_hits=1, sort_mode="Matches desc")

        self.assertEqual(filtered, [result])


if __name__ == "__main__":
    unittest.main()

