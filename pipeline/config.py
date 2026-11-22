"""讀取 config/settings.yaml 與 config/sources.yaml。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pipeline.models import Category, EsportsTag, League, SourceConfig

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
EDITIONS_DIR = DATA_DIR / "editions"
PROMPTS_DIR = ROOT / "pipeline" / "prompts"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpSettings(_Strict):
    user_agent: str
    timeout_seconds: float = 20
    per_domain_delay_seconds: float = 1.0
    domain_delays: dict[str, float] = {}
    respect_robots_txt: bool = True


class EditionSettings(_Strict):
    noon_cutoff: str = "16:30"  # 台北時間；早於此時刻執行算午報，否則算晚報
    default_lookback_hours: int = 12
    max_window_hours: int = 24


class ProcessingSettings(_Strict):
    max_items_per_edition: int = 200
    title_similarity_threshold: int = 85  # 英文等拼音文字
    title_similarity_threshold_cjk: int = 60  # 中日韓文（各家改寫幅度大，分數普遍偏低）
    ai_title_similarity_threshold: int = 65  # LLM 改寫後的繁中標題比較一致，用來合併不同批的重複
    summarize_top_n: int = 80
    summarize_min_importance: int = 2
    edition_size: int = 20  # 每刊最後只留幾則（使用者一天看兩刊、約 40 則）
    edition_max_per_category: int | None = 5  # 每個分類先各幾則（某類不夠時逐次放寬）
    headlines_min: int = 5
    headlines_max: int = 8
    headlines_max_per_category: int | None = 2  # 每個分類最多幾則今日重點（None＝不限）
    headlines_exclude_categories: list[
        Category
    ] = []  # 這些分類不當焦點、不進導讀（例如 Threads 熱議）
    # 分類裡某些子分類至少要有幾則（有新聞的話），例如電競 {valorant: 1, cs2: 1}，不讓 LoL 佔滿
    edition_min_per_subcategory: dict[Category, dict[str, int]] = {}


class OffseasonPeriod(_Strict):
    start: str  # MM-DD
    end: str  # MM-DD，可跨年


class EsportsSettings(_Strict):
    league_priority: list[League] = ["lck", "lpl", "international", "other"]
    offseason: list[OffseasonPeriod] = []
    offseason_boost_tags: list[EsportsTag] = ["transfer", "roster"]


class ProviderSettings(_Strict):
    name: str  # gemini / groq / anthropic
    model: str
    enabled: bool = True
    api_key_env: str
    min_interval_seconds: float = 0  # 兩次呼叫之間至少間隔幾秒（配合免費額度的每分鐘上限）
    max_batch_items: int | None = None  # 這家一次最多送幾則（Groq 免費版每分鐘 token 少，要切小批）
    max_output_tokens: int | None = None  # 這家的輸出上限
    options: dict[str, Any] = {}  # provider 專屬參數，例如 thinking_level、reasoning_effort、effort


class LLMSettings(_Strict):
    providers: list[ProviderSettings]
    classify_batch_size: int = 25
    summarize_batch_size: int = 15
    request_timeout_seconds: float = 120
    retry_backoff_seconds: float = 20
    max_retry_wait_seconds: float = 90


class MarketItem(_Strict):
    id: str  # 自訂的代號，前端用來辨識（例如 taiex）
    name: str
    cnyes: str | None = None  # 鉅亨網報價代號，例如 TWS:2330:STOCK
    fred: str | None = None  # FRED 資料序列代號（鉅亨網沒有或抓不到時用），例如 DCOILWTICO
    decimals: int = 2  # 顯示幾位小數
    unit: str | None = None  # 顯示在數字後面，例如 %
    currency: str | None = None


class MarketSettings(_Strict):
    items: list[MarketItem]


class NotifySettings(_Strict):
    """出刊後推播（pipeline/notify.py）。沒設定 token 的管道會自動略過。"""

    site_url: str  # 網站網址，訊息裡的連結用，結尾要有 /
    line_token_env: str = "LINE_CHANNEL_ACCESS_TOKEN"
    line_to_env: str = "LINE_USER_ID"
    max_headlines: int = 5  # 訊息裡列幾則焦點標題


class Settings(_Strict):
    timezone: str = "Asia/Taipei"
    http: HttpSettings
    edition: EditionSettings = EditionSettings()
    processing: ProcessingSettings = ProcessingSettings()
    esports: EsportsSettings = EsportsSettings()
    llm: LLMSettings
    markets: MarketSettings
    notify: NotifySettings | None = None


class SourcesFile(_Strict):
    sources: list[SourceConfig] = Field(default_factory=list)


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def load_settings(path: Path | None = None) -> Settings:
    return Settings.model_validate(_load_yaml(path or CONFIG_DIR / "settings.yaml"))


def load_sources(path: Path | None = None, *, include_disabled: bool = False) -> list[SourceConfig]:
    data = SourcesFile.model_validate(_load_yaml(path or CONFIG_DIR / "sources.yaml"))
    ids = [s.id for s in data.sources]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"sources.yaml 有重複的 id：{sorted(dupes)}")
    return [s for s in data.sources if include_disabled or s.enabled]
