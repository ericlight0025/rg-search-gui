# rg-search-gui → NiceGUI 重寫設計規劃書

| 項目 | 內容 |
|------|------|
| 文件版本 | v1.0 |
| 建立日期 | 2026-06-07 |
| 狀態 | **規劃中（尚未執行）** |
| 目標 | 將現有 Tkinter 桌面 GUI 重寫為 NiceGUI（網頁技術 + 原生視窗），取得現代化、可套用 design 工具的介面 |
| 設計依據 | Anthropic 官方 `frontend-design` skill |

> ⚠️ 本文件僅為規劃。**未開始實作**，所有程式碼片段為示意，非最終版本。

---

## 1. 為什麼要改 NiceGUI

### 1.1 現況痛點
- Tkinter 的 ttk style 只能手刻，**沒有 CSS、沒有元件市集、沒有動效**，配色與排版調整成本高、天花板低。
- 整個生態的 design 工具（`frontend-design` skill、Figma MCP、Magic MCP、shadcn）**全是網頁導向**，Tkinter 一個都用不上。
- 截圖會被 Windows IME 浮層污染（需用 PrintWindow 規避），代表 Tkinter 與系統整合在細節上很脆弱。

### 1.2 改 NiceGUI 後得到什麼
- **可套用 design skill / MCP**：介面就是 HTML/CSS/Vue，`frontend-design` 完全適用。
- **CSS 變數 + Tailwind + Quasar**：主題、動效、排版一次到位，配色用 token 管理。
- **同一套程式碼，原生視窗或瀏覽器皆可跑**（`ui.run(native=True)` 走 pywebview）。

### 1.3 為什麼是 NiceGUI 而非其他
| 候選 | 評價 |
|------|------|
| **NiceGUI** ✅ | 純 Python、後端邏輯零改動即可整合、可打包單機 exe、學習曲線低 |
| PySide6 + QML | 功能最強但 QML 另一套語言、授權與打包較重 |
| Electron + Web | 要寫 JS/前端棧，與現有 Python 後端隔一層 IPC，過重 |
| Streamlit | 重跑整頁的執行模型，不適合「即時串流結果 + 互動預覽」 |

---

## 2. 現況盤點與重用評估

總計 **2524 行**，只有 UI 層需重寫：

| 模組 | 行數 | 角色 | NiceGUI 重寫後 |
|------|-----:|------|----------------|
| `ui.py` | 1688 | Tkinter 介面 | ❌ **整支重寫** |
| `search_service.py` | 268 | rg/grep/python 搜尋引擎 | ✅ **原樣重用** |
| `search_helpers.py` | 238 | 結果過濾/排序/glob 比對 | ✅ **原樣重用** |
| `privacy_helpers.py` | 88 | 路徑去敏感化、根目錄標籤 | ✅ **原樣重用** |
| `installer_service.py` | 71 | 安裝 ripgrep | ✅ **原樣重用** |
| `engine_detection.py` | 67 | 偵測 rg/grep 引擎 | ✅ **原樣重用** |
| `models.py` | 47 | dataclass 資料模型 | ✅ **原樣重用** |
| `settings_service.py` | 35 | 設定 JSON 讀寫 | ✅ **原樣重用** |

> **關鍵結論**：836 行後端邏輯（搜尋、引擎、設定、隱私）與 UI 解耦良好，可直接重用。重寫風險集中在單一檔案 `ui.py`，且有現成功能規格可對照（見第 8 節對應表）。

---

## 3. 技術架構

