# 設計決策紀錄

規格（SPEC.md）沒寫清楚、或實作時發現跟規格衝突的地方，都記在這裡。新的寫在各節最後。

---

## 環境與工具

- **2026-11-15｜Python 3.12 由 uv 管理**：本機系統 Python 是 3.11，用 `uv python install 3.12` 裝在 uv 自己的目錄，不動系統環境。uv 本身用 `pip install --user uv` 安裝。
- **2026-11-15｜加裝 `tzdata`**：Windows 沒有內建 IANA 時區資料庫，沒有它 `ZoneInfo("Asia/Taipei")` 會失敗。
- **2026-11-15｜ruff 不檢查行長（E501）**：行長交給 `ruff format`，中文註解與網址不強制換行。

## 新聞來源（M1 實測，2026-11-15 台北時間晚上，從台灣家用網路測）

> ⚠️ GitHub Actions 的機器在美國，有些台灣網站可能擋海外 IP、有些國外網站會擋機房 IP。M6 上線後要再看一次 `source_errors`。

### 遵守 robots.txt 的例外

- **Google 新聞 RSS（`news.google.com/rss...`）設定 `ignore_robots: true`**。它的 robots.txt 是 `Disallow: /`，只放行 `/home`、`/topics/` 等網頁路徑，沒放行 `/rss`。但這些 RSS 是 Google 公開提供給人訂閱的，一般 RSS 閱讀器也不看 robots.txt（robots.txt 是給爬蟲用的）。我們每刊每個 feed 只抓一次，一天約 20 次請求，行為跟 RSS 閱讀器一樣。LPL 簡中新聞與 LCK 韓文新聞幾乎只能靠它取得。**使用者已同意（2026-11-15）**。其他來源一律遵守 robots.txt。

### 換掉或停用的來源

| 規格建議 | 結果 | 處理 |
|---|---|---|
| 公視新聞網 RSS | 連線被對方重設（WinError 10054） | 停用，保留在 sources.yaml |
| 聯合新聞網 RSS（udn.com/rssfeed） | 回 200，但每則標題、連結都是空的 | 停用；聯合系內容改由 Yahoo 新聞（轉載）與經濟日報補 |
| 中時新聞網 RSS | 502／404 | 移除；中時內容由 Yahoo 新聞轉載補 |
| MarketWatch Market Pulse | 最新一則停在 2025-07 | 停用，保留 Top Stories |
| Sheep Esports | 403，沒有可用 RSS | 停用 |
| Dot Esports（全部分類） | Cloudflare 驗證頁 | 停用，**不繞過** |
| esports.gg 分類 feed | Cloudflare 驗證頁 | 改用全站 feed 加關鍵字篩選 |
| Inven Global | 沒有 RSS（404） | 移除；LCK 改用韓國 Inven LoL RSS ＋ Google 뉴스 |
| LCK／LPL 官網、虎撲、玩加 | 沒有 RSS | 不解析 HTML（使用者同意 RSS／API 優先）；改用 Google 新聞搜尋 RSS 涵蓋 |
| vlrggapi（社群 API） | 部署已停用（402） | 不需要：VLR.gg 本身有 RSS |
| Liquipedia API | 可用，但轉會資訊要解析 wiki 頁面，且有嚴格的 parse 限速 | MVP 不用；Valorant／CS2 轉會由 VLR.gg、HLTV 涵蓋 |
| Reddit（JSON 與 RSS） | JSON 403；robots.txt 全站禁止 | **不用**（使用者同意）。之後要加可走官方 OAuth API |
| Dexerto `counter-strike/feed` | 404 | 改用 `counter-strike-2/feed` |
| Leaguepedia RosterChanges 表 | Cargo API 回 MWException | 改用 `NewsItems` 表（有完整句子，更適合當新聞） |

### 新增的來源（規格沒列，為了立場平衡或補缺）

- **Yahoo 奇摩新聞（政治、社會）**：轉載聯合、中時、TVBS 等，補足藍營媒體觀點。
- **ETtoday、新頭殼**：再多兩個不同立場的台灣媒體。
- **證交所 OpenAPI 新聞、央行新聞稿 RSS**：規格 4.3 的「央行、證交所公告」。央行的開標結果等例行公告用 `exclude_keywords` 濾掉。
- **Leaguepedia NewsItems**：LCK／LPL 一個來源（權重 1.5），其他賽區一個（權重 0.7）。「合約更新／延長」條目太多，先濾掉。
- **韓國 Inven LoL、Google 新聞搜尋 RSS（LPL 簡中、LCK 韓文、LoL 繁中與英文）**：取代沒有 RSS 的 LCK／LPL 在地來源。

