"""預覽 theme 設定測試。"""

from __future__ import annotations

import unittest

from rg_search_gui.ui import DEFAULT_PREVIEW_THEME, PREVIEW_THEMES, _normalize_preview_theme_name


class PreviewThemeTests(unittest.TestCase):
    def test_normalize_preview_theme_name_returns_default_for_unknown_value(self) -> None:
        self.assertEqual(_normalize_preview_theme_name("UnknownTheme"), DEFAULT_PREVIEW_THEME)

    def test_preview_theme_palettes_share_required_keys(self) -> None:
        required_keys = {
            "bg",
            "fg",
            "line_number",
            "selection_bg",
            "selection_fg",
            "match_bg",
            "match_fg",
            "active_match_bg",
            "active_match_fg",
            "syntax_comment",
            "syntax_string",
            "syntax_keyword",
            "syntax_number",
            "syntax_type",
        }
        for name, palette in PREVIEW_THEMES.items():
            with self.subTest(theme=name):
                self.assertTrue(required_keys.issubset(palette.keys()))


if __name__ == "__main__":
    unittest.main()