```
┌─────────────────────────────────────────────┐
│  pywebview 原生視窗 (Windows WebView2)         │
│  ┌───────────────────────────────────────┐   │
│  │  NiceGUI 前端 (Vue3 + Quasar + Tailwind) │   │
│  └───────────────▲───────────────────────┘   │
│                  │ websocket (即時推送結果)    │
│  ┌───────────────┴───────────────────────┐   │
│  │  NiceGUI / FastAPI (uvicorn) 本機伺服器   │   │
│  │  ┌─────────────────────────────────┐  │   │
│  │  │  重用後端：search_service 等 7 模組 │  │   │
│  │  └─────────────────────────────────┘  │   │
│  └───────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

- **執行模型**：`ui.run(native=True, reload=False)`，本機 uvicorn + pywebview 視窗，使用者感受同桌面 App，不需開瀏覽器。
- **搜尋串流**：rg subprocess 在 worker thread 跑（沿用 `_search_with_rg_stream` 的 `emit_result` callback），結果丟進 `queue.Queue`，前端用 `ui.timer(0.1, drain_queue)` 取出並增量更新結果表。
- **狀態管理**：以一個 `@dataclass AppState` 持有搜尋選項與結果，UI 元件綁定其欄位。

---

## 4. 設計方向（Aesthetic Direction）

> 依 `frontend-design` skill：**承諾一個明確、大膽且貼合情境的美學方向，精準執行**。避免 Inter/Roboto、紫漸層、罐頭版型等「AI slop」。

### 4.1 主題概念：**「Phosphor Workbench」（磷光工作台）**
向 grep / 終端機的血統致敬，但做成高質感的現代開發者工具——不是復古玩具，而是**冷靜、緻密、有工程氣味**的工作介面。記憶點：**琥珀磷光的單一銳利強調色 + 等寬字標題 + 可見的 1px 網格線**，像一台精密儀器。

| 設計支柱 | 決策 |
|---------|------|
| **Tone** | industrial / utilitarian，克制的緻密資訊設計（參考 Linear、Raycast 的工程感，但加入終端機性格） |
| **記憶點** | 琥珀磷光強調色 `#ffb000` 在近黑碳灰底上的銳利對比；hover 時的微光暈 |
| **反制 AI slop** | 不用 Inter/Roboto、不用紫漸層、不用置中卡片堆疊 |

### 4.2 字型（不用系統字型 / Inter）
- **顯示 / 標題 / 程式碼**：`IBM Plex Mono`（有性格的等寬，呼應終端機血統）
- **內文 / 標籤**：`IBM Plex Sans`（與 Mono 同家族，工業感、辨識度高）
- 透過 `ui.add_head_html` 載入 Google Fonts 或自帶字檔。

### 4.3 色彩 Token（CSS 變數）
```css
:root {
  --bg:        #0c0d0e;  /* 碳黑底 */
  --surface:   #141618;  /* 面板 */
  --surface-2: #1b1e21;  /* 次面板 / hover */
  --border:    #2a2e33;  /* 1px 網格線 */
  --text:      #d7dbdf;  /* 主文字 */
  --text-dim:  #7d858c;  /* 次文字 */
  --accent:    #ffb000;  /* 琥珀磷光（主強調，唯一暖色） */
  --accent-dim:#9c6c00;  /* 強調暗階 */
  --match:     #ffd866;  /* 命中高亮底 */
  --match-fg:  #1a1300;
  --ok:        #5ec27e;  /* 成功（綠，僅狀態用） */
  --warn:      #e0813d;  /* 警告 */
  --danger:    #e0556b;  /* 危險動作 */
}
```
> 原則：**單一暖色主導 + 中性灰階**，比平均分配的多彩配色更銳利（skill 指引）。綠/橘/紅僅作狀態語意，不參與版面裝飾。

### 4.4 動效（克制、高衝擊時刻）
- **頁面載入**：左側欄與結果區交錯淡入（staggered，`animation-delay`），一次性開場。
- **搜尋中**：頂部一條磷光掃描進度線（取代旋轉 spinner，呼應掃描概念）。
- **命中跳轉**：預覽聚焦目標行時，該行底色磷光脈衝一次。
- **hover**：按鈕/列表項目 1px 邊框點亮 + 極淡光暈，無多餘位移。
- 全部優先 CSS-only；避免散落的微互動，集中在上述高衝擊時刻。

