from datetime import datetime

from pipeline.models import RawItem
from pipeline.textutil import TAIPEI


def tpe(s: str) -> datetime:
    """'2026-11-15 11:40' → 台北時間 datetime。"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TAIPEI)


def item(
    title: str,
    url: str,
    *,
    source: str = "src",
    name: str | None = None,
    at: str | None = "2026-11-15 10:00",
    category: str = "taiwan",
    language: str = "zh-TW",
    weight: float = 1.0,
    **kw,
) -> RawItem:
    return RawItem(
        source_id=source,
        source_name=name or source,
        title=title,
        url=url,
        published_at=tpe(at) if at else None,
        language=language,
        category=category,
        weight=weight,
        **kw,
    )
