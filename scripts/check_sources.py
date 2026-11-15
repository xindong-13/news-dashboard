"""新聞來源健康檢查。

用法：
    uv run python scripts/check_sources.py              # 檢查所有啟用中的來源
    uv run python scripts/check_sources.py --all        # 連停用的也檢查
    uv run python scripts/check_sources.py --id hltv_news --samples 5
    uv run python scripts/check_sources.py --markdown reports/sources.md

結束碼：有任何啟用中的來源失敗或抓到 0 則就回傳 1（方便放進 CI）。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import load_settings, load_sources  # noqa: E402
from pipeline.fetchers import fetch_source  # noqa: E402
from pipeline.http import PoliteClient  # noqa: E402
from pipeline.textutil import TAIPEI, now_taipei  # noqa: E402

STALE_HOURS = 72


def _age(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "—"
    hours = (now - dt).total_seconds() / 3600
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--all", action="store_true", help="包含 enabled: false 的來源")
    ap.add_argument("--id", action="append", help="只檢查指定 id（可重複）")
    ap.add_argument("--samples", type=int, default=0, help="每個來源列出幾則標題")
    ap.add_argument("--markdown", type=Path, help="另外輸出 Markdown 報表到此路徑")
    args = ap.parse_args()

    settings = load_settings()
    sources = load_sources(include_disabled=args.all)
    if args.id:
        sources = [s for s in sources if s.id in set(args.id)]

    now = now_taipei()
    rows: list[tuple[str, ...]] = []
    failed = 0

    with PoliteClient(settings.http) as client:
        for s in sources:
            label = s.id + ("" if s.enabled else "（停用）")
            try:
                items = fetch_source(s, client)
            except Exception as e:  # noqa: BLE001
                status, count, newest, sample = "FAIL", "-", "—", f"{type(e).__name__}: {e}"[:120]
                if s.enabled:
                    failed += 1
                print(f"✗ {label:<24} {sample}", flush=True)
                rows.append((label, s.category, status, count, newest, sample))
                continue

            dated = [i.published_at for i in items if i.published_at]
            newest_dt = max(dated) if dated else None
            stale = newest_dt is not None and (now - newest_dt).total_seconds() > STALE_HOURS * 3600
            if not items:
                status = "EMPTY"
                if s.enabled:
                    failed += 1
            elif stale:
                status = "STALE"
            elif len(dated) < len(items):
                status = "OK*"  # 有部分項目沒有時間
            else:
                status = "OK"
            newest = newest_dt.astimezone(TAIPEI).strftime("%m-%d %H:%M") if newest_dt else "—"
            sample = items[0].title[:60] if items else ""
            mark = "✓" if status.startswith("OK") else "!"
            print(
                f"{mark} {label:<24} {status:<6} {len(items):>3} 則  最新 {newest}"
                f"（{_age(newest_dt, now)} 前）  {sample}",
                flush=True,
            )
            for it in items[: args.samples]:
                t = (
                    it.published_at.astimezone(TAIPEI).strftime("%m-%d %H:%M")
                    if it.published_at
                    else "--"
                )
                print(f"      {t}  [{it.source_name}] {it.title[:80]}")
            rows.append((label, s.category, status, str(len(items)), newest, sample))

    print(f"\n共 {len(sources)} 個來源，{failed} 個啟用中的來源有問題。")
    print("狀態：OK 正常／OK* 部分無時間／STALE 超過 72 小時沒更新／EMPTY 0 則／FAIL 抓取失敗")

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 來源健康檢查（{now.strftime('%Y-%m-%d %H:%M')} 台北時間）",
            "",
            "| 來源 | 類別 | 狀態 | 則數 | 最新 | 範例 |",
            "|---|---|---|---|---|---|",
        ]
        lines += ["| " + " | ".join(c.replace("|", "／") for c in r) + " |" for r in rows]
        args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"報表已寫入 {args.markdown}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