### 資料品質修正

- **ETtoday 日期格式不標準**（`Sun,15 Nov 2026 21:39:00  +0800`）：feedparser 解析不了，加正規化後再解析。
- **VLR.gg 時間標「CST」但實為 UTC+8**：feedparser 當成美國中部時間，時間會跑到未來。來源可設 `date_tz` 覆寫時區。
- **Leaguepedia／證交所只有日期，沒有時間**：`RawItem.date_only = true`，M2 的時間範圍判斷要特別處理。
- **Google 新聞標題結尾的「 - 媒體名」**：切下來放進來源名稱，例如「自由時報（Google 新聞）」。它的 description 只是重複標題，不保存。

## 抓取與去重（M2）

- **2026-11-15｜中文、韓文標題用較低的相似度門檻（60），英文維持 85**：實測同一事件在不同中文媒體的標題，rapidfuzz `ratio` 多落在 60～80，門檻 85 幾乎合併不到；50～60 也大多是同一事件，但門檻太低會連鎖合併。英文短標題（Leaguepedia 的「Crisp leaves.」vs「Izo leaves.」）在 70 分上下卻是不同事件，所以英文標題至少 15 個字元才做模糊比對，中文至少 8 字。
- **模糊合併的兩個限制**：(1) 一個事件最多 10 則；(2) 同一家媒體的兩則不會被模糊合併（完全相同的標題除外）。同一家不會把同一件事寫兩次，相似通常是模板標題（鉅亨網「Factset 最新調查：○○」、央行「115年10月○○」）。
- **跨語言、改寫幅度大的同一事件**：規則合併不了，留給 M3 的 LLM。
- **已刊出過的新聞不重複出現**：讀最近 3 天各刊的來源網址，抓到相同網址就跳過（重跑同一刊時不算自己）。這也讓沒有時間的項目不會一直重複出現。
- **時間處理**：只有日期的項目（Leaguepedia、證交所）只要日期 ≥ 範圍起點前一天就收；時間比現在晚超過 1 小時的視為來源時區錯誤，丟掉並記錄 warning。
- **去掉標點後少於 4 個字的標題丟掉**（例如 LoL Esports 的「Vod」）。
- **送進 LLM 前的篩選**：每個類別先保證 1/4 的名額（預設各 50 則），避免選舉期間電競被政治新聞擠掉，剩下的依規則分數補滿。規則分數＝最高來源權重 ×（1 + 0.5 × 額外媒體數，最多 +2）× 新鮮度；LCK／LPL 的電競新聞 ×1.3。
- **降級版的重要度只到 4**：依報導家數估計（1 家 2、2～3 家 3、4 家以上 4；低權重單一來源 1）。5 留給 AI 判斷。今日重點取重要度 ≥ 4 的，不足 5 則就往下補。
- **全部來源都抓不到時不出刊**，結束碼 2，讓 Actions 亮紅燈；部分來源失敗照常出刊。
- **簡中來源先轉成台灣繁中**（M3 改成 `s2tw` 加人工對照表，原因見下一節），降級版也是繁體，簡繁同一事件也能合併。
- **Dexerto 的 feed 網址改成轉址後的 `/feed/category/...`**，少一次 301。

## AI 處理（M3，2026-11-16）

### 模型選擇（實測）

| 順序 | provider | 模型 | 實測結果 |
|---|---|---|---|
| 1 | Gemini | `gemini-3.5-flash-lite` | 免費；一般 1～2 秒回應，台灣用語正確（聯準會、升息）。偶爾 503，退避後重試成功 |
| 2 | Groq | `openai/gpt-oss-120b` | 免費；支援 strict JSON schema。回應快，但會用中國用語（美聯儲、加息），靠後處理修正。**免費額度每分鐘 8000 tokens、每天 1000 次**（從回應 header 讀到） |
| 3 | Anthropic | `claude-haiku-5-5` | 付費，有 key 才啟用；effort 設 low |

- `gemini-3.8-flash`、`gemini-flash-latest` 實測連很小的請求都 60 秒逾時（免費版可能過載），所以不用。
- Groq 文件上的 `llama-3.3-70b-versatile` 標示為 Enterprise，免費帳號不一定能用；`gpt-oss` 才支援嚴格 JSON schema。
- **Gemini 免費版的內容可能被 Google 用來改進產品**（官方定價頁）。送出去的只有公開新聞標題與 RSS 短摘要，可接受。
- Gemini 官方文件沒有列出免費版每分鐘次數，所以兩次呼叫至少間隔 5 秒（`min_interval_seconds`），遇到 429 依回應建議的時間等待。

