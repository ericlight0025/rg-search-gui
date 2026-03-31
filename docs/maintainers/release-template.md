# Release Template

## Title

`v0.1.0 - Public MVP`

## Highlights

- Multi-folder desktop search GUI for Windows
- Powered by `ripgrep` when available
- GUI-guided `rg` installation with live logs
- Search preview with context and lightweight syntax highlighting

## Install

### Source

```bash
pip install -e .
rg-search-gui
```

### Windows launcher

Run `run_rg_search_gui.bat`

## Notes

- Python 3.10+
- Windows is the primary target environment
- `ripgrep` is recommended for best performance

## Release Checklist

- README is up to date
- CHANGELOG is updated
- `__pycache__` and `*.pyc` are not tracked
- Basic launch flow tested
- `rg` install prompt tested on Windows
