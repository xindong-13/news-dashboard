"""讀取 config/settings.yaml 與 config/sources.yaml。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from pipeline.models import EsportsTag, League, SourceConfig

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
    title_similarity_threshold: int = 85
    summarize_top_n: int = 80
    summarize_min_importance: int = 2
    headlines_min: int = 5
    headlines_max: int = 8


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
    max_output_tokens: int = 8192


class LLMSettings(_Strict):
    providers: list[ProviderSettings]
    batch_size: int = 30
    request_timeout_seconds: float = 120
    retry_backoff_seconds: float = 20


class MarketItem(_Strict):
    symbol: str
    name: str
    twse_fallback: str | None = None  # TWSE OpenAPI 備援的代號（例如 TAIEX、2330）


class MarketSettings(_Strict):
    items: list[MarketItem]


class Settings(_Strict):
    timezone: str = "Asia/Taipei"
    http: HttpSettings
    edition: EditionSettings = EditionSettings()
    processing: ProcessingSettings = ProcessingSettings()
    esports: EsportsSettings = EsportsSettings()
    llm: LLMSettings
    markets: MarketSettings


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
