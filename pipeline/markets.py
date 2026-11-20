"""行情卡資料。

主要來源是鉅亨網的報價 API（ws.api.cnyes.com），一次查全部代號；
鉅亨網沒有的項目（WTI 原油）用 FRED 的每日資料補，會晚幾天，前端會顯示資料日期。
Yahoo Finance（yfinance）的 robots.txt 全站禁止，所以不用，理由見 DECISIONS.md。

任何一項抓不到都只讓那一項變成空值（前端顯示「—」），不會讓整刊失敗。
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from pipeline.config import MarketItem, MarketSettings
from pipeline.http import PoliteClient
from pipeline.models import MarketQuote

log = logging.getLogger(__name__)

CNYES_QUOTES_URL = "https://ws.api.cnyes.com/ws/api/v1/quote/quotes/"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# 鉅亨網報價欄位代碼
_PRICE, _CHANGE, _CHANGE_PCT, _TIME = "6", "11", "56", "200007"


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _empty(item: MarketItem) -> MarketQuote:
    return MarketQuote(
        symbol=item.id,
        name=item.name,
        decimals=item.decimals,
        unit=item.unit,
        currency=item.currency,
    )


def parse_cnyes_quotes(data: dict) -> dict[str, dict]:
    """把鉅亨網的回應整理成 {代號: {price, change, change_pct, as_of}}。"""
    out: dict[str, dict] = {}
    if data.get("statusCode") != 200:
        return out
    for it in data.get("data") or []:
        sym = it.get("0")
        price = _num(it.get(_PRICE))
        if not sym or price is None:
            continue
        ts = it.get(_TIME)
        change = _num(it.get(_CHANGE))
        pct = _num(it.get(_CHANGE_PCT))
        if pct is None and change is not None and price != change:
            pct = change / (price - change) * 100
        out[sym] = {
            "price": price,
            "change": change,
            "change_pct": pct,
            "as_of": datetime.fromtimestamp(ts, tz=UTC) if ts else None,
        }
    return out


def parse_fred_csv(text: str) -> dict | None:
    """FRED 的 CSV（日期,數值；缺值是「.」）→ 最後一筆與前一筆比較的漲跌。"""
    rows: list[tuple[date, float]] = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # 標題列
    for row in reader:
        if len(row) < 2:
            continue
        v = _num(row[1])
        if v is None:
            continue
        try:
            rows.append((date.fromisoformat(row[0]), v))
        except ValueError:
            continue
    if not rows:
        return None
    rows.sort()
    d, price = rows[-1]
    change = pct = None
    if len(rows) >= 2:
        prev = rows[-2][1]
        change = round(price - prev, 6)
        pct = round(change / prev * 100, 4) if prev else None
    # FRED 只有日期：當成美東收盤（約 UTC 21:00），前端只顯示日期
    return {
        "price": price,
        "change": change,
        "change_pct": pct,
        "as_of": datetime(d.year, d.month, d.day, 21, tzinfo=UTC),
        "date_only": True,
    }


def fetch_markets(
    settings: MarketSettings, client: PoliteClient, now: datetime
) -> list[MarketQuote]:
    items = settings.items
    quotes = {i.id: _empty(i) for i in items}

    cnyes_syms = [i.cnyes for i in items if i.cnyes]
    cnyes: dict[str, dict] = {}
    if cnyes_syms:
        url = CNYES_QUOTES_URL + quote(",".join(cnyes_syms), safe=",:")
        try:
            cnyes = parse_cnyes_quotes(client.get(url).json())
        except Exception as e:  # noqa: BLE001 — 行情失敗不影響出刊
            log.warning("鉅亨網行情抓取失敗：%s", e)

    for item in items:
        got = cnyes.get(item.cnyes) if item.cnyes else None
        source = "cnyes"
        if got is None and item.fred:
            start = (now - timedelta(days=21)).date().isoformat()
            try:
                text = client.get(FRED_CSV_URL, params={"id": item.fred, "cosd": start}).text
                got = parse_fred_csv(text)
                source = "fred"
            except Exception as e:  # noqa: BLE001
                log.warning("FRED %s 抓取失敗：%s", item.fred, e)
        if got is None:
            log.warning("行情 %s（%s）抓不到，顯示「—」", item.name, item.id)
            continue
        quotes[item.id] = quotes[item.id].model_copy(update=got | {"source": source})
    return [quotes[i.id] for i in items]
