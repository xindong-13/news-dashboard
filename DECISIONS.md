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

## 資料模型

- **`SourceConfig` 的 `type` 只保留 `rss`、`api`**：Reddit 不用了；`api` 類型用 `parser` 欄位指定解析器。
- **`Event` 多了 `original_title`、`published_at`**：降級模式要顯示原標題；前端要顯示相對時間。
- **`LLMUsage` 多了 `model`、`calls`、`errors`**：方便追蹤額度與失敗原因。
- **`SourceConfig` 一律禁止未知欄位**：設定檔打錯字會直接報錯，不會默默被忽略。

## 已與使用者確認的方向（2026-11-15）

- repo 公開（GitHub Pages 免費）；私人化方案寫在 README。
- 電競「全部」的排序：LCK → LPL → 國際賽 → 其他（含 Valorant、CS2），組內依重要度。
- 每刊全部分類，只替重要度 ≥ 2 的前 80 則寫摘要（`settings.yaml` 可調）。
- 刊別：台北時間 16:30 前執行算午報，之後算晚報；時間範圍從上一個不同刊別的截止時間開始，最長 24 小時。
- 休賽季（預設 11/1～1/31）轉會、陣容異動的重要度 +1。
- LLM 以免費的 Gemini、Groq 為主，Claude Haiku 只當最後備援。
