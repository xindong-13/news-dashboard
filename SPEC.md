# 專案規格書：個人每日新聞儀表板（台灣 × 國際 × 財經 × 電競）

> 這份文件是給 Claude Code 的開發規格。請先完整讀完，再依照「開發里程碑」順序實作。
> 遇到規格沒寫清楚的地方，選擇最簡單、可維護的做法，並在 `DECISIONS.md` 記錄你的決定與理由。

---

## 1. 專案目標

做一個**個人用的新聞儀表板網頁**，讓我每天在固定時間花幾分鐘就能掌握：

1. 台灣發生的大小事（政治、社會重大事件）
2. 國際重要新聞（國際政治、地緣衝突、重大事件）
3. 財經（台股、美股、匯率、央行／利率、產業動態），附主要行情數字
4. 電競（英雄聯盟 LoL、Valorant、CS2），重點放在**轉會、退役、大賽消息、重大事件／爭議**

我每天只看兩次，所以產品形式是**一天兩刊**：

| 刊別 | 出刊時間（台北時間） | 涵蓋範圍 |
|---|---|---|
| 午報 | 12:00 | 前一刊之後到 12:00 的新聞 |
| 晚報 | 21:00 | 12:00 到 21:00 的新聞 |

使用者條件：

- 我有開發經驗，可以接受完整技術架構，但希望**維運成本趨近於零**（免費、不用開電腦）。
- 介面語言一律**繁體中文（台灣用語）**，英文新聞要翻成中文摘要。
- 主要用手機看，桌機也要好用。

---

## 2. 整體架構

```
GitHub Actions（排程：每天 2 次）
   │
   ├─ 1. 抓取：RSS / 公開 API → 原始新聞清單
   ├─ 2. 清理：去重、相似新聞合併成同一事件
   ├─ 3. AI 處理：分類、重要度評分、繁中摘要、電競事件標籤
   ├─ 4. 行情：抓主要指數／匯率／加密貨幣收盤或即時數字
   ├─ 5. 輸出：data/editions/YYYY-MM-DD-{noon|night}.json
   └─ 6. 建置靜態網站並部署到 GitHub Pages
```

- **沒有後端伺服器、沒有資料庫**。資料以 JSON 檔 commit 回 repo，順便成為歷史存檔。
- 網站是純靜態站，讀取 JSON 渲染。

### 技術選型（建議，可調整但要記錄理由）

| 部分 | 選擇 | 理由 |
|---|---|---|
| 資料管線 | Python 3.12 + `uv` 管理套件 | 抓 RSS、處理資料生態最成熟 |
| RSS 解析 | `feedparser` + `httpx` | 穩定 |
| 行情 | `yfinance`（非官方）＋ TWSE OpenAPI | 免費；要有失敗備援 |
| LLM | 自己寫的 provider 抽象層（見第 5 節） | 免費優先、可切換 |
| 前端 | Astro + Tailwind CSS（或 Vite + 原生 TS） | 靜態輸出、速度快、對內容型網站友善 |
| 排程與部署 | GitHub Actions + GitHub Pages | 免費、不用開電腦 |
| 設定檔 | `config/sources.yaml`、`config/settings.yaml` | 新增或移除新聞來源不用改程式 |

---

## 3. 排程

GitHub Actions 的 cron 是 UTC，而且**常常延遲 10～30 分鐘以上**，所以要提早觸發：

```yaml
on:
  schedule:
    - cron: "30 3 * * *"   # 台北 11:30 → 午報，目標 12:00 前上線
    - cron: "30 12 * * *"  # 台北 20:30 → 晚報，目標 21:00 前上線
  workflow_dispatch:        # 可手動觸發，方便測試
```

- 程式要依照**實際執行時間**判斷是午報還是晚報，不要只依賴觸發的是哪個 cron。
- 每刊的時間範圍 = 上一刊的截止時間 → 本次執行時間。找不到上一刊就抓過去 12 小時。
- 同一刊重跑時要覆蓋，不要產生重複檔案。

