# 個人每日新聞儀表板

每天兩刊（午報 12:00、晚報 21:00，台北時間）的台灣／國際／財經／電競新聞整理。
GitHub Actions 抓 RSS／API → 去重合併 → AI 分類與繁中摘要 → 存成 JSON → 建置靜態網站 → 部署到 GitHub Pages。
沒有後端、沒有資料庫，免費額度內就能跑。

- 規格：[SPEC.md](SPEC.md)
- 設計決策與來源實測紀錄：[DECISIONS.md](DECISIONS.md)

## 目錄

| 路徑 | 內容 |
|---|---|
| `config/sources.yaml` | 新聞來源清單 |
| `config/settings.yaml` | 刊別時間、篩選數量、LLM provider 順序、行情清單 |
| `pipeline/` | Python 資料管線（抓取、去重、AI、行情、輸出） |
| `pipeline/prompts/` | 給 AI 的指令（分類、摘要、去重、導讀） |
| `data/editions/` | 每一刊的 JSON（`2026-11-21-noon.json`），由 Actions 自動 commit |
| `web/` | 網站（Astro），建置時讀 `data/` |
| `scripts/check_sources.py` | 來源健康檢查 |
| `.github/workflows/` | 出刊排程、部署、測試 |

---

## 1. 本機執行

