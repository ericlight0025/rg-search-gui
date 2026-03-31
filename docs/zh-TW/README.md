# RG Search GUI

這是一個以 Python 與 Tkinter 製作的桌面全文搜尋工具，主要目標是讓你不用一直切回終端機，也能快速跨多個資料夾搜尋文字內容。
維護者：`Javalight`
這是一個獨立開發的 GUI 工具，與 `ripgrep` 專案無官方隸屬、合作或背書關係。

English README: [../../README.md](../../README.md)

## 這個專案在做什麼

- 支援多資料夾搜尋
- 支援 include / exclude pattern
- 支援大小寫敏感與 regex
- 支援搜尋中即時串流顯示結果
- 支援命中內容預覽與簡單語法高亮
- Windows 下若缺少 `rg`，可在 GUI 內提示安裝並顯示安裝日誌

## 執行方式

### 方式 1：安裝後啟動

```bash
pip install -e .
rg-search-gui
```

### 方式 2：直接以模組啟動

```bash
python -m rg_search_gui
```

### 方式 3：Windows 啟動器

直接執行 `run_rg_search_gui.bat`
這個啟動器不會綁死本機 `C:\...` Python 路徑，會先嘗試 `py`，再嘗試 `PATH` 內的 `python`

## 需求

- Python 3.10 以上
- Windows 為主要目標環境
- 建議安裝 `ripgrep (rg)` 以獲得最佳搜尋效能

## 專案定位

這個專案目前適合作為公開 MVP：

- 原始碼清楚
- 功能聚焦在搜尋主線
- 可直接本機執行
- 沒有過度框架化

如果之後要擴大受眾，可以再補 GitHub Releases 的 `.exe` 版本。

## 補充文件

- 英文首頁文件：[../../README.md](../../README.md)
- 英文操作手冊：[../user-guide.md](../user-guide.md)
- 操作手冊：[usage-guide.md](usage-guide.md)
- 歷史整理文件 / 原始碼參考：[rg-search-gui-spec.md](rg-search-gui-spec.md)
- 版本記錄：[../../CHANGELOG.md](../../CHANGELOG.md)

## 授權

本專案採用 MIT License。
白話來說，別人可以使用、修改、散布這個專案，但必須保留原本的授權聲明。

## 貢獻

如果你要提 issue 或 PR，請先看 [../../CONTRIBUTING.md](../../CONTRIBUTING.md)。