---

## 4. 新聞來源

全部放在 `config/sources.yaml`，每個來源有：`id`、`name`、`url`、`type`（rss / api / reddit）、`category`、`language`、`weight`（可信度／重要度權重）、`enabled`。

> ⚠️ 以下 URL 是起點，**請逐一實測可用性**。抓不到、格式改了、或需要付費的來源就換掉，結果寫進 `DECISIONS.md`。並請寫一個 `scripts/check_sources.py` 健康檢查腳本。

### 4.1 台灣（政治／社會）

- 中央社 CNA（各分類 RSS）
- 公視新聞網 PTS
- 自由時報、聯合新聞網（政治、社會分類 RSS）
- Google News RSS（台灣、繁中）作為補漏：`https://news.google.com/rss?hl=zh-TW&gl=TW&ceid=TW:zh-Hant` 及各主題 feed

> 政治新聞來源要盡量**涵蓋不同立場**的媒體，避免只有單一觀點。摘要必須保持中立，只陳述事實。

### 4.2 國際

- BBC News（World）、BBC 中文
- 德國之聲 DW 中文、法廣 RFI 中文
- NPR／The Guardian World（有 RSS 的英文來源）
- Google News RSS（World 主題，英文與繁中各一）

### 4.3 財經

- 鉅亨網 cnyes（台股、國際股市、外匯）
- 中央社財經、經濟日報
- CNBC／MarketWatch 等有 RSS 的英文財經來源
- 央行、證交所公告（若有 RSS 或公開 API）

### 4.4 電競（重點類別，請特別用心）

| 遊戲 | 來源建議 |
|---|---|
| LoL | Sheep Esports（轉會消息很快）、Dot Esports、Dexerto LoL、Reddit r/leagueoflegends（只取高分貼文、esports flair）、Leaguepedia（lol.fandom.com）的轉會／Roster Changes 頁面（MediaWiki／Cargo API） |
| Valorant | VLR.gg（新聞與賽程；無官方 API，可評估社群的 vlrggapi 或解析頁面，注意禮貌抓取）、Reddit r/ValorantCompetitive、Dot Esports Valorant |
| CS2 | HLTV 新聞 RSS（`https://www.hltv.org/rss/news`，需實測）、Reddit r/GlobalOffensive（esports 相關）、Dexerto CS2 |
| 綜合 | Liquipedia（MediaWiki API，請遵守其 API 使用規範：自訂 User-Agent、限速）、esports.gg |

電競要特別關注：

- **LoL 以 LCK（韓國）與 LPL（中國）為主**：這兩個賽區的新聞權重最高，排序最前面。其他賽區（LEC、LTA、LCP 等）只在重要度高的時候才收錄（例如世界賽、知名選手轉會）。
  - LCK 來源補充：Inven Global（英文）、韓國 Inven（lol.inven.co.kr，韓文需翻譯）、LCK 官方
  - LPL 來源補充：LPL 官方、虎撲電競、玩加電競等簡中來源（需實測可用性；摘要一律轉成繁體中文、台灣用語）
  - Leaguepedia 的 LCK／LPL Roster Changes 頁面是轉會資訊的重要來源
- **大賽**：LoL 世界賽／MSI、Valorant Champions／Masters、CS2 Major 等
- 轉會、退役、禁賽、戰隊解散、重大爭議
- 休賽季（大約世界賽結束後到隔年初）是 LCK／LPL 轉會高峰，這段期間轉會消息要優先呈現

### 4.5 抓取規範

- 自訂 User-Agent（含 repo 網址），每個網域加延遲，遵守 robots.txt。
- 單一來源失敗不能讓整個流程掛掉：記錄錯誤後繼續，最後在該刊的 JSON 裡留一個 `source_errors` 欄位。
- **只保存標題、連結、發布時間、RSS 提供的短摘要**，不要抓全文、也不要在網站上轉載全文（版權）。

