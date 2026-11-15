"""抓取入口：依來源類型分派，並套用關鍵字篩選與數量上限。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pipeline.fetchers.apis import PARSERS
from pipeline.fetchers.rss import fetch_rss
from pipeline.http import PoliteClient
from pipeline.models import RawItem, SourceConfig, SourceError

_EPOCH = datetime.min.replace(tzinfo=UTC)


def _keep(item: RawItem, source: SourceConfig) -> bool:
    title = item.title.lower()
    if source.keywords and not any(k.lower() in title for k in source.keywords):
        return False
    if source.exclude_keywords and any(k.lower() in title for k in source.exclude_keywords):
        return False
    return True


def fetch_source(source: SourceConfig, client: PoliteClient) -> list[RawItem]:
    if source.type == "rss":
        it = fetch_rss(source, client)
    elif source.type == "api":
        parser = PARSERS.get(source.parser or "")
        if parser is None:
            raise ValueError(f"未知的 api parser：{source.parser!r}")
        it = parser(source, client)
    else:  # pragma: no cover - Literal 已擋掉
        raise ValueError(f"未知的來源類型：{source.type}")

    items = [i for i in it if _keep(i, source)]
    items.sort(key=lambda i: i.published_at or _EPOCH, reverse=True)
    return items[: source.max_items]


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def fetch_all(sources: list[SourceConfig], client: PoliteClient) -> FetchResult:
    """逐一抓取；單一來源失敗只記錄錯誤，不中斷。"""
    result = FetchResult()
    for source in sources:
        try:
            items = fetch_source(source, client)
        except Exception as e:  # noqa: BLE001 - 任何錯誤都不能讓整刊失敗
            msg = f"{type(e).__name__}: {e}"[:300]
            result.errors.append(SourceError(source_id=source.id, error=msg))
            result.counts[source.id] = -1
            continue
        result.items.extend(items)
        result.counts[source.id] = len(items)
    return result
