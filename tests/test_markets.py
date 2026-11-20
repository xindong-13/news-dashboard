from datetime import UTC, datetime

import httpx

from pipeline.config import MarketItem, MarketSettings
from pipeline.markets import fetch_markets, parse_cnyes_quotes, parse_fred_csv

NOW = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)

CNYES_OK = {
    "statusCode": 200,
    "data": [
        {"0": "TWS:TSE01:INDEX", "6": 36033.29, "11": -2393.6, "56": -6.23, "200007": 1795152805},
        {"0": "GI:DJI:INDEX", "6": 48444.07, "11": -652.99, "200007": 1795203372},  # 沒有漲跌幅
        {"0": "GI:SOX:INDEX", "6": None},  # 沒有價格 → 當作抓不到
    ],
}

FRED_CSV = "observation_date,DCOILWTICO\n2026-11-13,91.96\n2026-11-16,.\n2026-11-17,89.53\n"


def test_parse_cnyes_quotes():
    q = parse_cnyes_quotes(CNYES_OK)
    assert q["TWS:TSE01:INDEX"]["price"] == 36033.29
    assert q["TWS:TSE01:INDEX"]["as_of"].tzinfo is not None
    # 漲跌幅缺值時用漲跌點數推算
    assert round(q["GI:DJI:INDEX"]["change_pct"], 2) == round(
        -652.99 / (48444.07 + 652.99) * 100, 2
    )
    assert "GI:SOX:INDEX" not in q


def test_parse_cnyes_error_status():
    assert parse_cnyes_quotes({"statusCode": 4002, "data": {"items": []}}) == {}


def test_parse_fred_skips_missing_values():
    got = parse_fred_csv(FRED_CSV)
    assert got["price"] == 89.53
    assert got["change"] == round(89.53 - 91.96, 6)
    assert got["as_of"].date().isoformat() == "2026-11-17"
    assert got["date_only"] is True
    assert parse_fred_csv("observation_date,X\n2026-11-17,.\n") is None


class _FakeClient:
    """模擬 PoliteClient.get：依網址回應，或丟錯。"""

    def __init__(self, cnyes=None, fred=None):
        self.cnyes, self.fred = cnyes, fred

    def get(self, url, **kw):
        req = httpx.Request("GET", url)
        body = self.cnyes if "cnyes" in url else self.fred
        if body is None:
            raise httpx.ConnectError("boom", request=req)
        if isinstance(body, dict):
            return httpx.Response(200, json=body, request=req)
        return httpx.Response(200, text=body, request=req)


ITEMS = MarketSettings(
    items=[
        MarketItem(id="taiex", name="加權指數", cnyes="TWS:TSE01:INDEX"),
        MarketItem(id="sox", name="費半", cnyes="GI:SOX:INDEX"),
        MarketItem(id="wti", name="原油", fred="DCOILWTICO"),
        MarketItem(id="dji", name="道瓊", cnyes="GI:XXX:INDEX", fred="DJIA"),
    ]
)


def test_fetch_markets_keeps_order_and_marks_missing():
    out = fetch_markets(ITEMS, _FakeClient(cnyes=CNYES_OK, fred=FRED_CSV), NOW)
    assert [q.symbol for q in out] == ["taiex", "sox", "wti", "dji"]
    assert out[0].price == 36033.29 and out[0].source == "cnyes"
    assert out[1].price is None  # 抓不到 → 前端顯示「—」
    assert out[2].source == "fred" and out[2].date_only
    assert out[3].source == "fred"  # 鉅亨網沒有 → 改用 FRED


def test_fetch_markets_all_sources_down():
    out = fetch_markets(ITEMS, _FakeClient(), NOW)
    assert len(out) == 4
    assert all(q.price is None for q in out)


def test_settings_markets_are_valid():
    from pipeline.config import load_settings

    items = load_settings().markets.items
    assert len({i.id for i in items}) == len(items)
    assert all(i.cnyes or i.fred for i in items)
