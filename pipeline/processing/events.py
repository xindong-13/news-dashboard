"""把合併好的 Cluster 轉成 Event（降級版：不經 LLM，只用規則）。"""

from __future__ import annotations

from pipeline.models import Event, SourceRef
from pipeline.processing.cluster import Cluster
from pipeline.processing.normalize import normalize_url


def sources_of(c: Cluster) -> list[SourceRef]:
    seen: set[str] = set()
    refs: list[SourceRef] = []
    for it in sorted(c.items, key=lambda i: (i.published_at is None, i.published_at)):
        key = normalize_url(it.url)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            SourceRef(
                name=it.source_name, url=it.url, published_at=it.published_at, language=it.language
            )
        )
    return refs


def rule_importance(c: Cluster) -> int:
    """降級時的重要度：依報導家數與來源權重估計，最高 4（5 留給 AI 判斷）。"""
    n = len(c.source_names)
    score = 2 + (n >= 2) + (n >= 4)
    if n == 1 and c.max_weight < 0.9:
        score -= 1
    return max(1, min(score, 4))


def degraded_event(c: Cluster, event_id: str) -> Event:
    p = c.primary
    times = [i.published_at for i in c.items if i.published_at]
    leagues = [i.league for i in c.items if i.league]
    return Event(
        id=event_id,
        category=p.category,
        subcategory=p.subcategory or next((i.subcategory for i in c.items if i.subcategory), None),
        importance=rule_importance(c),
        league=leagues[0] if p.game == "lol" and leagues else None,
        title_zh=p.title,
        summary_zh=next((i.summary for i in c.items if i.summary), ""),
        original_title=p.title,
        published_at=min(times) if times else None,
        sources=sources_of(c),
    )


def select_for_edition(events: list[Event], size: int, per_category: int | None) -> list[Event]:
    """每刊只留 size 則：重要度高的優先，各分類有上限（避免某一類佔滿），保持原本順序。"""
    keep = set(pick_headlines(events, size, size, per_category))
    return [e for e in events if e.id in keep]


def pick_headlines(
    events: list[Event], lo: int, hi: int, per_category: int | None = None
) -> list[str]:
    """今日重點：重要度最高的 lo～hi 則；重要度 ≥ 4 的全收（最多 hi），不足 lo 則往下補。

    per_category：每個分類最多幾則，避免休賽季的轉會新聞（重要度 +1）佔滿今日重點。
    名額不夠時每次把上限放寬 1 則再往下補，讓多出來的名額盡量平均分給各分類。
    """
    ranked = sorted(events, key=lambda e: (e.importance, len(e.sources)), reverse=True)
    cap = per_category or hi
    picked: list[Event] = []
    count: dict[str, int] = {}
    while True:
        for e in ranked:
            if len(picked) >= hi or (e.importance < 4 and len(picked) >= lo):
                break
            if e in picked or count.get(e.category, 0) >= cap:
                continue
            picked.append(e)
            count[e.category] = count.get(e.category, 0) + 1
        if len(picked) >= lo or len(picked) == len(ranked) or cap >= hi:
            break
        cap += 1  # 分類上限太緊時放寬
    picked.sort(key=lambda e: (e.importance, len(e.sources)), reverse=True)
    return [e.id for e in picked]