### 介面與 fallback

- **Gemini、Groq 直接打 REST（httpx），Anthropic 用官方 SDK**：前兩家的 SDK 改版頻繁，REST 的欄位實測穩定；Anthropic 依官方建議用 SDK 的 structured outputs。
- **`complete_json` 回傳 `Completion`（JSON＋token 數），不是規格範例的 `dict`**：才能記錄 `llm_usage`。
- **回傳格式的 JSON schema 手寫成嚴格模式**（全部欄位 required、可為 null 的用 type 陣列），三家共用；pydantic 再驗證一次。
- **每次呼叫，每個 provider 最多試 2 次**（失敗後退避重試一次，再換下一家）：
  - 暫時性錯誤（503、逾時）：等 20 秒重試；格式不對：立刻重試
  - 429：依對方建議等待後重試，同一次呼叫最多等 3 輪（每分鐘額度用完時等一下就恢復）；要求等超過 65 秒視為每日額度用完，直接停用這家
  - 413（請求太大）：直接換下一家，不重試也不停用
  - key 錯誤、模型不存在：立刻停用這家
- **連續 2 次「呼叫」都失敗才停用這家**（不是一次呼叫內的 2 次嘗試）。實測 Gemini 偶爾連回兩次 503，原本「一次呼叫失敗就整場停用」導致後面的摘要、導讀全部沒做；現在下一批會再試它，真的掛掉才停用，避免每批都浪費時間。
- **每家可以設自己的批次大小與輸出上限**（`max_batch_items`、`max_output_tokens`）。批次是邊跑邊切的，大小依「當下會用的 provider」決定，Gemini 中途停用後剩下的會自動切成 Groq 吃得下的小批。原因：實測 Groq 免費版會把「輸入＋輸出上限」算進每分鐘 8000 tokens，25 則一批加上 8192 的輸出上限直接回 413。Groq 設成每批 8 則、輸出上限 3000。
- **只靠 Groq 跑一刊會慢很多**：2026-11-21 實測（故意給錯的 Gemini key），Gemini 第一次呼叫就被判定設定錯誤、本次停用，全部改由 Groq 完成。一刊約 17 分鐘、40 次呼叫、輸入 10.9 萬／輸出 3.9 萬 tokens，中間遇到 137 次 429 都照設計等待後重試成功（Gemini 同樣一刊約 2.5 分鐘、17 次呼叫）。所以 Groq 只是備援；如果 Gemini 長期不能用，建議加上 Anthropic key。
- **Groq 每批從 8 則降到 6 則**：2026-11-21 實測 Gemini 偶發 503 改送 Groq 時，8 則的請求「輸入＋輸出上限」算出 8115 tokens，超過每分鐘 8000 被拒（413），那批 25 則就沒經過 AI。
- **摘要批次不要因為一則空白就整批作廢**：同一次實測有 2 批（16 則）摘要因為格式驗證失敗而沒有摘要。改成 `summary_zh` 允許空字串、空的那則略過；錯誤訊息也會列出第一個錯誤的欄位，方便查原因。
- **降級是以批次為單位**：某一批分類失敗，只有那幾則用規則版卡片（`ai_processed: false`），其他照樣有 AI 整理。`llm_usage.degraded` 只有在整刊完全沒用到 LLM 時才是 true。

### 處理步驟

1. **分類＋翻標題**：同類別放同一批（每批 25 則），LLM 比較容易抓到同批的重複（`duplicate_of`）。
2. **合併重複**，共三層：(a) LLM 在同一批標出的 `duplicate_of`；(b) 分類後標題都是繁中，用標題相似度 ≥ 65 再合併一次（實測同一事件 71、不同事件 57）；(c) 把重要度 ≥ 3 的標題一次交給 LLM 找出不同批的同一事件（多 1 次呼叫）。實測世界賽冠軍原本分散在好幾張卡片，現在合成一張 12 個來源。不同類別不會被合併。
3. **摘要**：重要度 ≥ 2 的前 80 則，每批 12 則；`why_it_matters` 只有重要度 ≥ 4 才保留。
4. **今日重點**：依 AI 重要度用規則挑（不另外呼叫 LLM）。
5. **導讀**：1 次呼叫，3～5 條。

