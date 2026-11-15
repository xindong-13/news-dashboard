"""刊別判斷、時間範圍、讀寫刊別 JSON 與 index.json。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Literal

from pipeline.config import EditionSettings
from pipeline.models import Edition, EditionIndex, EditionIndexEntry, EditionType
from pipeline.textutil import TAIPEI

EditionChoice = Literal["auto", "noon", "night"]
_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(noon|night)$")


@dataclass(frozen=True)
class EditionPlan:
    edition_id: str
    edition_type: EditionType
    now: datetime  # 本次執行時間（台北時間）＝ 出刊時間 ＝ 範圍終點
    window_from: datetime
    previous_id: str | None  # 範圍起點是根據哪一刊；None 表示找不到，往回抓預設小時數


def detect_edition_type(now: datetime, noon_cutoff: str) -> EditionType:
    """依「實際執行時間」判斷刊別：台北時間 cutoff 之前算午報，之後算晚報。"""
    hh, mm = (int(x) for x in noon_cutoff.split(":"))
    return "noon" if now.astimezone(TAIPEI).time() < time(hh, mm) else "night"


def edition_id_for(now: datetime, edition_type: EditionType) -> str:
    return f"{now.astimezone(TAIPEI):%Y-%m-%d}-{edition_type}"


def list_edition_files(editions_dir: Path) -> list[Path]:
    return sorted(p for p in editions_dir.glob("*.json") if _ID_RE.match(p.stem))


def load_edition(path: Path) -> Edition:
    return Edition.model_validate_json(path.read_text(encoding="utf-8"))


def _previous_edition(editions_dir: Path, exclude_id: str, now: datetime) -> Edition | None:
    """找出時間在 now 之前、且不是本刊的最近一刊（同一刊重跑時不會拿自己當起點）。"""
    candidates: list[Edition] = []
    for p in reversed(list_edition_files(editions_dir)):  # 檔名可排序，新的在後
        if p.stem == exclude_id:
            continue
        try:
            ed = load_edition(p)
        except Exception:  # noqa: BLE001 - 壞掉的檔案就跳過
            continue
        if ed.window.to <= now:
            candidates.append(ed)
        if len(candidates) >= 3:
            break
    return max(candidates, key=lambda e: e.window.to, default=None)


def plan_edition(
    now: datetime, choice: EditionChoice, settings: EditionSettings, editions_dir: Path
) -> EditionPlan:
    now = now.astimezone(TAIPEI)
    etype: EditionType = (
        detect_edition_type(now, settings.noon_cutoff) if choice == "auto" else choice
    )
    eid = edition_id_for(now, etype)

    prev = _previous_edition(editions_dir, eid, now)
    earliest = now - timedelta(hours=settings.max_window_hours)
    if prev is None:
        start, prev_id = now - timedelta(hours=settings.default_lookback_hours), None
    else:
        start, prev_id = max(prev.window.to.astimezone(TAIPEI), earliest), prev.edition_id
    return EditionPlan(eid, etype, now, start, prev_id)


def recent_source_urls(
    editions_dir: Path, exclude_id: str, now: datetime, days: int = 3
) -> set[str]:
    """最近幾天已經刊出過的來源網址，避免同一則新聞連續兩刊都出現。"""
    from pipeline.processing.normalize import normalize_url

    cutoff = (now - timedelta(days=days)).astimezone(TAIPEI).strftime("%Y-%m-%d")
    urls: set[str] = set()
    for p in list_edition_files(editions_dir):
        if p.stem == exclude_id or p.stem[:10] < cutoff:
            continue
        try:
            ed = load_edition(p)
        except Exception:  # noqa: BLE001
            continue
        for ev in ed.events:
            urls.update(normalize_url(s.url) for s in ev.sources)
    return urls


def write_edition(edition: Edition, data_dir: Path) -> Path:
    """寫出刊別 JSON（同一刊重跑會覆蓋）並更新 index.json。"""
    editions_dir = data_dir / "editions"
    editions_dir.mkdir(parents=True, exist_ok=True)
    path = editions_dir / f"{edition.edition_id}.json"
    path.write_text(edition.to_json() + "\n", encoding="utf-8")
    update_index(data_dir / "index.json", edition)
    return path


def update_index(index_path: Path, edition: Edition) -> EditionIndex:
    if index_path.exists():
        index = EditionIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    else:
        index = EditionIndex()
    entry = EditionIndexEntry(
        edition_id=edition.edition_id,
        edition_type=edition.edition_type,
        published_at=edition.published_at,
        event_count=len(edition.events),
        degraded=edition.llm_usage.degraded,
    )
    entries = [e for e in index.editions if e.edition_id != edition.edition_id] + [entry]
    # 依刊別 ID 排序（日期＋noon 在 night 前），新的在前
    entries.sort(key=lambda e: (e.edition_id[:10], e.edition_type == "night"), reverse=True)
    index = EditionIndex(editions=entries)
    index_path.write_text(
        json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return index
