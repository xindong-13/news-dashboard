"""出刊推播：訊息內容、最新一刊的判斷、LINE API 呼叫（不連網路）。"""

import json

import httpx
import pytest

from pipeline.config import NotifySettings
from pipeline.models import Edition
from pipeline.notify import build_message, latest_edition, send_line

SETTINGS = NotifySettings(site_url="https://example.github.io/news/")


def _edition(eid: str, briefing=(), headlines=()) -> Edition:
    events = [
        {
            "id": f"evt_00{i}",
            "category": "taiwan",
            "importance": 3,
            "title_zh": f"標題{i}",
            "sources": [{"name": "中央社", "url": f"https://cna/{i}", "language": "zh-TW"}],
        }
        for i in range(1, 4)
    ]
    return Edition.model_validate(
        {
            "edition_id": eid,
            "edition_type": "night" if eid.endswith("night") else "noon",
            "published_at": f"{eid[:10]}T20:00:00+08:00",
            "window": {"from": f"{eid[:10]}T12:00:00+08:00", "to": f"{eid[:10]}T20:00:00+08:00"},
            "briefing": list(briefing),
            "headlines": list(headlines),
            "events": events,
        }
    )


def test_message_uses_briefing_and_link():
    text = build_message(_edition("2026-11-22-night", briefing=["第一點", "第二點"]), SETTINGS)
    assert text.splitlines()[0] == "📰 11/22（日）晚報・3 則"
    assert "・第一點" in text and "・第二點" in text
    assert text.endswith("https://example.github.io/news/2026-11-22-night.html")


def test_message_falls_back_to_headlines_without_briefing():
    text = build_message(_edition("2026-11-23-noon", headlines=["evt_002", "evt_001"]), SETTINGS)
    assert "11/23（一）午報" in text
    assert text.index("標題2") < text.index("標題1")
    assert "標題3" not in text


def test_latest_edition_night_after_noon(tmp_path):
    d = tmp_path / "editions"
    d.mkdir()
    for eid in ["2026-11-21-night", "2026-11-22-noon", "2026-11-22-night"]:
        (d / f"{eid}.json").write_text(_edition(eid).to_json(), encoding="utf-8")
    assert latest_edition(tmp_path).edition_id == "2026-11-22-night"


def test_send_line_request_and_errors():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        send_line("你好", "tok", "U123", client=c)
    assert seen["auth"] == "Bearer tok"
    assert seen["body"] == {"to": "U123", "messages": [{"type": "text", "text": "你好"}]}

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as c:
        with pytest.raises(RuntimeError, match="401"):
            send_line("你好", "bad", "U123", client=c)