一刊實測（只用 Gemini）：約 17 次呼叫、輸入 5.5 萬／輸出 2.7 萬 tokens，抓取加處理約 2.5 分鐘。

### Prompt 的調整（依實測結果）

- **不把來源設定的 `league` 提示給 LLM**：「LPL 搜尋結果」裡常有 LCK 的新聞，實測會讓 LLM 把 Zeus、KRX 標成 LPL。改成在 prompt 列出各賽區主要戰隊，要求依戰隊所屬賽區判斷。
- **未經證實的傳聞重要度最高 3**：實測「韓網小道消息：KRX 新陣容」被給了 5。
- **休賽季加權改由 prompt 處理，程式不再 +1**：原本 prompt 和程式各加一級會重複；而且只有 LLM 分得出「已證實的轉會」和「傳聞」。

### 台灣用語

- **不用 OpenCC 的 `s2twp`**：它的片語轉換會把「支持」改成「支援」、「信息安全」改成「資訊保安」，政治新聞會改錯意思。改成 `s2tw`（只轉字形）＋人工確認過的對照表（`pipeline/taiwan_terms.py`：視頻→影片、美聯儲→聯準會、加息→升息、特朗普→川普、英偉達→輝達、宣佈→宣布…）。
- **所有 LLM 輸出（標題、摘要、導讀）都經過這個轉換**，即使 LLM 已經寫繁體，也能修掉中國用語與譯名。「臺」統一成「台」。

### 其他

- 本機用 `.env`（python-dotenv 載入，已存在的環境變數優先），Actions 用 Secrets。
- 刊別 JSON 一律用 LF 換行寫出，Windows 本機和 Actions 產生的檔案一致。

## 行情（M4，2026-11-21）

- **不用 Yahoo Finance／yfinance**：`query1`、`query2.finance.yahoo.com` 的 robots.txt 都是 `Disallow: /`。規格建議 yfinance，但它用的就是這些網址，違反「一律遵守 robots.txt」。
- 其他候選實測：Stooq、Binance、mis.twse.com.tw 也是 `Disallow: /`；CoinGecko 禁止 `/api/v3`；台銀匯率 403；Coinbase 憑證錯誤。
- **主要來源：鉅亨網報價 API（`ws.api.cnyes.com/ws/api/v1/quote/quotes/`）**。這個網域沒有 robots.txt，鉅亨網的新聞 API 本來就在用，一次請求就能查全部代號。欄位代碼：`6` 價格、`11` 漲跌、`56` 漲跌幅、`200007` 時間（Unix 秒）。
  代號實測：加權指數 `TWS:TSE01:INDEX`、台積電 `TWS:2330:STOCK`、道瓊 `GI:DJI:INDEX`、S&P 500 `GI:INX:INDEX`、那斯達克 `GI:IXIC:INDEX`、費半 `GI:SOX:INDEX`、美元兌台幣 `FX:USDTWD:FOREX`、10 年美債殖利率 `GF:US10YY:FUTURES`、現貨黃金 `GI:XAUUSDOZ:INDEX`、比特幣 `CC:BTC:FOREX`。
- **WTI 原油用 FRED（`DCOILWTICO`）**：鉅亨網試了十幾種代號都查不到原油。FRED 的 robots.txt 允許，但資料晚 3～4 天，所以網站會顯示「11/17 收盤」這種日期。美股三大指數、殖利率也設了 FRED 當備援。
- 規格說「黃金／原油」用期貨（`GC=F`、`CL=F`），這裡黃金改成現貨價，差距很小。
- 某一項抓不到只會讓那張卡顯示「—」，不影響出刊；`settings.yaml` 原本的 `symbol`、`twse_fallback` 欄位改成 `id`、`cnyes`、`fred`。
- **非官方 API 的風險**：鉅亨網的報價 API 沒有公開文件，哪天改格式行情卡就會全變「—」。到時再找替代（例如申請免費 key 的 Alpha Vantage、Twelve Data）。

## 網站（M5，2026-11-21）