---

## 5. AI 處理（LLM）

### 5.1 Provider 抽象層

實作 `pipeline/llm/` 模組，統一介面，例如：

```python
class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...
```

支援三個 provider，用設定檔決定順序（fallback chain）：

| 順序 | Provider | 模型（以實作當下官方文件為準） | 說明 |
|---|---|---|---|
| 1 | Google Gemini | Flash／Flash-Lite 免費額度 | 預設 |
| 2 | Groq | OpenAI 相容 API 的開源模型 | 備援，也是免費 |
| 3 | Anthropic Claude | `claude-haiku-5-5` | 有 `ANTHROPIC_API_KEY` 才啟用，付費但很便宜 |

- API key 全部從環境變數讀取（GitHub Secrets）：`GEMINI_API_KEY`、`GROQ_API_KEY`、`ANTHROPIC_API_KEY`。
- 某個 provider 遇到 429 或錯誤時，退避重試一次，再換下一個。
- **全部 provider 都失敗時要降級**：照常出刊，但只顯示標題＋來源＋原始短摘要，頁面上標示「本刊未經 AI 整理」。
- 免費額度有限，所以要**批次處理**（例如一次送 20～40 則標題＋短摘要），不要一則新聞呼叫一次。
- 在 `settings.yaml` 設定每刊最多處理幾則（預設約 200 則），超過的先用規則篩掉。

### 5.2 處理步驟

1. **規則預處理（不用 LLM）**：URL 正規化去重；標題相似度（例如 rapidfuzz）≥ 門檻的合併成同一「事件」，保留所有來源連結。
2. **LLM 分類與評分**：每個事件輸出：
   - `category`：`taiwan` / `world` / `finance` / `esports`
   - `subcategory`：例如 `politics`、`society`、`tw_stock`、`us_stock`、`macro`、`lol`、`valorant`、`cs2`
   - `importance`：1～5（5 = 今天必看）
   - `esports_tags`（電競限定）：`transfer` 轉會 / `retirement` 退役 / `tournament` 大賽 / `result` 賽果 / `controversy` 爭議・禁賽 / `roster` 陣容異動 / `other`
   - `region_tags`：若與台灣有關標 `taiwan`
   - `league`（LoL 限定）：`lck` / `lpl` / `international`（世界賽、MSI）/ `other`。評分時 LCK、LPL 的新聞重要度要加權
3. **LLM 摘要**：每個事件產出
   - `title_zh`：繁中標題（英文新聞要翻譯）
   - `summary_zh`：2～3 句繁中摘要，**只寫事實，不加評論**
   - `why_it_matters`：一句話說明為什麼重要（重要度 ≥ 4 才需要）
4. **今日重點**：從所有類別挑出重要度最高的 5～8 則，放到頁面最上方。
5. **每刊導讀**：用 3～5 個條列總結這一刊最重要的事（一段話，類似主編的話）。

### 5.3 Prompt 要求

- Prompt 放在 `pipeline/prompts/*.md`，不要寫死在程式碼裡，方便我自己調。
- 要求 LLM 回傳 JSON，並用 schema（pydantic）驗證；驗證失敗就重試一次，再失敗就降級。
- 明確要求：繁體中文、台灣用語（例如「影片」不是「視頻」、「網路」不是「網絡」）、政治新聞中立、不得編造原文沒有的資訊。

### 5.4 成本預估（供參考）

一天兩刊、每刊約 200 則，以批次處理估算，每天大約 10 萬 input tokens、3 萬 output tokens：

- Gemini／Groq 免費額度：一天兩次的呼叫量應在免費範圍內。
- Claude Haiku 5.5（$0.10／百萬 input、$0.50／百萬 output）：每月約 1 美元以下。

請在每刊 JSON 中記錄 `llm_usage`（用了哪個 provider、token 數），方便我追蹤。

---

## 6. 行情數據

