# User Guide

This guide is for first-time users of `RG Search GUI`.
It focuses on startup, the first search, `rg` installation, and common troubleshooting.

## 1. Prerequisites

- Windows
- Python 3.10+
- `ripgrep (rg)` is recommended for the best search performance

## 2. Start the App

### Option 1: Windows launcher

Run:

```text
run_rg_search_gui.bat
```

The launcher does not depend on a hardcoded local Python path.
It tries `py` first, then `python` from `PATH`.

### Option 2: Run as a module

```bash
python -m rg_search_gui
```

### Option 3: Run after installation

```bash
pip install -e .
rg-search-gui
```

## 3. First Search

Recommended minimal validation:

1. Start the app
2. Add a small folder in `Folders`
3. Enter a clear keyword in `Containing Text`
4. Click `Start`
5. Confirm that the left panel shows matched files and the right panel shows preview content

## 4. Main Fields

### Folders

The list of folders to search. Multiple folders are supported.

### Include Files

Limits which files are searched, for example:

```text
*.py;*.md
```

### Exclude Files

Excludes folders or files, for example:

```text
.git;node_modules;__pycache__
```

### Containing Text

The keyword or pattern to search for. This field is required.

### Recursive

Search subfolders recursively.

### Case-sensitive

Enable case-sensitive matching.

### Regular Expression

Treat `Containing Text` as a regex pattern.

### File Encoding

Default is `Auto`. If you know the file encoding, you can set it manually.

### Max file size (MB)

Limits the maximum file size to search.

## 5. Installing `rg`

If the app cannot find `rg`, it can prompt you to install it on Windows.

The install flow:

- uses `winget`
- shows a live install log window
- treats "already installed / no upgrade available" as usable

You can also trigger the install flow manually from the `安裝 rg` button or the `Settings` menu.

## 6. Settings and Diagnostics

Use the `Settings` menu when you want to adjust search behavior or inspect the current engine state.

![Settings menu](assets/settings-menu.png)

### Menu items

- `Search Settings`: opens the search settings dialog
- `Diagnostics`: shows the detected engine, executable path, version, settings path, and folder count
- `Install ripgrep (rg)`: starts the Windows `winget` install flow manually

![Search Settings dialog](assets/search-settings-dialog.png)

### Search Settings fields

- `Recursive`: search subfolders
- `Case-sensitive`: match exact letter casing
- `Regular Expression`: treat the search text as regex
- `File Encoding`: choose `Auto` or a specific encoding such as `utf-8` or `cp950`
- `Max file size (MB)`: skip files that are too large
- `Display lines`: controls how many context lines are shown in preview
- `Font size`: adjusts the table and preview font size

When you close the settings dialog, the app saves the current settings automatically.

## 7. Search Results

### Left result panel

- shows matched files
- supports filters by file name, root, extension, and hit count
- supports sorting

### Right preview panel

- shows matching lines with context
- supports next / previous hit navigation
- can open the current file directly

## 8. Settings File

The app stores settings automatically.

Default Windows location:

```text
%APPDATA%\rg-search-gui\settings.json
```

If settings become corrupted, deleting that file is the simplest reset.

## 9. Common Issues

### Python launcher not found

If `run_rg_search_gui.bat` says it cannot find Python, your system does not expose `py` or `python`.

Check:

1. Python 3.10+ is installed
2. `py` or `python` works in a terminal
3. You reopened the terminal or app after installation

### `rg` not found

This usually means `ripgrep` is not installed yet.

You can:

- install it from the GUI prompt
- install it manually with `winget`

### Search is slow

Common causes:

- searching folders that are too large
- missing exclude patterns
- no file-size limit
- running without `rg`

Start by reducing folder scope, adding excludes, and lowering max file size.

### Regex returns no result

Check:

- `Regular Expression` is enabled
- the pattern itself is valid
- include / exclude rules are not filtering the target files out
