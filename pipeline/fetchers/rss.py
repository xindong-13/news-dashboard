"""一般 RSS / Atom 來源。"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import feedparser

from pipeline.http import PoliteClient
from pipeline.models import RawItem, SourceConfig
from pipeline.textutil import parse_datetime, strip_html, truncate

_TZ_ABBR_RE = re.compile(r"\s+[A-Z]{2,5}$")


def normalize_date_string(raw: str) -> str:
    """修正常見的不標準寫法，例如 ETtoday 的 'Sun,15 Nov 2026 21:39:00  +0800'。"""
    raw = re.sub(r"^([A-Za-z]{3}),(\S)", r"\1, \2", raw.strip())
    return re.sub(r"\s+", " ", raw)


def entry_time(entry, date_tz: str | None = None) -> datetime | None:
    raw = entry.get("published") or entry.get("updated") or ""
    dt: datetime | None = None
    if date_tz and raw:
        # 來源的時區縮寫不可信（例如 VLR.gg 標 CST 實為 UTC+8），去掉縮寫改用指定時區
        stripped = _TZ_ABBR_RE.sub("", normalize_date_string(raw))
        dt = parse_datetime(stripped, naive_tz=ZoneInfo(date_tz))
    if dt is None:
        st = entry.get("published_parsed") or entry.get("updated_parsed")
        if st:
            dt = datetime.fromtimestamp(calendar.timegm(st), tz=UTC)
    if dt is None and raw:
        dt = parse_datetime(normalize_date_string(raw))
    if dt is None or dt.year < 2000:  # 例如 1970-01-01 這種空值
        return None
    return dt


def split_google_news_title(title: str, publisher: str | None) -> tuple[str, str | None]:
    """Google 新聞的標題結尾會附「 - 媒體名」，把它切開。"""
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3].strip(), publisher
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if 0 < len(tail) <= 30:
            return head.strip(), tail.strip()
    return title, publisher


def fetch_rss(source: SourceConfig, client: PoliteClient) -> Iterator[RawItem]:
    resp = client.get(source.url, ignore_robots=source.ignore_robots)
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        if feed.bozo:
            raise ValueError(f"RSS 解析失敗：{feed.bozo_exception}")
        return

    is_google = urlsplit(source.url).netloc.endswith("news.google.com")

    for entry in feed.entries:
        title = strip_html(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        name = source.name
        summary = strip_html(entry.get("summary"))
        if is_google:
            publisher = (entry.get("source") or {}).get("title")
            title, publisher = split_google_news_title(title, publisher)
            if publisher:
                name = f"{publisher}（{source.name}）"
            summary = ""  # Google 新聞的 description 只是重複標題加連結
        if summary == title:
            summary = ""

        yield RawItem(
            source_id=source.id,
            source_name=name,
            title=title,
            url=link,
            published_at=entry_time(entry, source.date_tz),
            summary=truncate(summary),
            language=source.language,
            category=source.category,
            weight=source.weight,
            subcategory=source.subcategory,
            game=source.game,
            league=source.league,
        )