在財經區塊上方顯示一排行情卡：

| 項目 | 代號（yfinance 參考） |
|---|---|
| 台股加權指數 | `^TWII`（或 TWSE OpenAPI） |
| 台積電 | `2330.TW` |
| 道瓊／S&P 500／那斯達克 | `^DJI`、`^GSPC`、`^IXIC` |
| 費城半導體 | `^SOX` |
| 美元兌台幣 | `TWD=X` |
| 美國 10 年期公債殖利率 | `^TNX` |
| 黃金／原油 | `GC=F`、`CL=F` |
| 比特幣 | `BTC-USD` |

- 每張卡顯示：最新價、漲跌、漲跌幅、資料時間。
- 清單放在 `settings.yaml`，我可以自己增減（預留未來加自選股）。
- 抓不到的項目顯示「—」，不要讓整個區塊壞掉。
- 午報時美股是前一晚收盤，要清楚標示資料時間。

---

## 7. 網站功能與介面

### 7.1 頁面

1. **首頁（最新一刊）**
   - 頂部：刊別（午報／晚報）、日期、出刊時間、導讀
   - 今日重點（卡片，大一點）
   - 分頁籤或區塊：台灣｜國際｜財經｜電競
   - 財經區塊上方：行情卡
   - 電競區塊：可依遊戲（LoL／Valorant／CS2）、LoL 賽區（LCK／LPL／國際賽／其他）與標籤（轉會、退役、大賽…）篩選；預設排序 LCK、LPL 在最前面
2. **歷史存檔**：依日期瀏覽過去每一刊（日曆或列表）
3. **搜尋**：用前端搜尋（例如 Pagefind 或 Fuse.js）搜尋歷史新聞標題與摘要

### 7.2 每則新聞卡片

- 繁中標題、2～3 句摘要
- 重要度（視覺上用顏色或標記，不要用一堆星星）
- 類別／電競標籤
- 來源列表（可點擊、另開新分頁）：合併的事件會顯示「3 個來源」並可展開
- 發布時間（相對時間，例如「3 小時前」）

### 7.3 設計要求

- 手機優先、深色模式（跟隨系統，可手動切換）
- 閱讀為主的乾淨排版，資訊密度適中，字體大小適合手機閱讀
- 首頁載入快（靜態 JSON，避免大型框架 runtime）
- 繁中字型：系統字型堆疊即可（`"PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif`）

### 7.4 可選功能（MVP 之後）

- 已讀標記（存在 localStorage）
- 電競賽程表：未來 7 天重要比賽（LoL／Valorant／CS2）
- 每刊出刊後推播到 Discord／Telegram（webhook，只送導讀＋連結）
- PWA，可加到手機主畫面

---

## 8. 資料格式

`data/editions/2026-11-15-noon.json`（範例結構，可依需要調整，但要有 pydantic model 與 TS 型別對應）：

```json
{
  "edition_id": "2026-11-15-noon",
  "edition_type": "noon",
  "published_at": "2026-11-15T11:52:00+08:00",
  "window": { "from": "2026-11-14T21:00:00+08:00", "to": "2026-11-15T11:52:00+08:00" },
  "briefing": ["...", "..."],
  "headlines": ["evt_001", "evt_014"],
  "markets": [
    { "symbol": "^TWII", "name": "台股加權指數", "price": 0, "change": 0, "change_pct": 0, "as_of": "..." }
  ],
  "events": [
    {
      "id": "evt_001",
      "category": "esports",
      "subcategory": "lol",
      "importance": 5,
      "esports_tags": ["transfer"],
      "league": "lck",
      "region_tags": [],
      "title_zh": "...",
      "summary_zh": "...",
      "why_it_matters": "...",
      "sources": [
        { "name": "Sheep Esports", "url": "https://...", "published_at": "...", "language": "en" }
      ]
    }
  ],
  "llm_usage": { "provider": "gemini", "input_tokens": 0, "output_tokens": 0, "degraded": false },
  "source_errors": [{ "source_id": "...", "error": "..." }]
}
```

