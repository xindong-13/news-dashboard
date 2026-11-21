"""整條流程（不連網路）：抓取 → 篩選 → 合併 → 降級版 Edition → 寫檔。"""

import json

import pytest

from pipeline.config import load_settings
from pipeline.fetchers import FetchResult
from pipeline.main import NoItemsError, build_edition
from pipeline.models import Edition, SourceError
from pipeline.processing.edition import plan_edition, write_edition
from tests.helpers import item, tpe

NOW = tpe("2026-11-15 11:40")


def _fake_fetcher(items, errors=()):
    def fetch(sources, settings):
        return FetchResult(items=list(items), errors=list(errors))

    return fetch


def _items():
    return [
        # 同一事件，三家媒體
        item("鼻頭漁港外海漁船起火燃燒 6船員跳海逃生獲救", "https://ltn/1", name="自由時報"),
        item("瑞芳鼻頭外海漁船起火 6船員跳海逃生獲救", "https://yahoo/1", name="Yahoo 新聞"),
        item(
            "快訊／鼻頭漁港外海漁船起火 6船員跳海逃生全數獲救",
            "https://cna/1",
            name="中央社",
            weight=1.3,
        ),
        # 不同事件
        item("立法院三讀通過年度總預算案", "https://cna/2", name="中央社", weight=1.3),
        item(
            "BLG wins Worlds 2026 after beating Gen.G 3-1",
            "https://x/1",
            name="Dexerto",
            category="esports",
            language="en",
            game="lol",
            league="lpl",
        ),
        # 簡中來源要轉繁體
        item(
            "英雄联盟全球总决赛落幕 视频回顾",
            "https://cn/1",
            name="新浪",
            category="esports",
            language="zh-CN",
        ),
        # 範圍外（太舊）、無意義標題、時間在未來（來源時區錯誤）
        item("昨天早上的舊新聞", "https://old/1", at="2026-11-14 08:00"),
        item("Vod", "https://lol/vod", category="esports", language="en"),
        item("時間跑到未來的新聞", "https://future/1", at="2026-11-15 18:00"),
    ]


def _build(tmp_path, items, errors=()):
    settings = load_settings()
    plan = plan_edition(NOW, "auto", settings.edition, tmp_path / "editions")
    return build_edition(
        plan, settings, [], tmp_path, fetcher=_fake_fetcher(items, errors), use_llm=False
    )


def test_degraded_edition_end_to_end(tmp_path):
    err = SourceError(source_id="pts", error="ConnectError: reset")
    ed = _build(tmp_path, _items(), [err])

    assert ed.edition_id == "2026-11-15-noon"
    assert ed.llm_usage.degraded is True
    assert ed.source_errors == [err]
    assert ed.window.to == NOW and ed.window.from_ == tpe("2026-11-14 23:40")

    titles = {e.title_zh for e in ed.events}
    assert "昨天早上的舊新聞" not in titles
    assert "Vod" not in titles
    assert "時間跑到未來的新聞" not in titles
    assert "英雄聯盟全球總決賽落幕 影片回顧" in titles  # 簡轉繁＋台灣用語

    # 同一事件只有一張卡片，列出全部來源；代表標題取權重最高的中央社
    boat = [e for e in ed.events if "漁船" in e.title_zh]
    assert len(boat) == 1
    assert len(boat[0].sources) == 3
    assert boat[0].sources[0].name in {"自由時報", "Yahoo 新聞", "中央社"}
    assert boat[0].title_zh.startswith("快訊／鼻頭")
    assert boat[0].importance >= 3

    esports = [e for e in ed.events if e.category == "esports" and "BLG" in e.title_zh]
    assert esports[0].league == "lpl"

    assert set(ed.headlines) <= {e.id for e in ed.events}
    assert boat[0].id in ed.headlines


