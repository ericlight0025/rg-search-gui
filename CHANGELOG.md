# Changelog

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