另外維護 `data/index.json`，列出所有刊別 ID，給存檔頁使用。

---

## 9. 專案結構（建議）

```
.
├── pipeline/
│   ├── fetchers/          # rss.py, reddit.py, liquipedia.py, markets.py ...
│   ├── processing/        # dedupe.py, cluster.py, edition.py
│   ├── llm/               # base.py, gemini.py, groq.py, anthropic.py, router.py
│   ├── prompts/           # classify.md, summarize.md, briefing.md
│   ├── models.py          # pydantic schemas
│   └── main.py            # 入口：python -m pipeline.main --edition auto
├── config/
│   ├── sources.yaml
│   └── settings.yaml
├── data/
│   ├── editions/
│   └── index.json
├── web/                   # Astro 前端
├── scripts/
│   └── check_sources.py
├── tests/
├── .github/workflows/
│   ├── edition.yml        # 排程抓取＋處理＋commit＋部署
│   └── ci.yml             # lint + test
├── DECISIONS.md
└── README.md
```

---

## 10. 開發里程碑

請依序完成，每個里程碑結束時確認可執行再往下：

1. **M1 骨架**：專案結構、設定檔、pydantic models、`check_sources.py`；實測並修正所有來源 URL。
2. **M2 抓取＋去重**：能在本機跑出一刊不含 AI 的 JSON（降級模式）。
3. **M3 LLM**：provider 抽象層＋fallback＋分類／摘要／導讀。沒有任何 API key 時也要能跑（降級）。
4. **M4 行情**：行情卡資料。
5. **M5 前端**：首頁、分類、電競篩選、存檔、搜尋、深色模式、手機版。
6. **M6 自動化**：GitHub Actions 排程、commit 資料、部署 GitHub Pages、失敗時 Actions 顯示紅燈。
7. **M7 文件**：README 寫清楚如何本機執行、如何申請各家 API key、如何設定 GitHub Secrets 與 Pages、如何新增新聞來源、如何調整 prompt。

---

## 11. 驗收標準

- [ ] 本機執行 `uv run python -m pipeline.main --edition noon` 可產生一刊 JSON
- [ ] 不設定任何 API key 時，流程仍能完成並輸出降級版
- [ ] 只設定 `GEMINI_API_KEY` 時，摘要為繁體中文、台灣用語
- [ ] 刻意讓 Gemini 失敗（例如錯誤的 key），會自動改用 Groq
- [ ] 任一新聞來源掛掉，不影響整刊產出，錯誤記錄在 `source_errors`
- [ ] 同一事件來自多家媒體時，只顯示一張卡片，並列出所有來源
- [ ] 電競區塊能依遊戲、LoL 賽區與「轉會／退役／大賽」標籤篩選，LCK／LPL 新聞排在最前面
- [ ] 行情卡顯示正確的資料時間，抓不到的顯示「—」
- [ ] 網站在手機寬度（375px）排版正常，支援深色模式
- [ ] GitHub Actions 每天兩次自動出刊並部署，也可手動觸發
- [ ] 有基本單元測試：去重／合併、刊別判斷、LLM 回傳 JSON 驗證、provider fallback
- [ ] 網站上沒有轉載新聞全文，只有標題、AI 摘要與原文連結

---

## 12. 注意事項

- 不要把任何 API key commit 進 repo；`.env.example` 列出需要的變數即可。
- 所有時間以 `Asia/Taipei` 顯示，內部存 ISO 8601 含時區。
- 免費 API 額度與模型名稱變動很快，實作前請查官方文件確認目前可用的免費模型與限制，並把模型名稱放在設定檔。
- 如果 repo 是 public，GitHub Pages 網站也會是公開的；請在 README 提醒我這點，並說明若要私人可改用 Cloudflare Pages + Access 等替代方案。
