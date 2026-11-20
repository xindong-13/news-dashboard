"""Pydantic 資料模型。

前端的 TS 型別（web/src/types.ts）要跟這裡保持一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 列舉值
# ---------------------------------------------------------------------------

Category = Literal["taiwan", "world", "finance", "esports"]
EditionType = Literal["noon", "night"]
Language = Literal["zh-TW", "zh-CN", "en", "ko"]
SourceType = Literal["rss", "api"]  # reddit 已評估後放棄，見 DECISIONS.md
EsportsTag = Literal[
    "transfer", "retirement", "tournament", "result", "controversy", "roster", "other"
]
League = Literal["lck", "lpl", "international", "other"]
Game = Literal["lol", "valorant", "cs2"]

SUBCATEGORIES: dict[str, list[str]] = {
    "taiwan": ["politics", "society", "cross_strait", "other"],
    "world": ["politics", "conflict", "disaster", "tech", "other"],
    "finance": ["tw_stock", "us_stock", "macro", "forex", "industry", "crypto", "other"],
    "esports": ["lol", "valorant", "cs2", "other"],
}


# ---------------------------------------------------------------------------
# 設定檔
# ---------------------------------------------------------------------------


class SourceConfig(BaseModel):
    """config/sources.yaml 裡的一個來源。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    url: str
    type: SourceType
    category: Category
    language: Language
    weight: float = Field(default=1.0, ge=0, le=3)
    enabled: bool = True
    # 選填：給規則與 LLM 的提示
    parser: str | None = None  # type=api 時指定解析器，例如 cnyes、leaguepedia
    subcategory: str | None = None
    game: Game | None = None
    league: League | None = None
    max_items: int = 40
    keywords: list[str] | None = None  # 只收標題含任一關鍵字的項目（不分大小寫）
    exclude_keywords: list[str] | None = None  # 標題含任一關鍵字就丟掉
    ignore_robots: bool = False  # 只給官方公開訂閱的 RSS 用，理由見 DECISIONS.md
    date_tz: str | None = (
        None  # 來源標的時區縮寫不可信時，改用此 IANA 時區解讀（例如 Asia/Shanghai）
    )
    note: str | None = None  # 給人看的備註，例如實測結果


# ---------------------------------------------------------------------------
# 抓取結果
# ---------------------------------------------------------------------------


class RawItem(BaseModel):
    """從單一來源抓到的一則新聞（只有標題、連結、時間、短摘要）。"""

    source_id: str
    source_name: str
    title: str
    url: str
    published_at: AwareDatetime | None
    date_only: bool = False  # 來源只給日期（例如 Leaguepedia），時間不可信
    summary: str = ""
    language: Language
    category: Category
    weight: float = 1.0
    subcategory: str | None = None
    game: Game | None = None
    league: League | None = None


# ---------------------------------------------------------------------------
# 一刊
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    name: str
    url: str
    published_at: AwareDatetime | None = None
    language: Language


class Event(BaseModel):
    id: str
    category: Category
    subcategory: str | None = None
    importance: int = Field(ge=1, le=5)
    esports_tags: list[EsportsTag] = []
    league: League | None = None
    region_tags: list[str] = []
    title_zh: str
    summary_zh: str = ""
    why_it_matters: str | None = None
    original_title: str | None = None
    published_at: AwareDatetime | None = None  # 事件中最早的來源時間
    ai_processed: bool = False  # 這則有沒有經過 LLM 分類與翻譯
    sources: list[SourceRef]


class MarketQuote(BaseModel):
    symbol: str  # settings.yaml 的 markets.items[].id
    name: str
    price: float | None = None  # None＝抓不到，前端顯示「—」
    change: float | None = None
    change_pct: float | None = None
    currency: str | None = None
    unit: str | None = None  # 例如殖利率的「%」
    decimals: int = 2
    as_of: AwareDatetime | None = None
    date_only: bool = False  # 來源只有日期（FRED），前端只顯示日期
    source: str | None = None  # cnyes / fred


class TimeWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime


class ProviderUsage(BaseModel):
    provider: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LLMUsage(BaseModel):
    provider: str | None = None  # 呼叫次數最多的 provider；完全沒用到 LLM 時為 None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    degraded: bool = True  # True＝整刊都沒有經過 AI 整理
    breakdown: list[ProviderUsage] = []
    errors: list[str] = []


class SourceError(BaseModel):
    source_id: str
    error: str


class Edition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    edition_id: str
    edition_type: EditionType
    published_at: AwareDatetime
    window: TimeWindow
    briefing: list[str] = []
    headlines: list[str] = []
    markets: list[MarketQuote] = []
    events: list[Event] = []
    llm_usage: LLMUsage = LLMUsage()
    source_errors: list[SourceError] = []

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2, exclude_none=False)


class EditionIndexEntry(BaseModel):
    edition_id: str
    edition_type: EditionType
    published_at: datetime
    event_count: int
    degraded: bool


class EditionIndex(BaseModel):
    editions: list[EditionIndexEntry] = []  # 新的在前