def test_written_json_matches_spec_shape(tmp_path):
    ed = _build(tmp_path, _items())
    path = write_edition(ed, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "edition_id",
        "edition_type",
        "published_at",
        "window",
        "briefing",
        "headlines",
        "markets",
        "events",
        "llm_usage",
        "source_errors",
    ):
        assert key in data
    assert set(data["window"]) == {"from", "to"}
    assert data["published_at"].endswith("+08:00")
    assert Edition.model_validate(data) == ed
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["editions"][0]["edition_id"] == "2026-11-15-noon"


def test_items_already_published_are_skipped(tmp_path):
    first = _build(tmp_path, _items())
    write_edition(first, tmp_path)
    # 晚報：同樣的新聞再抓到一次，不應重複刊出
    settings = load_settings()
    plan = plan_edition(tpe("2026-11-15 20:40"), "auto", settings.edition, tmp_path / "editions")
    new = item("晚上的新新聞", "https://cna/99", at="2026-11-15 19:00")
    old = item("立法院三讀通過年度總預算案", "https://cna/2", at="2026-11-15 12:30")
    night = build_edition(
        plan, settings, [], tmp_path, fetcher=_fake_fetcher([new, old]), use_llm=False
    )
    assert [e.title_zh for e in night.events] == ["晚上的新新聞"]


def test_all_sources_failed_raises(tmp_path):
    with pytest.raises(NoItemsError):
        _build(tmp_path, [], [SourceError(source_id="a", error="x")])


def _build_with_markets(tmp_path, markets_fetcher):
    settings = load_settings()
    plan = plan_edition(NOW, "auto", settings.edition, tmp_path / "editions")
    return build_edition(
        plan,
        settings,
        [],
        tmp_path,
        fetcher=_fake_fetcher(_items()),
        use_llm=False,
        markets_fetcher=markets_fetcher,
    )


def test_markets_are_included(tmp_path):
    from pipeline.models import MarketQuote

    ed = _build_with_markets(
        tmp_path, lambda settings, now: [MarketQuote(symbol="taiex", name="加權", price=1.0)]
    )
    assert ed.markets[0].symbol == "taiex"


def test_markets_failure_does_not_break_edition(tmp_path):
    def boom(settings, now):
        raise RuntimeError("行情掛了")

    ed = _build_with_markets(tmp_path, boom)
    assert ed.markets == []
    assert ed.events  # 照常出刊


def test_headlines_capped_per_category():
    from pipeline.models import Event, SourceRef
    from pipeline.processing.events import pick_headlines

    src = [SourceRef(name="x", url="https://x", language="zh-TW")]

    def ev(i, cat, imp):
        return Event(id=f"e{i}", category=cat, importance=imp, title_zh=str(i), sources=src)

    events = [ev(i, "esports", 5) for i in range(5)] + [ev(10, "world", 4), ev(11, "taiwan", 3)]
    got = pick_headlines(events, 3, 8, per_category=2)
    cats = [next(e.category for e in events if e.id == h) for h in got]
    assert cats.count("esports") == 2
    assert "world" in cats
    # 名額不夠時放寬，仍至少 lo 則
    assert len(pick_headlines(events[:5], 4, 8, per_category=2)) == 4


def test_headlines_cap_relaxes_one_step_at_a_time():
    from pipeline.models import Event, SourceRef
    from pipeline.processing.events import pick_headlines

    src = [SourceRef(name="x", url="https://x", language="zh-TW")]

    def ev(i, cat, imp):
        return Event(id=f"{cat}{i}", category=cat, importance=imp, title_zh=str(i), sources=src)

    cats4 = ["taiwan", "world", "finance", "esports"]
    events = [ev(i, "esports", 5) for i in range(6)] + [
        ev(i, c, 3) for c in cats4[:3] for i in range(4)
    ]
    got = pick_headlines(events, 10, 10, per_category=2)
    assert len(got) == 10
    counts = {c: sum(h.startswith(c) for h in got) for c in cats4}
    # 4 類 × 2 = 8，剩下 2 則放寬到每類 3 則，不會全給電競
    assert max(counts.values()) == 3
    assert counts["esports"] == 3
