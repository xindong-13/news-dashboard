"""type: api 的來源，各自有一個解析器（parser 欄位指定）。"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from urllib.parse import quote

from pipeline.http import PoliteClient
from pipeline.models import League, RawItem, SourceConfig
from pipeline.textutil import TAIPEI, strip_html, truncate

ApiParser = Callable[[SourceConfig, PoliteClient], Iterator[RawItem]]


def _base(source: SourceConfig, **kw) -> dict:
    return (
        dict(
            source_id=source.id,
            source_name=source.name,
            language=source.language,
            category=source.category,
            weight=source.weight,
            subcategory=source.subcategory,
            game=source.game,
            league=source.league,
        )
        | kw
    )


# ---------------------------------------------------------------------------
# 鉅亨網（api.cnyes.com）
# ---------------------------------------------------------------------------


def parse_cnyes(source: SourceConfig, client: PoliteClient) -> Iterator[RawItem]:
    data = client.get(source.url, ignore_robots=source.ignore_robots).json()
    for it in data["items"]["data"]:
        title = strip_html(it.get("title"))
        news_id = it.get("newsId")
        if not title or not news_id:
            continue
        ts = it.get("publishAt")
        yield RawItem(
            **_base(source),
            title=title,
            url=f"https://news.cnyes.com/news/id/{news_id}",
            published_at=datetime.fromtimestamp(ts, tz=UTC) if ts else None,
            summary=truncate(strip_html(it.get("summary"))),
        )


# ---------------------------------------------------------------------------
# 證交所 OpenAPI 新聞（日期是民國年 1151113）
# ---------------------------------------------------------------------------


def _roc_date(s: str) -> datetime | None:
    m = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})", (s or "").strip())
    if not m:
        return None
    y, mo, d = int(m[1]) + 1911, int(m[2]), int(m[3])
    return datetime(y, mo, d, tzinfo=TAIPEI)


def parse_twse_news(source: SourceConfig, client: PoliteClient) -> Iterator[RawItem]:
    for it in client.get(source.url, ignore_robots=source.ignore_robots).json():
        title = strip_html(it.get("Title"))
        url = it.get("Url")
        if not title or not url:
            continue
        yield RawItem(
            **_base(source),
            title=title,
            url=url,
            published_at=_roc_date(it.get("Date")),
            date_only=True,
        )


# ---------------------------------------------------------------------------
# Leaguepedia（lol.fandom.com）Cargo：NewsItems 表
# ---------------------------------------------------------------------------

_FILE_LINK_RE = re.compile(r"\[\[(?:File|Image):[^\]]*\]\]", re.IGNORECASE)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_INVISIBLE_RE = re.compile(r"[​⁠﻿]")  # 零寬空白、word joiner、BOM
_REGION_LEAGUE: dict[str, League] = {
    "Korea": "lck",
    "China": "lpl",
    "International": "international",
}


def clean_wikitext(text: str) -> str:
    """把 Leaguepedia 的維基語法轉成純文字：去掉圖片、[[頁面|顯示文字]] 只留顯示文字。"""
    text = _FILE_LINK_RE.sub("", text or "")
    text = _WIKILINK_RE.sub(lambda m: m.group(1).split("|")[-1], text)
    text = text.replace("'''", "").replace("''", "")
    text = strip_html(text)
    return _INVISIBLE_RE.sub("", text).strip()


def _first_http_source(raw: str) -> str | None:
    # 格式：url;;;標題;;;網域;;;...:::url;;;...
    for chunk in (raw or "").split(":::"):
        url = chunk.split(";;;", 1)[0].strip()
        if url.startswith("http"):
            return url
    return None


def parse_leaguepedia_news(source: SourceConfig, client: PoliteClient) -> Iterator[RawItem]:
    data = client.get(source.url, ignore_robots=source.ignore_robots).json()
    if "error" in data:
        raise ValueError(f"Cargo API 錯誤：{data['error'].get('info')}")
    for row in data.get("cargoquery", []):
        f = row["title"]
        sentence = clean_wikitext(f.get("Sentence", ""))
        if not sentence:
            continue
        date = datetime.strptime(f["Date Sort"][:10], "%Y-%m-%d").replace(tzinfo=TAIPEI)
        team = (f.get("Teams") or "").split(",")[0].strip()
        url = _first_http_source(f.get("Source", ""))
        if not url:
            page = team or f"News/{f['Date Sort'][:10]}"
            url = "https://lol.fandom.com/wiki/" + quote(page.replace(" ", "_"))
        league = _REGION_LEAGUE.get(f.get("Region", ""), source.league or "other")
        yield RawItem(
            **_base(source, league=league),
            title=truncate(sentence, 200),
            url=url,
            published_at=date,
            date_only=True,
            summary=f"Teams: {f.get('Teams', '')}；Players: {f.get('Players', '')}；Region: {f.get('Region', '')}",
        )


PARSERS: dict[str, ApiParser] = {
    "cnyes": parse_cnyes,
    "twse_news": parse_twse_news,
    "leaguepedia_news": parse_leaguepedia_news,
}