需要：[uv](https://docs.astral.sh/uv/)（會自動裝 Python 3.12）、Node.js 22 以上（只有要看網站時需要）。

```bash
uv sync                                              # 安裝 Python 依賴
cp .env.example .env                                 # 填入 API key（全部留空也能跑，會輸出降級版）
uv run python -m pipeline.main --edition noon        # 產生一刊 → data/editions/
```

常用參數：

| 參數 | 用途 |
|---|---|
| `--edition auto\|noon\|night` | 刊別。`auto` 依現在時間判斷（台北 16:30 前＝午報） |
| `--no-llm` | 不呼叫 AI，直接輸出降級版（標題是原文、沒有摘要） |
| `--dry-run` | 不寫檔，只印結果 |
| `--now 2026-11-21T11:50:00+08:00` | 假裝在某個時間執行（測試刊別判斷） |
| `--data-dir <路徑>` | 輸出到別的資料夾（測試時避免弄髒 `data/`） |

同一刊重跑會覆蓋原本的檔案。Windows 的 Git Bash 若中文變亂碼，指令前面加 `PYTHONIOENCODING=utf-8`。

看網站：

```bash
cd web
npm install
npm run build && npm run preview     # 打開 http://localhost:4321
```

測試與 lint：

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

---

## 2. 申請 AI 的 API key

依 `config/settings.yaml` 的順序嘗試：**Gemini → Groq → Anthropic**。沒設定 key 的會自動跳過，全部失敗也會照常出刊（降級版）。**一家就夠用，建議至少設 Gemini。**

| 服務 | 費用 | 申請 | 環境變數 |
|---|---|---|---|
| Google Gemini | 免費額度，一天兩刊用不完 | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → Create API key | `GEMINI_API_KEY` |
| Groq | 免費（每分鐘 8000 tokens，比較慢） | [console.groq.com/keys](https://console.groq.com/keys) → Create API Key | `GROQ_API_KEY` |
| Anthropic（選用） | 付費，依用量計費 | [console.anthropic.com](https://console.anthropic.com/) → API Keys | `ANTHROPIC_API_KEY` |

本機放在 `.env`（已被 `.gitignore` 排除，不會 commit）。**不要把 key 貼到 issue、commit 或聊天裡。**

免費模型的名稱與額度變動很快。某家壞掉時，先到 `settings.yaml` 的 `llm.providers` 換模型名稱。

---

## 3. 放上 GitHub、設定自動出刊

1. **建立 repo**：在 GitHub 新增一個 repo（公開才能免費用 Pages），把這個資料夾 push 上去。
2. **把 User-Agent 換成你的 repo 網址**：`config/settings.yaml` 的 `http.user_agent`，讓新聞網站知道是誰在抓。
3. **設定 Secrets**：repo → Settings → Secrets and variables → Actions → New repository secret，
   依需要新增 `GEMINI_API_KEY`、`GROQ_API_KEY`、`ANTHROPIC_API_KEY`。
4. **開啟 Pages**：repo → Settings → Pages → Source 選 **GitHub Actions**。
5. **允許 Actions 寫入**：repo → Settings → Actions → General → Workflow permissions 選 **Read and write permissions**（出刊後要 commit `data/`）。
6. **手動跑第一刊**：Actions → 「出刊」→ Run workflow。成功後網址會出現在 Settings → Pages。

之後每天台北 11:50、20:50 自動執行（GitHub 的排程常延遲 5～30 分鐘，所以提早排）。

| workflow | 什麼時候跑 | 做什麼 |
|---|---|---|
| `edition.yml`「出刊」 | 排程、手動 | 產生一刊 → commit `data/` → 部署網站 |
| `deploy.yml`「部署網站」 | 改了 `web/`、手動、出刊後 | 建置並部署 Pages |
| `ci.yml`「測試」 | push、PR | pytest＋ruff |

**失敗時**：Actions 會亮紅燈，GitHub 預設會寄信給 repo 擁有者。單一新聞來源失敗**不會**讓出刊失敗，會記在該刊的 `source_errors`（網站最下面「這一刊的製作資訊」看得到）；只有全部來源都抓不到或程式錯誤才會紅燈。

**不想公開？** 免費帳號的 Pages 只能用公開 repo。替代方案：repo 設私人、把 `deploy.yml` 換成部署到 Cloudflare Pages 或 Netlify（免費、可設密碼保護）。

---

## 4. 新增新聞來源

一般 RSS 只要改 `config/sources.yaml`，加一段：

```yaml
  - id: my_source            # 不能重複
    name: 某某新聞             # 網站上顯示的來源名稱
    url: https://example.com/rss
    type: rss
    category: taiwan         # taiwan | world | finance | esports
    language: zh-TW          # zh-TW | zh-CN（自動轉繁體）| en | ko
    weight: 1.0              # 0～3，越高越容易入選、重要度越高
    # 選填：subcategory、game、league、max_items、keywords、exclude_keywords、note
```

加完先檢查能不能抓：

```bash
uv run python scripts/check_sources.py --id my_source --samples 5
```

規則：

- 要遵守 robots.txt；被 403 或 Cloudflare 擋的網站直接停用（`enabled: false`），不要繞過。
- 只存標題、連結、時間和短摘要，不抓全文。
- 沒有 RSS、只有 API 的來源，要在 `pipeline/fetchers/apis.py` 寫解析器並註冊到 `PARSERS`，再在 yaml 用 `type: api`、`parser: <名稱>`。
- 設定檔打錯欄位名稱會直接報錯（故意的，避免設定被默默忽略）。

## 5. 調整 AI 的指令（prompt）

在 `pipeline/prompts/`：

| 檔案 | 用途 |
|---|---|
| `classify.md` | 分類、重要度（1～5）、電競標籤、翻成繁中標題 |
| `summarize.md` | 2～3 句繁中摘要、「為什麼重要」 |
| `dedupe.md` | 找出不同語言、不同寫法的同一事件 |
| `briefing.md` | 刊頭導讀、今日重點 |

`{{名稱}}` 是程式會填入的內容，不要刪。回傳格式由 `pipeline/llm/schemas.py` 檢查，要改欄位時兩邊一起改。
改完可以用 `--data-dir` 輸出到暫存資料夾，比較前後差異：

```bash
uv run python -m pipeline.main --edition noon --data-dir /tmp/test-edition
```

所有 AI 輸出都會再經過 `pipeline/taiwan_terms.py` 轉成台灣用語，發現用語不對可以在那裡加對照。

## 6. 調整其他設定（`config/settings.yaml`）

- `edition.noon_cutoff`：午報／晚報的分界時間
- `processing.summarize_top_n`、`summarize_min_importance`：替幾則新聞寫摘要（影響 AI 用量）
- `esports.offseason`：休賽季日期（這段期間轉會新聞重要度 +1）
- `markets.items`：行情卡。代號到 [鉅亨網](https://invest.cnyes.com/) 查，例如個股 `TWS:2454:STOCK`、美股 `USS:NVDA:STOCK`

## 7. 網站功能

- 首頁是最新一刊：導讀、今日重點、台灣／國際／財經／電競分頁（網址 `#esports` 可以直接開電競分頁）
- 財經分頁上方有行情卡；電競可依遊戲、LoL 賽區、標籤篩選，LCK、LPL 排最前面
- 存檔頁可看過去每一刊；搜尋頁可搜所有刊的標題與摘要
- 深色模式預設跟隨系統，右上角按鈕可切換
- 已讀標記：點來源連結會自動標成已讀，也可以按卡片右下角的「標為已讀」；刊頭會顯示還有幾則未讀，可以「全部標為已讀」或「隱藏已讀」。存檔頁會顯示每刊讀了幾則。紀錄只存在這台裝置的瀏覽器
- 過去的刊都會保留，從存檔頁或刊頭的「上一刊」翻
- 可以加到手機主畫面，開起來像 App，沒網路時也能看開過的頁面：
  - iPhone：用 Safari 開網站 → 分享按鈕 → 「加入主畫面」
  - Android：用 Chrome 開網站 → 頁尾的「把這個網站安裝到手機主畫面」，或右上選單 →「安裝應用程式／加到主畫面」
- 排程：每刊排三次（午報 11:50、12:20、13:20；晚報 20:50、21:20、22:20）。後兩次是補跑，GitHub 漏跑時才會真的出刊
