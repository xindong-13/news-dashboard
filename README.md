# 個人每日新聞儀表板

每天兩刊（午報 12:00、晚報 21:00，台北時間）的台灣／國際／財經／電競新聞整理，外加 Threads 熱議。
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
| `pipeline/notify.py` | 出刊後推播到 LINE |
| `.github/workflows/` | 出刊、部署、推播、測試 |
| `worker/trigger.mjs` | 準時觸發出刊＋漏刊通知（跑在 Cloudflare） |

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

GitHub 的排程實測會晚 6～10 小時，所以準時出刊要再做「3-2. 準時出刊與漏刊通知」。

| workflow | 什麼時候跑 | 做什麼 |
|---|---|---|
| `edition.yml`「出刊」 | Cloudflare 觸發、手動、GitHub 排程（備援） | 產生一刊 → commit `data/` → 部署網站 |
| `deploy.yml`「部署網站」 | 改了 `web/`、手動、出刊後 | 建置並部署 Pages |
| `ci.yml`「測試」 | push、PR | pytest＋ruff＋Worker 測試＋網站建置 |

**失敗時**：Actions 會亮紅燈，GitHub 預設會寄信給 repo 擁有者。單一新聞來源失敗**不會**讓出刊失敗，會記在該刊的 `source_errors`（網站最下面「這一刊的製作資訊」看得到）；只有全部來源都抓不到或程式錯誤才會紅燈。

**不想公開？** 免費帳號的 Pages 只能用公開 repo。替代方案：repo 設私人、把 `deploy.yml` 換成部署到 Cloudflare Pages 或 Netlify（免費、可設密碼保護）。

---

## 3-1. 出刊推播到 LINE（選用）

每次出刊、網站部署完後，用 LINE 傳「導讀＋網站連結」給自己。用的是 LINE 官方帳號的 Messaging API，免費方案每月 200 則，一天兩刊約 62 則，夠用。（LINE Notify 已在 2025 年停止服務，不能用了。）

