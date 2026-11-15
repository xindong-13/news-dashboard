"""時間範圍篩選、規則評分、各類別配額（送進 LLM 前先用規則篩到上限）。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from pipeline.models import Category, RawItem
from pipeline.processing.cluster import Cluster
from pipeline.processing.edition import EditionPlan
from pipeline.processing.normalize import normalize_url, title_key
from pipeline.textutil import TAIPEI

log = logging.getLogger(__name__)

FUTURE_TOLERANCE = timedelta(hours=1)
MIN_TITLE_CHARS = 4  # 去掉標點後少於這個字數的標題視為無意義
CATEGORIES: tuple[Category, ...] = ("taiwan", "world", "finance", "esports")


def filter_window(items: list[RawItem], plan: EditionPlan, seen_urls: set[str]) -> list[RawItem]:
    """只留本刊時間範圍內、且之前沒刊過的項目。

    - 沒有時間的項目：保留（靠 seen_urls 避免重複刊出）
    - 只有日期的項目（Leaguepedia 等）：日期 ≥ 範圍起點前一天就保留
    - 時間在未來（來源時區錯誤）：超過容忍值就丟掉
    """
    kept: list[RawItem] = []
    future = 0
    start_date = (plan.window_from.astimezone(TAIPEI) - timedelta(days=1)).date()
    for it in items:
        if len(title_key(it.title)) < MIN_TITLE_CHARS:  # 例如 LoL Esports 的「Vod」
            continue
        if normalize_url(it.url) in seen_urls:
            continue
        ts = it.published_at
        if ts is None:
            kept.append(it)
        elif it.date_only:
            if ts.astimezone(TAIPEI).date() >= start_date:
                kept.append(it)
        elif ts > plan.now + FUTURE_TOLERANCE:
            future += 1
        elif ts > plan.window_from:
            kept.append(it)
    if future:
        log.warning("丟掉 %d 則時間在未來的項目（可能是來源時區錯誤）", future)
    return kept


def cluster_score(c: Cluster, now: datetime) -> float:
    """規則分數：來源權重 × 報導家數加成 × 新鮮度。只用來排序與篩選，不是最終重要度。"""
    n = len(c.source_names)
    score = c.max_weight * (1 + 0.5 * min(n - 1, 4))
    p = c.primary
    if p.category == "esports" and any(i.league in ("lck", "lpl") for i in c.items):
        score *= 1.3
    times = [i.published_at for i in c.items if i.published_at and not i.date_only]
    if times:
        age_h = max((now - max(times)).total_seconds() / 3600, 0)
        score *= 1.0 if age_h < 6 else 0.85 if age_h < 12 else 0.7
    return score


def select_clusters(clusters: list[Cluster], limit: int, now: datetime) -> list[Cluster]:
    """先保證每個類別至少 limit/4 的名額（避免電競被大量政治新聞擠掉），再依分數補滿。"""
    scored = sorted(clusters, key=lambda c: cluster_score(c, now), reverse=True)
    if len(scored) <= limit:
        return scored

    by_cat: dict[str, list[Cluster]] = defaultdict(list)
    for c in scored:
        by_cat[c.primary.category].append(c)

    quota = limit // len(CATEGORIES)
    chosen: list[Cluster] = []
    for cat in CATEGORIES:
        chosen.extend(by_cat[cat][:quota])
    chosen_ids = {id(c) for c in chosen}
    for c in scored:
        if len(chosen) >= limit:
            break
        if id(c) not in chosen_ids:
            chosen.append(c)
    log.info("事件 %d 則，依規則篩到 %d 則", len(scored), len(chosen))
    return sorted(chosen, key=lambda c: cluster_score(c, now), reverse=True)