- **Astro 7，純靜態、幾乎沒有 JS runtime**：分頁籤、篩選、相對時間、深色模式切換都是幾十行的原生 JS，寫在頁面裡。
- **所有頁面放在根目錄、用相對路徑互連**（`index.html`、`archive.html`、`search.html`、`2026-11-21-noon.html`），CSS 內嵌。這樣部署在 GitHub Pages 的子路徑（`/repo名稱/`）不用設定 base，也能直接預覽。
- 分頁籤用網址 hash（`#taiwan`、`#world`、`#finance`、`#esports`），可以直接分享某個分頁。
- **搜尋不用 Pagefind／Fuse.js**：中文用關鍵字「包含」比對比模糊搜尋準，而且不用多裝套件。索引依月份切檔（`search/2026-11.json`），從最新的月份開始載入，最多列 100 則。
- **重要度**：卡片左邊的色條（5 紅、4 橘、3 藍、2 以下灰），5 標「焦點」、4 標「重要」，不用星星。
- **漲跌顏色用台灣習慣**：漲紅跌綠。
- 「今日重點」的新聞在分類分頁裡也會出現一次（分頁是該分類的完整清單）。
- 電競篩選：遊戲（LoL／Valorant／CS2）、賽區（只在 LoL 有意義，選了其他遊戲會隱藏）、標籤（轉會、陣容、退役、大賽、賽果、爭議）。只列出這一刊實際有的選項。

- **今日重點固定 10 則、每個分類最多 2 則**（2026-11-22 使用者確認）：2026-11-21 實測，休賽季轉會新聞重要度 +1 後，8 則今日重點有 4 則是 LoL 轉會，國際新聞 0 則，所以設分類上限。4 類 × 2 = 8 湊不滿 10，剩下的名額每次把上限放寬 1 則再補，盡量平均分給各類，不會全給同一類。
- **原標題只在外文來源時顯示**：繁中來源經 AI 改寫的標題跟原標題差不多，再列一次是雜訊。

- **第四層去重：標題裡的英文專有名詞**（2026-11-22）：11/21 的今日重點出現兩則「Dplus KIA 簽 Doran、Kanavi」（一則來自英文、一則來自韓文報導），中文措辭差太多，標題相似度不到門檻，LLM 跨批去重也漏了。電競轉會新聞的標題幾乎都是「隊名＋選手名」，所以同類別、至少共用 3 個英文名詞、且占較短那則名詞數 75% 以上的兩則直接合併。拿 11/21 的兩刊實測抓到 5 組，全部都是真的重複，沒有誤併。

## 自動化（M6，2026-11-21）

- 排程提早 10 分鐘（台北 11:50、20:50）：GitHub 的排程常延遲。刊別依實際執行時間判斷，延遲到 16:30 前都還是午報。
- 出刊 workflow 用 `GITHUB_TOKEN` commit `data/`，但這種 commit 不會觸發其他 workflow，所以出刊 workflow 自己呼叫 `deploy.yml` 部署。
- 單一來源失敗不讓 Actions 失敗（記在 `source_errors`）；全部來源都失敗或程式錯誤才亮紅燈。

## 來源調整（2026-11-21）

- **HLTV RSS 停用**：改回 403（M1 時可用），依規則不繞過。
- **新增 `gnews_cs2_en`（Google News 英文搜尋 CS2）**：補 HLTV 停用後的 CS2 新聞，屬於使用者同意過的 Google 新聞 RSS 例外。

## 資料模型

- **`SourceConfig` 的 `type` 只保留 `rss`、`api`**：Reddit 不用了；`api` 類型用 `parser` 欄位指定解析器。
- **`Event` 多了 `original_title`、`published_at`、`ai_processed`**：降級模式要顯示原標題；前端要顯示相對時間；部分降級時前端可以標示哪幾則沒經過 AI。
- **`LLMUsage` 多了 `model`、`calls`、`breakdown`（各 provider 的用量）、`errors`**：方便追蹤額度與失敗原因。
- **`SourceConfig` 一律禁止未知欄位**：設定檔打錯字會直接報錯，不會默默被忽略。

## 已與使用者確認的方向（2026-11-15）

- repo 公開（GitHub Pages 免費）；私人化方案寫在 README。
- 電競「全部」的排序：LCK → LPL → 國際賽 → 其他（含 Valorant、CS2），組內依重要度。
- 每刊全部分類，只替重要度 ≥ 2 的前 80 則寫摘要（`settings.yaml` 可調）。
- 刊別：台北時間 16:30 前執行算午報，之後算晚報；時間範圍從上一個不同刊別的截止時間開始，最長 24 小時。
- 休賽季（預設 11/1～1/31）轉會、陣容異動的重要度 +1（M3 起由 prompt 處理，傳聞不加）。
- LLM 以免費的 Gemini、Groq 為主，Claude Haiku 只當最後備援。
