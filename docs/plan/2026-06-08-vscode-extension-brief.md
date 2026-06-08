# vscode-rg-anywhere 開發規劃書

| 項目 | 內容 |
|------|------|
| Repo | `ericlight0025/vscode-rg-anywhere`（private，發佈前改 public） |
| 參考 Repo | `ericlight0025/rg-search-gui`（現有 Python 版，後端邏輯可參考） |
| 狀態 | **準備開始實作** |
| 目標 | 將 rg-search-gui 的核心功能做成 VS Code Extension |

---

## 1. 為什麼做這個

VS Code 內建搜尋雖然用 ripgrep，但**只能搜 workspace 已加入的資料夾**。
這個 extension 的核心差異點：

| 功能 | VS Code 內建 | vscode-rg-anywhere |
|------|-------------|-------------------|
| 搜尋 workspace 外的任意資料夾 | ❌ | ✅ |
| 按命中數排序 / 最少命中過濾 | ❌ | ✅ |
| 副檔名 / 根目錄篩選 | ❌ | ✅ |
| 多資料夾結果彙整 | 有限 | ✅ |

現有類似 extension（Periscope、vscode-livegrep、Quick Grep）都綁 workspace，沒有填這個缺口。

---

## 2. 技術架構

```
VS Code Extension Host (Node.js / TypeScript)
│
├── WebviewPanel  ← HTML/CSS/JS UI（Obsidian 主題）
│   └── postMessage ↔ extension host
│
├── ripgrep subprocess  ← 直接 spawn rg（VS Code 自帶 @vscode/ripgrep）
│   └── JSON output 解析
│
└── vscode.window.showOpenDialog  ← 原生資料夾選取（免費解決最大風險）
```

**後端實作選擇（二擇一，建議 A）：**

| 方案 | 做法 | 優缺點 |
|------|------|--------|
| **A（推薦）** | 直接用 TypeScript 重寫搜尋邏輯 | 架構乾淨，無 Python 依賴，VS Code 自帶 rg 可直接用 |
| B | spawn Python subprocess，JSON 溝通 | 保留現有後端，但用戶需裝 Python，打包複雜 |

---

## 3. UI 設計

使用 **Obsidian / One Dark Pro** 色票，WebviewPanel 內跑 HTML/CSS。

色彩 token：
```css
--bg:         #282c34;
--surface:    #21252b;
--surface-2:  #2c313a;
--border:     #3b4048;
--text:       #abb2bf;
--text-dim:   #7f848e;
--accent:     #61afef;   /* 藍色強調 */
--match:      #f6d365;   /* 命中高亮黃 */
--ok:         #98c379;
--danger:     #e06c75;
```

語法高亮色（Obsidian）：
- keyword：`#c678dd`（紫）
- string：`#98c379`（綠）
- number：`#d19a66`（橘）
- type/fn：`#61afef`（藍）
- comment：`#7f848e`（灰）

版面：**三欄** — 搜尋側欄（280px）｜結果清單（300px）｜程式碼預覽（flex）

互動 mockup 可參考：`rg-search-gui` repo 的 `docs/assets/mockup-phosphor-workbench.html`（側欄有搜尋/設定兩個分頁可切換）

---

## 4. 功能清單（MVP）

- [ ] 搜尋文字輸入 + Enter 觸發
- [ ] 新增 / 移除任意資料夾（`vscode.window.showOpenDialog`）
- [ ] 包含 / 排除 glob 模式
- [ ] 大小寫 / 正規式 / 遞迴 toggle
- [ ] 即時串流結果（rg `--json` output）
- [ ] 結果清單（檔名 + 命中數徽章）
- [ ] 篩選：檔名關鍵字、副檔名、最少命中
- [ ] 排序：命中數 desc/asc、檔名 asc/desc
- [ ] 程式碼預覽（行號 + 語法高亮 + 命中高亮）
- [ ] 上一筆 / 下一筆命中跳轉
- [ ] 開啟檔案（`vscode.window.showTextDocument`）
- [ ] 危險副檔名只 reveal，不直接執行
- [ ] 設定持久化（`vscode.workspace.getConfiguration`）

---

## 5. 初始專案結構

```
vscode-rg-anywhere/
├── package.json          ← extension manifest + dependencies
├── tsconfig.json
├── .vscodeignore
├── .gitignore
├── src/
│   ├── extension.ts      ← activate/deactivate entry point
│   ├── SearchPanel.ts    ← WebviewPanel 管理
│   ├── SearchService.ts  ← rg subprocess 執行與結果解析
│   ├── models.ts         ← SearchOptions, SearchHit, SearchFileResult
│   └── webview/
│       ├── index.html    ← WebviewPanel 的 HTML shell
│       ├── main.js       ← UI 邏輯（postMessage 溝通）
│       └── style.css     ← Obsidian 主題樣式
└── README.md
```

---

## 6. package.json 重點欄位

```json
{
  "name": "vscode-rg-anywhere",
  "displayName": "RG Anywhere",
  "description": "ripgrep search across any folder, not limited to workspace",
  "version": "0.1.0",
  "engines": { "vscode": "^1.85.0" },
  "categories": ["Other"],
  "activationEvents": [],
  "main": "./out/extension.js",
  "contributes": {
    "commands": [
      {
        "command": "rgAnywhere.open",
        "title": "RG Anywhere: Open Search Panel"
      }
    ]
  },
  "dependencies": {
    "@vscode/ripgrep": "^1.15.0"
  },
  "devDependencies": {
    "@types/vscode": "^1.85.0",
    "typescript": "^5.3.0"
  }
}
```

---

## 7. 第一步建議（P0 PoC）

1. 建好專案骨架（package.json、tsconfig、src/extension.ts）
2. `activate()` 裡註冊 command，開一個空白 WebviewPanel
3. WebviewPanel 裡貼上 mockup 的 HTML/CSS，確認樣式正確
4. 接上 `vscode.window.showOpenDialog`，確認能拿到資料夾路徑
5. spawn `rg --json` subprocess，把結果顯示在 webview

P0 完成後整個架構就通了，剩下都是填功能。

---

## 8. 現有 Python 版參考邏輯

`rg-search-gui` repo 的這幾個檔案邏輯可以直接對照移植到 TypeScript：

| Python 檔案 | 對應 TypeScript |
|------------|----------------|
| `search_service.py` | `SearchService.ts`（rg subprocess + JSON 解析）|
| `search_helpers.py` | `SearchService.ts`（filter、sort、glob match）|
| `models.py` | `models.ts`（dataclass → TypeScript interface）|
| `privacy_helpers.py` | 可先跳過，MVP 後補 |
| `settings_service.py` | 用 `vscode.workspace.getConfiguration` 取代 |

---

## 9. 不做（Out of Scope）

- 不做雲端 / 多人
- 不做 Language Server Protocol 整合
- 不做 git blame / diff 功能
- MVP 不做路徑去敏感化（後補）
