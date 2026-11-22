"""用 LLM 分類、翻標題、合併跨語言重複、寫摘要與導讀。

任何一步失敗都不會讓整刊失敗：
- 分類失敗的批次 → 那幾則用降級版（規則）卡片
- 摘要失敗 → 那幾則沒有摘要
- 導讀失敗 → 沒有導讀
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from rapidfuzz import fuzz, process

from pipeline.config import PROMPTS_DIR, OffseasonPeriod, Settings
from pipeline.llm.router import AllProvidersFailed, LLMRouter
from pipeline.llm.schemas import (
    BRIEFING_SCHEMA,
    CLASSIFY_SCHEMA,
    DEDUPE_SCHEMA,
    SUMMARY_SCHEMA,
    BriefingOutput,
    ClassifiedItem,
    ClassifyOutput,
    DedupeOutput,
    SummaryOutput,
)
from pipeline.models import SUBCATEGORIES, EditionType, Event
from pipeline.processing.cluster import MAX_CLUSTER_SIZE, MIN_KEY_LEN_CJK, Cluster, publisher
from pipeline.processing.events import (
    degraded_event,
    pick_headlines,
    select_for_edition,
    sources_of,
)
from pipeline.processing.normalize import title_key
from pipeline.taiwan_terms import to_taiwan
from pipeline.textutil import TAIPEI, truncate

log = logging.getLogger(__name__)

EDITION_NAMES = {"noon": "午報", "night": "晚報"}


def load_prompt(name: str, **values: str) -> str:
    """讀 pipeline/prompts/{name}.md，替換 {{key}}。"""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def in_offseason(d: date, periods: list[OffseasonPeriod]) -> bool:
    md = d.strftime("%m-%d")
    for p in periods:
        if p.start <= p.end:
            if p.start <= md <= p.end:
                return True
        elif md >= p.start or md <= p.end:  # 跨年，例如 11-01 ～ 01-31
            return True
    return False


def _batches(groups: list[list], base_size: int, router: LLMRouter) -> Iterator[list]:
    """邊跑邊切批次：每批的大小依「當下會用的 provider」決定。

    例如 Gemini 中途停用、改用 Groq 之後，剩下的會自動切成 Groq 吃得下的小批。
    """
    for group in groups:
        i = 0
        while i < len(group):
            size = router.batch_limit(base_size)
            yield group[i : i + size]
            i += size


@dataclass
class _Work:
    """處理中的一個事件。"""

    cluster: Cluster
    cls: ClassifiedItem | None = None
    merged_into: str | None = None
    summary: str = ""
    why: str | None = None
    extra: list[Cluster] = field(default_factory=list)  # LLM 判定重複而併進來的

    @property
    def items(self):
        out = list(self.cluster.items)
        for c in self.extra:
            out.extend(c.items)
        return out


@dataclass
class AIResult:
    events: list[Event]
    headlines: list[str]
    briefing: list[str]


def _classify_input(tid: str, c: Cluster) -> dict:
    p = c.primary
    snippet = next((i.summary for i in c.items if i.summary), "")
    return {
        "id": tid,
        "title": p.title,
        "snippet": truncate(snippet, 160),
        "sources": sorted({publisher(i) for i in c.items})[:5],
        "lang": p.language,
        # 不給 league 提示：來源設定的 league（例如「LPL 搜尋結果」）常常和新聞實際賽區不同，
        # 實測會誤導 LLM 把 LCK 的戰隊標成 LPL。賽區交給 LLM 依戰隊判斷。
        "hint": {"category": p.category, "subcategory": p.subcategory, "game": p.game},
    }


def classify(work: dict[str, _Work], router: LLMRouter, settings: Settings, today: date) -> None:
    note = ""
    if in_offseason(today, settings.esports.offseason):
        tags = "、".join(settings.esports.offseason_boost_tags)
        note = (
            f"- **現在是 LoL 休賽季（轉會期）**：LCK／LPL 已證實的轉會、續約、陣容異動"
            f"（esports_tags 含 {tags}）重要度再高一級；傳聞仍然最高 3"
        )
    system = load_prompt("classify", today=today.isoformat(), offseason_note=note)

    # 同類別放在同一批，LLM 比較容易抓到重複
    by_cat: dict[str, list[str]] = defaultdict(list)
    for tid, w in work.items():
        by_cat[w.cluster.primary.category].append(tid)
    batches = _batches(list(by_cat.values()), settings.llm.classify_batch_size, router)

    for n, ids in enumerate(batches, start=1):
        if not router.available:
            break
        payload = [_classify_input(tid, work[tid].cluster) for tid in ids]
        user = f"以下是 {len(ids)} 則新聞：\n" + json.dumps(payload, ensure_ascii=False)
        try:
            out = router.run(
                f"分類第 {n} 批",
                system,
                user,
                ClassifyOutput,
                CLASSIFY_SCHEMA,
                max_output_tokens=8192,
            )
        except AllProvidersFailed:
            log.warning("分類第 %d 批失敗，這 %d 則改用降級版", n, len(ids))
            continue
        batch_ids = set(ids)
        for item in out.items:
            if item.id in batch_ids:
                work[item.id].cls = item
        missing = [tid for tid in ids if work[tid].cls is None]
        if missing:
            log.warning("分類第 %d 批有 %d 則沒有回傳結果", n, len(missing))


def _absorb(work: dict[str, _Work], src: str, dst: str) -> None:
    t, w = work[dst], work[src]
    t.extra.append(w.cluster)
    t.extra.extend(w.extra)
    w.extra = []
    w.merged_into = dst


def merge_duplicates(work: dict[str, _Work]) -> int:
    """依 LLM 的 duplicate_of 把重複事件併進主要事件。回傳併掉的數量。"""
    merged = 0
    for tid, w in work.items():
        target = w.cls.duplicate_of if w.cls else None
        if not target or target == tid or target not in work or w.merged_into:
            continue
        t = work[target]
        if t.cls is None or t.merged_into is not None:  # 只併進「有分類、本身不是重複」的事件
            continue
        _absorb(work, tid, target)
        merged += 1
    return merged


def merge_similar_titles(work: dict[str, _Work], threshold: int) -> int:
    """分類完後所有標題都是繁中，再用標題相似度合併一次。

    分類是分批送的，不同批的同一事件 LLM 看不到彼此（例如英文與中文的世界賽冠軍報導），
    這一步補上。和規則合併一樣，同一家媒體的報導不合併。
    """
    alive = [tid for tid, w in work.items() if w.cls and not w.merged_into]
    order = {tid: i for i, tid in enumerate(work)}
    by_cat: dict[str, list[str]] = defaultdict(list)
    for tid in alive:
        if len(title_key(work[tid].cls.title_zh)) >= MIN_KEY_LEN_CJK:
            by_cat[work[tid].cls.category].append(tid)

    merged = 0
    for ids in by_cat.values():
        if len(ids) < 2:
            continue
        keys = [title_key(work[t].cls.title_zh) for t in ids]
        scores = process.cdist(keys, keys, scorer=fuzz.ratio, score_cutoff=threshold, workers=-1)
        rows, cols = np.nonzero(np.triu(scores, k=1))
        for r, c in sorted(zip(rows, cols, strict=True), key=lambda rc: -scores[rc[0], rc[1]]):
            a, b = ids[r], ids[c]
            wa, wb = work[a], work[b]
            if wa.merged_into or wb.merged_into:
                continue
            if {publisher(i) for i in wa.items} & {publisher(i) for i in wb.items}:
                continue
            if len(wa.items) + len(wb.items) > MAX_CLUSTER_SIZE * 2:
                continue
            # 併進重要度高的那則（相同時取原本排序較前的）
            ka = (wa.cls.importance, -order[a])
            kb = (wb.cls.importance, -order[b])
            dst, src = (a, b) if ka >= kb else (b, a)
            _absorb(work, src, dst)
            merged += 1
    return merged


# 專有名詞重疊：中文標題裡的英文隊名、選手名、公司名
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
NAME_STOPWORDS = frozenset(
    "lck lpl lec lcs lol vct msi cs2 ai the and of to in for on vs with etf ceo us usa gdp cpi fed".split()
)
NAME_MIN_SHARED = 3
NAME_MIN_OVERLAP = 0.75


# 同一隊的全名與簡稱（比對前先把全名換成簡稱）。2026-11-22「Dplus KIA 簽下 Doran 與 Kanavi」
# 和「DK 網羅 Doran 與 Kanavi」是同一件事，但共用的名詞只有兩個，沒有合併。
# 只放常見的 LoL／Valorant／CS2 隊伍；漏了再補。
TEAM_ALIASES: dict[str, str] = {
    "dplus kia": "dk",
    "dplus": "dk",
    "damwon kia": "dk",
    "damwon": "dk",
    "hanwha life esports": "hle",
    "hanwha life": "hle",
    "hanwha": "hle",
    "gen.g": "geng",
    "gen g": "geng",
    "kt rolster": "kt",
    "nongshim redforce": "ns",
    "nongshim": "ns",
    "bnk fearx": "fox",
    "fearx": "fox",
    "oksavingsbank brion": "bro",
    "ok brion": "bro",
    "brion": "bro",
    "kwangdong freecs": "kdf",
    "dn freecs": "kdf",
    "freecs": "kdf",
    "bilibili gaming": "blg",
    "bilibili": "blg",
    "top esports": "tes",
    "jd gaming": "jdg",
    "weibo gaming": "wbg",
    "anyone's legend": "al",
    "invictus gaming": "ig",
    "edward gaming": "edg",
    "royal never give up": "rng",
    "lng esports": "lng",
    "ninjas in pyjamas": "nip",
    "g2 esports": "g2",
    "team liquid": "tl",
    "fnatic": "fnc",
    "cloud9": "c9",
    "team vitality": "vitality",
    "natus vincere": "navi",
    "paper rex": "prx",
}
_ALIAS_RE = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(re.escape(k) for k in sorted(TEAM_ALIASES, key=len, reverse=True))
    + r")(?![a-z0-9])"
)
NAME_STOPWORDS = NAME_STOPWORDS | {"gaming", "esports", "team"}


def name_tokens(title: str) -> set[str]:
    text = _ALIAS_RE.sub(lambda m: f" {TEAM_ALIASES[m.group(1)]} ", title.lower())
    return set(NAME_RE.findall(text)) - NAME_STOPWORDS


def merge_shared_names(work: dict[str, _Work]) -> int:
    """同類別、標題裡的英文專有名詞幾乎相同的兩則，視為同一件事合併。

    電競轉會新聞常是「隊名＋兩三位選手名」，各語言翻成中文後措辭差很多，
    標題相似度抓不到、LLM 去重也會漏（2026-11-21 的 Dplus KIA 簽 Doran、Kanavi）。
    條件：至少共用 3 個名詞，且占較短那則名詞數的 75% 以上；同一家媒體不合併。
    """
    alive = [tid for tid, w in work.items() if w.cls and not w.merged_into]
    order = {tid: i for i, tid in enumerate(work)}
    names = {tid: name_tokens(work[tid].cls.title_zh) for tid in alive}
    pairs = []
    for i, a in enumerate(alive):
        for b in alive[i + 1 :]:
            if work[a].cls.category != work[b].cls.category:
                continue
            shared = len(names[a] & names[b])
            if shared < NAME_MIN_SHARED:
                continue
            overlap = shared / min(len(names[a]), len(names[b]))
            if overlap >= NAME_MIN_OVERLAP:
                pairs.append((overlap, shared, a, b))

    merged = 0
    for _, _, a, b in sorted(pairs, reverse=True):
        wa, wb = work[a], work[b]
        if wa.merged_into or wb.merged_into:
            continue
        if {publisher(i) for i in wa.items} & {publisher(i) for i in wb.items}:
            continue
        if len(wa.items) + len(wb.items) > MAX_CLUSTER_SIZE * 2:
            continue
        ka = (wa.cls.importance, len(wa.items), -order[a])
        kb = (wb.cls.importance, len(wb.items), -order[b])
        dst, src = (a, b) if ka >= kb else (b, a)
        if work[src].cls.importance > work[dst].cls.importance:
            work[dst].cls = work[dst].cls.model_copy(
                update={"importance": work[src].cls.importance}
            )
        _absorb(work, src, dst)
        merged += 1
    return merged


DEDUPE_MIN_IMPORTANCE = 3
DEDUPE_MAX_TITLES = 150


def dedupe_with_llm(work: dict[str, _Work], router: LLMRouter) -> int:
    """把重要度 ≥ 3 的標題一次交給 LLM，找出分在不同批的同一事件。失敗就跳過。"""
    alive = [
        tid
        for tid, w in work.items()
        if w.cls and not w.merged_into and w.cls.importance >= DEDUPE_MIN_IMPORTANCE
    ]
    alive.sort(key=lambda t: -work[t].cls.importance)
    alive = alive[:DEDUPE_MAX_TITLES]
    if len(alive) < 2 or not router.available:
        return 0
    payload = [
        {"id": t, "category": work[t].cls.category, "title": work[t].cls.title_zh} for t in alive
    ]
    try:
        out = router.run(
            "跨批去重",
            load_prompt("dedupe"),
            json.dumps(payload, ensure_ascii=False),
            DedupeOutput,
            DEDUPE_SCHEMA,
            max_output_tokens=4096,
        )
    except AllProvidersFailed:
        log.warning("跨批去重失敗，略過")
        return 0

    allowed = set(alive)
    merged = 0
    for g in out.groups:
        keep = g.keep
        if keep not in allowed or work[keep].merged_into:
            continue
        for dup in g.duplicates:
            if dup == keep or dup not in allowed or work[dup].merged_into:
                continue
            if work[dup].cls.category != work[keep].cls.category:
                continue  # 不同類別不合併，避免 LLM 誤判
            if len(work[keep].items) + len(work[dup].items) > MAX_CLUSTER_SIZE * 2:
                continue
            k, d = work[keep].cls, work[dup].cls
            if d.importance > k.importance:  # 合併後取較高的重要度
                work[keep].cls = k.model_copy(update={"importance": d.importance})
            _absorb(work, dup, keep)
            merged += 1
    return merged


def _event_from(tid: str, w: _Work) -> Event:
    c = w.cls
    assert c is not None
    cat = c.category
    sub = c.subcategory if c.subcategory in SUBCATEGORIES[cat] else "other"
    importance = c.importance
    tags = list(dict.fromkeys(c.esports_tags)) if cat == "esports" else []

    merged = Cluster(items=w.items)
    times = [i.published_at for i in merged.items if i.published_at]
    return Event(
        id=tid,
        category=cat,
        subcategory=sub,
        importance=importance,
        esports_tags=tags,
        league=c.league if cat == "esports" and sub == "lol" else None,
        region_tags=list(dict.fromkeys(c.region_tags)),
        title_zh=to_taiwan(c.title_zh.strip()),
        original_title=w.cluster.primary.title,
        published_at=min(times) if times else None,
        ai_processed=True,
        sources=sources_of(merged),
    )


def summarize(
    events: list[Event], work: dict[str, _Work], router: LLMRouter, settings: Settings, today: date
) -> None:
    proc = settings.processing
    targets = [
        e for e in events if e.ai_processed and e.importance >= proc.summarize_min_importance
    ][: proc.summarize_top_n]
    if not targets:
        return
    system = load_prompt("summarize", today=today.isoformat())
    by_id = {e.id: e for e in events}
    batches = _batches([targets], settings.llm.summarize_batch_size, router)
    for n, batch in enumerate(batches, start=1):
        if not router.available:
            break
        payload = []
        for e in batch:
            items = work[e.id].items[:4]
            payload.append(
                {
                    "id": e.id,
                    "title_zh": e.title_zh,
                    "category": e.category,
                    "importance": e.importance,
                    "materials": [
                        {
                            "source": publisher(i),
                            "title": i.title,
                            "snippet": truncate(i.summary, 200),
                        }
                        for i in items
                    ],
                }
            )
        user = f"以下是 {len(batch)} 則新聞事件：\n" + json.dumps(payload, ensure_ascii=False)
        try:
            out = router.run(
                f"摘要第 {n} 批",
                system,
                user,
                SummaryOutput,
                SUMMARY_SCHEMA,
                max_output_tokens=8192,
            )
        except AllProvidersFailed:
            log.warning("摘要第 %d 批失敗，這 %d 則沒有摘要", n, len(batch))
            continue
        ids = {e.id for e in batch}
        for s in out.items:
            if s.id not in ids or not s.summary_zh.strip():
                continue
            ev = by_id[s.id]
            ev.summary_zh = to_taiwan(s.summary_zh.strip())
            if ev.importance >= 4 and s.why_it_matters:
                ev.why_it_matters = to_taiwan(s.why_it_matters.strip())


def write_briefing(
    events: list[Event],
    headlines: list[str],
    router: LLMRouter,
    edition_type: EditionType,
    today: date,
) -> list[str]:
    if not router.available:
        return []
    by_id = {e.id: e for e in events}
    picked = [by_id[h] for h in headlines]
    picked += [e for e in events if e.ai_processed and e.id not in headlines][: 12 - len(picked)]
    payload = [
        {"title_zh": e.title_zh, "category": e.category, "summary_zh": e.summary_zh} for e in picked
    ]
    system = load_prompt(
        "briefing", today=today.isoformat(), edition_name=EDITION_NAMES[edition_type]
    )
    try:
        out = router.run(
            "導讀",
            system,
            json.dumps(payload, ensure_ascii=False),
            BriefingOutput,
            BRIEFING_SCHEMA,
            max_output_tokens=2048,
        )
    except AllProvidersFailed:
        log.warning("導讀失敗，本刊沒有導讀")
        return []
    return [to_taiwan(b.strip()) for b in out.briefing if b.strip()][:5]


def process_with_llm(
    clusters: list[Cluster],
    router: LLMRouter,
    settings: Settings,
    now: datetime,
    edition_type: EditionType,
) -> AIResult:
    today = now.astimezone(TAIPEI).date()
    work = {f"c{i}": _Work(cluster=c) for i, c in enumerate(clusters, start=1)}

    classify(work, router, settings, today)
    n_ai = sum(1 for w in work.values() if w.cls)
    merged = merge_duplicates(work)
    similar = merge_similar_titles(work, settings.processing.ai_title_similarity_threshold)
    cross = dedupe_with_llm(work, router)
    names = merge_shared_names(work)
    log.info(
        "LLM 分類 %d／%d 則；合併重複：同批 %d、標題相似 %d、跨批 %d、專有名詞 %d",
        n_ai,
        len(work),
        merged,
        similar,
        cross,
        names,
    )

    # 組成事件：有分類的用 AI 版，沒有的用降級版；依重要度排序後重新編號
    order = {tid: i for i, tid in enumerate(work)}
    drafts: list[tuple[str, Event]] = []
    for tid, w in work.items():
        if w.merged_into:
            continue
        ev = _event_from(tid, w) if w.cls else degraded_event(Cluster(items=w.items), tid)
        drafts.append((tid, ev))
    drafts.sort(key=lambda d: (-d[1].importance, order[d[0]]))
    # 分類、去重都做完後才篩到每刊的則數，只替留下來的寫摘要
    proc = settings.processing
    kept = {
        e.id
        for e in select_for_edition(
            [ev for _, ev in drafts], proc.edition_size, proc.edition_max_per_category
        )
    }
    log.info("每刊只留 %d 則（共 %d 則）", len(kept), len(drafts))
    drafts = [d for d in drafts if d[1].id in kept]

    events: list[Event] = []
    final_work: dict[str, _Work] = {}
    for i, (tid, ev) in enumerate(drafts, start=1):
        new_id = f"evt_{i:03d}"
        events.append(ev.model_copy(update={"id": new_id}))
        final_work[new_id] = work[tid]

    summarize(events, final_work, router, settings, today)
    headlines = pick_headlines(
        events, proc.headlines_min, proc.headlines_max, proc.headlines_max_per_category
    )
    briefing = write_briefing(events, headlines, router, edition_type, today) if n_ai else []
    return AIResult(events=events, headlines=headlines, briefing=briefing)
