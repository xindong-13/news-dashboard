# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概要

個人用的每日新聞儀表板（台灣／國際／財經／電競），一天出兩刊（午報 12:00、晚報 21:00，台北時間）。Python 資料管線在 GitHub Actions 上抓 RSS／API → 去重合併 → LLM 分類與繁中摘要 → 輸出 `data/editions/YYYY-MM-DD-{noon|night}.json`，再由靜態前端（`web/`，Astro）讀 JSON 渲染並部署到 GitHub Pages。沒有後端與資料庫。

- **`SPEC.md` 是完整規格**，開發依第 10 節的里程碑 M1～M7 進行。進度看 `git log`。
- **`DECISIONS.md` 記錄所有偏離或補充規格的決定**（來源實測結果、換掉的來源、與使用者確認過的方向）。做出新決定時要寫進去。
- 工作流程：每完成一個里程碑就停下來，說明做了什麼、怎麼測試，git commit，等使用者說「繼續」才做下一個。
- 網站介面、文件、程式註解一律用**繁體中文（台灣用語）**。

## 常用指令

```bash
uv sync                                              # 安裝依賴（Python 3.12 由 uv 管理）
uv run pytest                                        # 全部測試
uv run pytest tests/test_fetch_parsing.py::test_roc_date   # 單一測試
uv run ruff check . && uv run ruff format .          # lint + 格式化（E501 已關閉，行長交給 format）
uv run python scripts/check_sources.py --samples 3   # 來源健康檢查；--all 含停用、--id <id> 指定來源、--markdown <path> 輸出報表
uv run python -m pipeline.main --edition noon        # 產生一刊（--edition auto|noon|night；--no-llm、--dry-run、--now <ISO>）
```

在 Windows 的 Git Bash 執行會印中文的腳本時，前面加 `PYTHONIOENCODING=utf-8`。

## 架構

資料流（`pipeline/main.py` 的 `build_edition`）：`plan_edition`（刊別與時間範圍）→ `fetch_all` → `filter_window`（範圍內、未刊出過）→ `localize`（簡轉繁）→ `cluster_items`（去重合併）→ `select_clusters`（規則篩到上限）→ LLM（M3）或 `degraded_event` → `write_edition`（刊別 JSON ＋ `data/index.json`）。

- 刊別依**實際執行時間**判斷（台北 16:30 前＝午報），時間範圍起點＝上一個「不同」刊別的 `window.to`；同一刊重跑會覆蓋檔案。
- 合併門檻中英文不同、同一家媒體不模糊合併等規則的理由見 `DECISIONS.md`「抓取與去重」。

- **`pipeline/models.py`** 是資料格式的唯一來源。`Edition` 序列化要用 `Edition.to_json()`，因為 `TimeWindow.from_` 要輸出成 `"from"` 欄位。前端的 TS 型別要跟這裡同步。
- **設定模型一律 `extra="forbid"`**：`sources.yaml`、`settings.yaml` 有打錯的欄位會直接報錯。新增設定欄位時，要同時改 `config.py`（或 `models.py` 的 `SourceConfig`）和 yaml。
- **`pipeline/http.py` 的 `PoliteClient`**：所有對外 HTTP 都走它，它處理 User-Agent、每網域限速（`settings.http.domain_delays`）和 robots.txt。不要另外開 httpx client。
- **抓取分派**（`pipeline/fetchers/__init__.py`）：`type: rss` 走 `rss.py`；`type: api` 依 `parser` 欄位查 `apis.py` 的 `PARSERS` 表。新增一般 RSS 來源只要改 yaml；新增 API 來源要在 `apis.py` 寫一個 parser 並註冊進 `PARSERS`。`fetch_all` 永遠不拋例外，單一來源的失敗會收集成 `SourceError`，最後寫進每刊的 `source_errors`。
- **`RawItem` 只存標題、連結、時間、短摘要**（`truncate` 到 300 字），不抓也不存全文，這是版權考量。

### 容易踩到的地方

- 時間一律存成帶時區的 datetime，顯示時用 `Asia/Taipei`（`textutil.TAIPEI`）。Windows 上需要 `tzdata` 套件。
- `RawItem.date_only=True` 的來源（Leaguepedia、證交所）只有日期、沒有時間，判斷時間範圍時要特別處理。
- `SourceConfig.date_tz` 用來處理時區縮寫不可信的來源，例如 VLR.gg 標「CST」實為 UTC+8。
- `ignore_robots: true` 只開放給 Google News 的官方 RSS，這是使用者同意過的例外。其他來源一律遵守 robots.txt。被 Cloudflare 或 403 擋的網站直接停用，不要繞過。
- Google News 的標題結尾會帶「 - 媒體名」，`split_google_news_title` 會把它切下來放進來源名稱。
- LLM 依 `settings.yaml` 的 provider 順序 fallback（Gemini → Groq → Anthropic），沒設 API key 的會跳過。全部失敗時照樣出刊，標記為 `degraded`。模型名稱放在設定檔，不要寫死。Prompt 放 `pipeline/prompts/*.md`。
- git 作者身分設定在 repo 層級（GitHub noreply email），不要改成使用者的私人 email。檔案一律 LF 換行（`.gitattributes`）。
