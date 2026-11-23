import json
from datetime import UTC, timedelta

import pytest

from pipeline.config import EditionSettings
from pipeline.models import Edition, TimeWindow
from pipeline.processing.edition import (
    detect_edition_type,
    due_edition_id,
    plan_edition,
    update_index,
    write_edition,
)
from tests.helpers import tpe

S = EditionSettings(noon_cutoff="16:30", default_lookback_hours=12, max_window_hours=24)


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2026-11-15 11:30", "noon"),  # 正常的午報排程
        ("2026-11-15 12:25", "noon"),  # Actions 延遲 55 分鐘仍是午報
        ("2026-11-15 16:29", "noon"),
        ("2026-11-15 16:30", "night"),
        ("2026-11-15 20:30", "night"),
        ("2026-11-15 23:59", "night"),
        ("2026-11-16 00:10", "night"),  # 半夜手動跑算前一天的晚報
        ("2026-11-16 04:59", "night"),
        ("2026-11-16 05:00", "noon"),
    ],
)
def test_detect_edition_type(clock, expected):
    assert detect_edition_type(tpe(clock), S) == expected


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2026-11-23 11:44", None),
        ("2026-11-23 11:50", "2026-11-23-noon"),  # 外部觸發
        ("2026-11-23 16:29", "2026-11-23-noon"),
        ("2026-11-23 16:30", None),
        ("2026-11-23 20:01", None),  # 11/22 實際發生：午報排程晚 8 小時，搶走晚報
        ("2026-11-23 20:44", None),
        ("2026-11-23 20:50", "2026-11-23-night"),
        ("2026-11-23 23:59", "2026-11-23-night"),
        ("2026-11-24 00:00", None),  # 跨午夜：不能出成隔天的刊
        ("2026-11-24 00:05", None),
        ("2026-11-24 03:59", None),  # 11/22 實際發生：晚報排程拖到隔天凌晨
        ("2026-12-31 23:59", "2026-12-31-night"),
        ("2027-01-01 00:01", None),
    ],
)
def test_due_edition_id(clock, expected):
    assert due_edition_id(tpe(clock), S) == expected
    assert due_edition_id(tpe(clock).astimezone(UTC), S) == expected  # Actions 的時鐘是 UTC


@pytest.mark.parametrize("choice", ["auto", "night"])
@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2026-11-23 23:59", "2026-11-23-night"),
        ("2026-11-24 00:05", "2026-11-23-night"),
        ("2026-11-24 04:59", "2026-11-23-night"),
        ("2026-12-01 01:00", "2026-11-30-night"),
        ("2027-01-01 01:00", "2026-12-31-night"),
    ],
)
def test_manual_run_after_midnight_is_previous_night(tmp_path, choice, clock, expected):
    plan = plan_edition(tpe(clock), choice, S, tmp_path / "editions")
    assert plan.edition_id == expected
    assert plan.now == tpe(clock)  # 出刊時間仍是實際時間


def test_manual_noon_after_midnight_stays_today(tmp_path):
    plan = plan_edition(tpe("2026-11-24 01:00"), "noon", S, tmp_path / "editions")
    assert plan.edition_id == "2026-11-24-noon"


def test_late_night_edition_starts_at_previous_noon(tmp_path):
    _save(tmp_path, "2026-11-23-noon", "noon", "2026-11-22 20:50", "2026-11-23 11:50")
    plan = plan_edition(tpe("2026-11-24 00:30"), "auto", S, tmp_path / "editions")
    assert (plan.edition_id, plan.previous_id) == ("2026-11-23-night", "2026-11-23-noon")


def test_detect_uses_taipei_time_even_if_given_utc():
    # GitHub Actions 的時鐘是 UTC：12:40 UTC = 台北 20:40，應該是晚報
    assert detect_edition_type(tpe("2026-11-15 20:40").astimezone(UTC), S) == "night"


def _save(tmp_path, eid, etype, frm, to):
    ed = Edition(
        edition_id=eid,
        edition_type=etype,
        published_at=tpe(to),
        window=TimeWindow(from_=tpe(frm), to=tpe(to)),
    )
    write_edition(ed, tmp_path)
    return ed


def test_no_previous_edition_uses_default_lookback(tmp_path):
    plan = plan_edition(tpe("2026-11-15 11:40"), "auto", S, tmp_path / "editions")
    assert plan.edition_id == "2026-11-15-noon"
    assert plan.previous_id is None
    assert plan.now - plan.window_from == timedelta(hours=12)


def test_window_starts_at_previous_edition_cutoff(tmp_path):
    _save(tmp_path, "2026-11-14-night", "night", "2026-11-14 11:50", "2026-11-14 20:45")
    plan = plan_edition(tpe("2026-11-15 11:40"), "auto", S, tmp_path / "editions")
    assert plan.previous_id == "2026-11-14-night"
    assert plan.window_from == tpe("2026-11-14 20:45")


def test_rerun_same_edition_does_not_use_itself_as_start(tmp_path):
    _save(tmp_path, "2026-11-14-night", "night", "2026-11-14 11:50", "2026-11-14 20:45")
    _save(tmp_path, "2026-11-15-noon", "noon", "2026-11-14 20:45", "2026-11-15 11:40")
    plan = plan_edition(tpe("2026-11-15 12:30"), "noon", S, tmp_path / "editions")
    assert plan.edition_id == "2026-11-15-noon"
    assert plan.previous_id == "2026-11-14-night"
    assert plan.window_from == tpe("2026-11-14 20:45")


def test_window_capped_when_editions_were_missed(tmp_path):
    _save(tmp_path, "2026-11-12-night", "night", "2026-11-12 11:50", "2026-11-12 20:45")
    plan = plan_edition(tpe("2026-11-15 11:40"), "auto", S, tmp_path / "editions")
    assert plan.now - plan.window_from == timedelta(hours=24)


def test_rerun_overwrites_file_and_index_entry(tmp_path):
    _save(tmp_path, "2026-11-15-noon", "noon", "2026-11-14 20:45", "2026-11-15 11:40")
    _save(tmp_path, "2026-11-15-noon", "noon", "2026-11-14 20:45", "2026-11-15 12:10")
    files = list((tmp_path / "editions").glob("*.json"))
    assert [f.name for f in files] == ["2026-11-15-noon.json"]
    index = update_index(
        tmp_path / "index.json",
        Edition.model_validate_json(files[0].read_text(encoding="utf-8")),
    )
    assert [e.edition_id for e in index.editions] == ["2026-11-15-noon"]


def test_index_sorted_newest_first(tmp_path):
    _save(tmp_path, "2026-11-14-night", "night", "2026-11-14 11:50", "2026-11-14 20:45")
    _save(tmp_path, "2026-11-15-noon", "noon", "2026-11-14 20:45", "2026-11-15 11:40")
    _save(tmp_path, "2026-11-14-noon", "noon", "2026-11-13 20:45", "2026-11-14 11:50")
    data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert [e["edition_id"] for e in data["editions"]] == [
        "2026-11-15-noon",
        "2026-11-14-night",
        "2026-11-14-noon",
    ]
