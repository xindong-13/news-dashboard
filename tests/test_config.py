import pytest
from pydantic import ValidationError

from pipeline.config import load_settings, load_sources
from pipeline.models import Edition, SourceConfig


def test_settings_load():
    s = load_settings()
    assert s.timezone == "Asia/Taipei"
    assert [p.name for p in s.llm.providers] == ["gemini", "groq", "anthropic"]
    assert any(m.id == "taiex" for m in s.markets.items)


def test_sources_load_and_cover_all_categories():
    sources = load_sources()
    assert sources, "至少要有一個啟用中的來源"
    assert {s.category for s in sources} == {"taiwan", "world", "finance", "esports"}
    assert all(s.enabled for s in sources)
    # api 類型一定要指定 parser
    assert all(s.parser for s in sources if s.type == "api")


def test_disabled_sources_kept_for_record():
    all_sources = load_sources(include_disabled=True)
    assert len(all_sources) > len(load_sources())


def test_source_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        SourceConfig(
            id="x",
            name="x",
            url="https://x",
            type="rss",
            category="world",
            language="en",
            typo_field=1,
        )


def test_edition_json_uses_from_alias():
    ed = Edition.model_validate(
        {
            "edition_id": "2026-11-15-noon",
            "edition_type": "noon",
            "published_at": "2026-11-15T11:52:00+08:00",
            "window": {"from": "2026-11-14T21:00:00+08:00", "to": "2026-11-15T11:52:00+08:00"},
        }
    )
    js = ed.to_json()
    assert '"from"' in js and '"from_"' not in js
    assert ed.llm_usage.degraded is True