### 4.5 空間構成
- **非對稱三欄**：窄側欄（查詢/設定）｜結果清單｜寬預覽。可見 1px 分隔線強化「儀器面板」感。
- 控制密度高、留白克制（工具型而非行銷頁）。

---

## 5. 版面架構（Wireframe）

```
┌──────────────────────────────────────────────────────────────────────┐
│  ◆ PHOSPHOR WORKBENCH            引擎: ripgrep 14.1   [掃描中 ▰▰▱▱]   │ ← 頂列：品牌 + 引擎狀態 + 掃描進度線
├────────────┬───────────────────────┬───────────────────────────────────┤
│ [搜尋][設定] │  檔案 (12/40)          │  src/search_service.py            │ ← 分頁切左欄；中欄結果；右欄預覽
│            │  ┌───────────────────┐ │  hits: 8 · lines: 142            │
│ 搜尋文字     │  │ search_service.py 8│ │  ┌─────────────────────────────┐ │
│ ┌────────┐ │  │ ui.py           23│ │  │ 130 │ result = grouped...    │ │
│ │ result │ │  │ models.py        2│ │  │ 131 │   if result is None:   │ │ ← 命中行磷光高亮
│ └────────┘ │  │ helpers.py       5│ │  │ 132 │     result = Search... │ │
│            │  └───────────────────┘ │  └─────────────────────────────┘ │
│ 範圍        │  篩選 [____] 根 [All▾] │  [↑ 上一筆] [↓ 下一筆] [開啟檔案]  │
│  ▸ 資料夾列表 │  副檔名[All▾] 排序[▾] │                                  │
│ [開始搜尋]   │                       │                                  │
├────────────┴───────────────────────┴───────────────────────────────────┤
│  狀態: 完成，共 12 個檔案有命中 · 找到 38 筆結果                          │ ← 底部狀態列
└──────────────────────────────────────────────────────────────────────┘
```

NiceGUI 對應：`ui.header` ＋ `ui.splitter`（三欄可拖曳）＋ 左欄 `ui.tabs`/`ui.tab_panels`＋ 中欄 `ui.table`(aggrid) ＋ 右欄 `ui.codemirror`／自繪 `ui.html` ＋ `ui.footer`。

---

## 6. 元件級設計

### 6.1 搜尋列
- `ui.input` 綁 `text_var`，Enter 觸發搜尋（`.on('keydown.enter')`）。
- 旁附三個 toggle：大小寫、正規式、遞迴（`ui.switch`，磷光開關）。

### 6.2 資料夾範圍
- `ui.list` 顯示已加入根目錄，可上移/下移/移除/清空。
- **新增資料夾**：見第 9 節資料夾選取方案（重點風險）。

### 6.3 結果清單（中欄）
- `ui.aggrid` 或 `ui.table`：欄位 = 檔名、命中數；支援即時增量更新與點選。
- 上方篩選列：檔名關鍵字、根目錄、副檔名、最少命中、排序 → 全部沿用 `search_helpers._filter_file_results`。

### 6.4 內容預覽（右欄）— **最高技術風險**
- 需求：行號 + 語法高亮 + **命中字串高亮** + 命中行間跳轉。
- 方案 A（推薦）：`ui.codemirror`（CodeMirror 6），用 decoration 標記命中範圍，內建語法高亮與行號。
- 方案 B：自繪 `ui.html`，每行 `<span>` 套色 + 命中 `<mark>`，完全可控但要自己處理語法著色（可移植現有 `_find_syntax_spans` / `_find_match_spans` 邏輯）。
- 上一筆/下一筆/開啟檔案沿用後端 `_open_path_safely`（含危險副檔名只定位不執行的保護）。