1. 到 [LINE Official Account Manager](https://manager.line.biz/) 用自己的 LINE 登入，建立一個官方帳號（名稱隨意，例如「每日新聞」），方案選免費的。
2. 在官方帳號的「設定 → Messaging API」按「啟用 Messaging API」，提供者（Provider）名稱隨便取。
3. 到 [LINE Developers Console](https://developers.line.biz/console/)，點進剛剛的 channel：
   - **Messaging API** 分頁：用手機掃 QR code，把官方帳號加為好友。
   - 同一頁最下面的 **Channel access token (long-lived)** 按「Issue」產生 token。
   - **Basic settings** 分頁最下面的 **Your user ID**（U 開頭）。
4. 存進 GitHub Secrets（在專案資料夾開終端機執行，會要你貼上內容，不會顯示在畫面上）：
   ```
   gh secret set LINE_CHANNEL_ACCESS_TOKEN
   gh secret set LINE_USER_ID
   ```
5. 到 GitHub Actions 手動執行「推播測試」，LINE 應該會收到最新一刊。
6. （建議）在 Official Account Manager 的「回應設定」關掉自動回應訊息，免得你傳訊息給它時收到罐頭回覆。

沒設定這兩個 Secrets 時，推播會自動略過。token 失效或本月則數用完時，Actions 的推播步驟會亮紅燈。

## 3-2. 準時出刊與漏刊通知

`worker/trigger.mjs` 跑在 Cloudflare（免費），每天做六件事：

| 台北時間 | 做什麼 |
|---|---|
| 11:50、20:50 | 觸發出刊 |
| 12:05、21:05 | 再觸發一次，已經出過就自動略過 |
| 12:30、21:30 | 看網站上有沒有這一刊，沒有就傳一則 LINE |

Cloudflare 自己沒跑、或 LINE 傳不出去時，改由 Healthchecks.io 寄 email。

> 金鑰只貼在下面說的欄位裡，不要貼到聊天、issue 或程式碼。網站改版後按鈕名稱可能略有不同。

### 步驟一：建立 GitHub 權杖（約 2 分鐘）

1. 點[這個連結](https://github.com/settings/personal-access-tokens/new?name=news-dashboard-trigger&description=Cloudflare%20edition%20trigger&expires_in=365&actions=write)，名稱、效期（365 天）、權限會先填好。
2. **Repository access** 選 **Only select repositories**，只勾 `news-dashboard`。
3. 確認 **Permissions** 只有 `Actions: Read and write`（系統會自動附上必要的 `Metadata: Read-only`），沒有別的。
4. 按 **Generate token**，把 `github_pat_` 開頭的那串複製起來。離開頁面後就看不到了，步驟三會用到。

### 步驟二：建立 Healthchecks.io 檢查（約 3 分鐘）

1. 到 [healthchecks.io](https://healthchecks.io/) 用 email 註冊（免費）。
2. 按 **Add Check** 新增一個，名稱填「漏刊檢查」。
3. 排程（Schedule）選 **Cron**：
   - Cron Expression：`30 12,21 * * *`
   - Time Zone：`Asia/Taipei`
   - Grace Time：`1 hour`
4. 到 **Integrations** 確認裡面有你的 email。
5. 複製這個檢查的網址（`https://hc-ping.com/` 開頭），步驟三會用到。

### 步驟三：建立 Cloudflare Worker（約 8 分鐘）

1. 到 [dash.cloudflare.com](https://dash.cloudflare.com/) 註冊並登入（免費，不用綁網域）。
2. 左邊 **Workers & Pages** → **Create application** → 選最簡單的 **Hello World** 範本 → 名稱填 `edition-trigger` → **Deploy**。
3. 按 **Edit code**，把原本的程式全部刪掉，貼上 [`worker/trigger.mjs`](worker/trigger.mjs) 的全部內容，按 **Deploy**。
4. 回到這個 Worker 的 **Settings → Variables and Secrets**，按 **Add** 加四個，類型都選 **Secret**，最後按 **Deploy**：

   | 名稱 | 內容 |
   |---|---|
   | `GITHUB_TOKEN` | 步驟一的權杖 |
   | `LINE_CHANNEL_ACCESS_TOKEN` | [LINE Developers Console](https://developers.line.biz/console/) → 你的 channel → Messaging API 最下面的 token。**不要按 Reissue**，按了舊的會失效 |
   | `LINE_USER_ID` | 同一個 channel → Basic settings 最下面的 Your user ID |
   | `HEALTHCHECKS_URL` | 步驟二的網址 |

5. **Settings → Trigger Events → Add → Cron Triggers**，加這四個（Cloudflare 用 UTC 時間，照抄就好）：

   ```
   50 3,12 * * *
   5 4,13 * * *
   30 4,13 * * *
   */5 * * * *
   ```

   最後一個是測試用的。設定最多要 15 分鐘才會生效。

### 步驟四：確認設定

1. 等 LINE 收到「🔧 出刊觸發設定檢查」，前兩行都要是 ✅。測試不會出刊。
2. 收到後，把測試用的 `*/5 * * * *` 刪掉，其他三個留著。
3. Healthchecks.io 上的檢查會變成綠色。

| 看到什麼 | 怎麼辦 |
|---|---|
| ❌ GitHub 權杖有問題（HTTP 401） | 權杖貼錯或過期，重做步驟一 |
| ❌ GitHub 權杖有問題（HTTP 403 或 404） | 權限或 repo 沒選對，重做步驟一 |
| ⚠️「出刊」目前是 disabled_manually | 到 Actions →「出刊」→ 按 Enable workflow |
| 20 分鐘都沒收到 LINE | Worker 的 **Settings → Trigger Events → View events** 看錯誤，多半是 LINE 的兩個值貼錯 |

### 之後的維護

- **權杖一年後過期**：過期後會漏刊，LINE 會說「GitHub 權杖失效」。重做步驟一，再把 Cloudflare 的 `GITHUB_TOKEN` 換成新的。
- **改了 `worker/trigger.mjs`**：要自己再貼到 Cloudflare 一次，push 不會自動更新。
- **收到漏刊通知**：先看通知裡寫的原因。想補出就到 Actions →「出刊」→ Run workflow，選午報或晚報，`target` 留空。
- **停掉**：在 Cloudflare 刪除三個排程，並在 Healthchecks.io 暫停檢查。

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
| `briefing.md` | 刊頭導讀 |

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

- 首頁是最新一刊：每刊約 25 則（台灣／國際／財經／電競／Threads 熱議各約 5 則；電競裡 Valorant、CS2 至少各 1 則），一頁往下滑就看完。最上面是導讀，最重要的幾則標「焦點」，並附「為什麼重要」。上方的分類列可以直接跳到某一類（網址 `#esports` 會直接跳到電競）
- 財經區塊上方有行情卡；電競依 LCK、LPL、國際賽的順序排
- 存檔頁可看過去每一刊；搜尋頁可搜所有刊的標題與摘要
- 深色模式預設跟隨系統，右上角按鈕可切換
- 已讀標記：點來源連結會自動標成已讀，也可以按卡片右下角的「標為已讀」；刊頭會顯示還有幾則未讀，可以「全部標為已讀」或「隱藏已讀」。存檔頁會顯示每刊讀了幾則。紀錄只存在這台裝置的瀏覽器
- 過去的刊都會保留，從存檔頁或刊頭的「上一刊」翻
- 可以加到手機主畫面，開起來像 App，沒網路時也能看開過的頁面：
  - iPhone：用 Safari 開網站 → 分享按鈕 → 「加入主畫面」
  - Android：用 Chrome 開網站 → 頁尾的「把這個網站安裝到手機主畫面」，或右上選單 →「安裝應用程式／加到主畫面」
- 排程：Cloudflare 在 11:50、20:50 觸發（12:05、21:05 再試一次），12:30、21:30 還沒出就用 LINE 通知。GitHub 自己的排程只當備援
- 自動出刊的時段：午報 11:45～16:30、晚報 20:45～24:00（`settings.yaml` 的 `edition`）。超過時段就不補，要補請手動執行
- 手動執行會覆蓋同一刊；00:00～05:00 手動執行（自動判斷或選晚報）算前一天的晚報
