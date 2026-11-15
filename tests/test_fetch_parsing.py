from datetime import UTC, datetime, timedelta, timezone

from pipeline.fetchers.apis import _first_http_source, _roc_date, clean_wikitext
from pipeline.fetchers.rss import entry_time, normalize_date_string, split_google_news_title


def test_google_news_title_split():
    assert split_google_news_title("颱風來襲 停班停課一覽 - 自由時報", "自由時報") == (
        "颱風來襲 停班停課一覽",
        "自由時報",
    )
    # 沒有 source 欄位時，用最後一個 " - " 切
    assert split_google_news_title("Title here - Reuters", None) == ("Title here", "Reuters")
    assert split_google_news_title("沒有來源的標題", None) == ("沒有來源的標題", None)


def test_ettoday_style_date():
    assert (
        normalize_date_string("Sun,15 Nov 2026 21:39:00  +0800")
        == "Sun, 15 Nov 2026 21:39:00 +0800"
    )
    dt = entry_time({"published": "Sun,15 Nov 2026 21:39:00  +0800"})
    assert dt == datetime(2026, 11, 15, 13, 39, tzinfo=UTC)


def test_date_tz_override_for_untrustworthy_abbreviation():
    entry = {"published": "Sun, 15 Nov 2026 22:58:38 CST"}
    dt = entry_time(entry, date_tz="Asia/Shanghai")
    assert dt.utcoffset() == timedelta(hours=8)
    assert dt.astimezone(UTC) == datetime(2026, 11, 15, 14, 58, 38, tzinfo=UTC)


def test_epoch_dates_are_dropped():
    assert entry_time({"published": "Thu, 01 Jan 1970 08:00:00 +0800"}) is None


def test_clean_wikitext():
    raw = (
        "<span title=\"First Place\">[[Image:PlacementIcon1.png|25px|link=]]</span>'''"
        "<span>&#8288;[[File:Bilibili Gaminglogo std.png|48px|link=Bilibili Gaming]]&#8288;"
        "</span><span>[[Bilibili Gaming|Bilibili Gaming]]</span>''' wins "
        "[[2026 Season World Championship|Worlds 2026]]."
    )
    assert clean_wikitext(raw) == "Bilibili Gaming wins Worlds 2026."
    assert clean_wikitext("[[Faker]] re-signs.") == "Faker re-signs."


def test_leaguepedia_source_field():
    raw = "https://x.com/a/1;;;Post;;;x.com;;;;;;ref:::https://x.com/a/2;;;Post;;;x.com:::"
    assert _first_http_source(raw) == "https://x.com/a/1"
    assert _first_http_source(";;;;;;;;;News/ref;;;Archive:GCD:::") is None


def test_roc_date():
    d = _roc_date("1151113")
    assert (d.year, d.month, d.day) == (2026, 11, 13)
    assert d.utcoffset() == timezone(timedelta(hours=8)).utcoffset(None)
    assert _roc_date("bad") is None