### 6.5 設定分頁
- 搜尋設定（遞迴、大小寫、正規式、編碼、最大檔案 MB）。
- 預覽設定（顯示行數、字體大小、程式碼主題）。
- 診斷資訊 + 安裝 rg 按鈕。
- 全部綁 `settings_service` 讀寫。

---

## 7. 即時搜尋串流設計

```python
# 示意：worker thread 跑 rg，queue 傳結果，ui.timer 取
result_queue: queue.Queue = queue.Queue()
stop_event = threading.Event()

def run_search():
    service_search_with_rg_stream(
        engine, options, sort_mode,
        emit_result=lambda res, total, cur: result_queue.put((res, total, cur)),
        stop_event=stop_event,
    )

threading.Thread(target=run_search, daemon=True).start()

def drain():                       # 每 0.1s 取一次，增量刷新結果表
    while not result_queue.empty():
        results, total, current = result_queue.get()
        refresh_table(results, total, current)
ui.timer(0.1, drain)
```
- **取消搜尋**：`stop_event.set()` + terminate process（後端已支援）。
- 進度以頂部磷光掃描線呈現（不確定模式）。

---

## 8. 功能對應表（Tkinter → NiceGUI 全量對照，確保 0 功能流失）

| # | 現有功能 (Tkinter) | NiceGUI 對應 | 後端重用 |
|---|---------------------|--------------|---------|
| 1 | 搜尋文字輸入 + Enter 觸發 | `ui.input` + keydown | — |
| 2 | 包含/排除 glob | 兩個 `ui.input` | `search_helpers` |
| 3 | 遞迴 / 大小寫 / 正規式 | `ui.switch` ×3 | `search_service` |
| 4 | 編碼選擇 / 最大檔案 MB | `ui.select` / `ui.number` | `search_service` |
| 5 | 多根目錄：新增/CWD/上移/下移/移除/清空 | `ui.list` + 按鈕群 | — |
| 6 | 開始 / 取消搜尋 | `ui.button` + `stop_event` | `search_service` |
| 7 | 即時串流結果 | `ui.timer` + queue | `emit_result` |
| 8 | 結果檔案清單 + 命中數 | `ui.aggrid` | — |
| 9 | 篩選：檔名/根/副檔名/最少命中/排序 | 篩選列元件 | `_filter_file_results` |
| 10 | 內容預覽 + 行號 + 語法高亮 | `ui.codemirror` / `ui.html` | `_find_syntax_spans` |
| 11 | 命中字串高亮 | CodeMirror decoration / `<mark>` | `_find_match_spans` |
| 12 | 上一筆/下一筆命中跳轉 | 按鈕 + scrollIntoView | — |
| 13 | 安全開啟檔案（危險副檔名只定位） | 後端呼叫 | `_open_path_safely` |
| 14 | 引擎偵測 + 顯示 | 頂列徽章 | `engine_detection` |
| 15 | 安裝 ripgrep | `ui.button` + 通知 | `installer_service` |
| 16 | 設定持久化 | 綁定 + 存檔 | `settings_service` |
| 17 | 路徑去敏感化顯示 | 套用於顯示層 | `privacy_helpers` |
| 18 | 程式碼主題切換（Obsidian/Monokai/Light） | `ui.select` + CSS 變數 | — |
| 19 | 字體大小 / 顯示行數 | `ui.number` | — |
| 20 | 診斷資訊 | `ui.dialog` / 分頁 | — |
| 21 | 狀態列（狀態/摘要/檔案數） | `ui.footer` | — |

---

## 9. 關鍵風險：資料夾選取（必須先驗證）

瀏覽器的 `<input type=file>` **無法選資料夾、拿不到伺服器絕對路徑**。三個方案：

