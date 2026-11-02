# Changelog

## Unreleased

### Added

- Terminal UI `rg-search-tui` (Textual) for large log folders: left search options, top-right file list, bottom-right hit preview
- Hit navigation across files (`n` / `N`, `]` / `[`), level filter (ERROR / ERROR+WARN), refine-in-results with exclusions, adjustable context, full-file view
- Open in VS Code at the hit line, copy hit with context, export filtered hits, search history
- `tui` optional dependency, `run_rg_search_tui.bat` launcher, and TUI tests

## 0.1.0 - 2026-03-31

Initial public MVP packaging.

### Added

- Windows desktop search GUI built with Python and Tkinter
- `rg` engine detection with live version display
- In-app `winget` installation flow for `ripgrep`
- Live install log window for the `rg` setup workflow
- Traditional Chinese companion README

### Changed

- Extracted ripgrep installation workflow into `installer_service.py`
- Improved repository packaging for public release
- Removed generated cache files from the public artifact set