| 方案 | 做法 | 取捨 |
|------|------|------|
| **A（推薦）** | native 模式下，伺服器端呼叫 `tkinter.filedialog.askdirectory()` 跳原生資料夾對話框 | 體驗最好、回傳絕對路徑；需確保在主執行緒/正確呼叫 |
| B | 自製伺服器端檔案瀏覽器（`ui.tree` 走本機檔系統，參考 NiceGUI `local_file_picker` 範例） | 純前端、跨瀏覽器；但比原生對話框慢、要自己防越權 |
| C | pywebview 的 `window.create_file_dialog(FOLDER)` | 與 native 模式整合；綁 pywebview API |

> **行動項**：第一階段先做 PoC 驗證方案 A 在 `native=True` 下可正常跳窗回傳路徑，這是整個重寫能否成立的關卡。

---

## 10. 打包與發佈

- **開發**：`pip install nicegui pywebview`，`ui.run(native=True, reload=True)`。
- **單機 exe**：NiceGUI 官方 `nicegui-pack`（封裝 PyInstaller）；Windows native 模式依賴 **WebView2 Runtime**（Win11 內建，Win10 多數已有，否則需附帶安裝）。
- **風險**：pywebview + PyInstaller 在 Windows 偶有 WebView2 路徑問題，需在乾淨機器實測。

---

## 11. 階段拆解與工時估算

> 採 MVP 優先：先打通骨架與最大風險，再補細節。

| 階段 | 內容 | 產出 | 估時 |
|------|------|------|------|
| **P0 PoC** | `native=True` 起得來 + 方案 A 資料夾對話框驗證 + 後端模組 import 通 | 可選資料夾的空殼 | 0.5 天 |
| **P1 核心搜尋** | 搜尋列 + 串流結果表 + 取消 + 引擎徽章 | 能搜、能看到檔案清單 | 1 天 |
| **P2 預覽** | 右欄預覽（行號 + 語法 + 命中高亮 + 上/下一筆） | 完整搜尋→預覽流程 | 1 天 |
| **P3 篩選/設定** | 篩選列、設定分頁、持久化、安裝 rg、安全開檔 | 功能對應表 21 項全綠 | 0.5 天 |
| **P4 設計打磨** | 套 Phosphor Workbench 主題、字型、動效、響應式 | 視覺定稿 | 0.5–1 天 |
| **P5 打包** | `nicegui-pack` 出 exe + 乾淨機實測 + 文件/截圖更新 | 可發佈版 | 0.5 天 |
| | | **合計** | **約 4–4.5 天** |

---

## 12. 驗收標準

- [ ] 功能對應表（第 8 節）21 項全部可用，**0 功能流失**。
- [ ] rg / grep / python 三種引擎 fallback 行為與現版一致。
- [ ] 大型資料夾搜尋結果**即時串流**呈現，可中途取消。
- [ ] 危險副檔名（.exe/.bat…）只在檔案總管定位，**不直接執行**（保留現有安全行為）。
- [ ] 路徑去敏感化在 UI 顯示層生效。
- [ ] `native=True` 單機視窗可啟動；`nicegui-pack` 產出 exe 在乾淨 Windows 機可跑。
- [ ] 設定持久化（編碼、主題、字體、行數）跨次啟動保留。

---

## 13. 不做的事（Out of Scope）

- 不做雲端 / 多人 / SaaS，維持**單機本地工具**定位。
- 不改後端搜尋演算法（只重用，不重寫 `search_service` 等）。
- 不引入資料庫、帳號、權限系統。
- 保留純 CLI 能力的討論留待後續（本案聚焦 GUI 重寫）。

---

## 14. 決策待確認（開工前）

1. **美學方向**：採用「Phosphor Workbench（琥珀磷光）」？或偏好其他 tone（極簡 / 編輯雜誌 / 冷藍工程）？
2. **打包目標**：是否需要單機 exe（影響 P5 與 WebView2 依賴處理）？
3. **資料夾選取**：接受方案 A（原生對話框）作為首選，方案 B 為瀏覽器備援？

> 三點確認後即可進入 P0 PoC。**本規劃書到此為止，不含任何實作。**
