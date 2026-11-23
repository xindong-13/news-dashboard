"""出刊入口。

    uv run python -m pipeline.main --edition auto      # 依執行時間判斷午報／晚報
    uv run python -m pipeline.main --edition noon      # 指定刊別
    uv run python -m pipeline.main --no-llm            # 強制降級版（不呼叫 LLM）
    uv run python -m pipeline.main --target 2026-11-24-noon   # 自動觸發：只出指定的這一刊，出過或不在時段內就略過
    uv run python -m pipeline.main --due               # 自動觸發：出「現在該出、但還沒出」的那一刊，沒有就略過
    uv run python -m pipeline.main --dry-run           # 不寫檔，只印摘要
    uv run python -m pipeline.main --now 2026-11-15T11:40:00+08:00   # 假裝在某個時間執行（測試用）

結束碼：成功出刊為 0（即使部分來源失敗）；全部來源都抓不到或程式錯誤為非 0，讓 Actions 亮紅燈。
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from pipeline.config import DATA_DIR, ROOT, Settings, load_settings, load_sources
from pipeline.fetchers import FetchResult, fetch_all
from pipeline.http import PoliteClient
from pipeline.llm.router import LLMRouter
from pipeline.markets import fetch_markets
from pipeline.models import Edition, LLMUsage, MarketQuote, SourceConfig, TimeWindow
from pipeline.processing.ai import process_with_llm
from pipeline.processing.cluster import cluster_items
from pipeline.processing.edition import (
    EDITION_ID_RE,
    EditionChoice,
    EditionPlan,
    due_edition_id,
    plan_edition,
    recent_source_urls,
    write_edition,
)
from pipeline.processing.events import degraded_event, pick_headlines, select_for_edition
from pipeline.processing.normalize import localize
from pipeline.processing.select import filter_window, select_clusters
from pipeline.textutil import TAIPEI, now_taipei

log = logging.getLogger("pipeline")

Fetcher = Callable[[list[SourceConfig], Settings], FetchResult]
MarketsFetcher = Callable[[Settings, datetime], list[MarketQuote]]


class NoItemsError(RuntimeError):
    pass


def default_fetcher(sources: list[SourceConfig], settings: Settings) -> FetchResult:
    with PoliteClient(settings.http) as client:
        return fetch_all(sources, client)


def default_markets_fetcher(settings: Settings, now: datetime) -> list[MarketQuote]:
    with PoliteClient(settings.http) as client:
        return fetch_markets(settings.markets, client, now)


def build_edition(
    plan: EditionPlan,
    settings: Settings,
    sources: list[SourceConfig],
    data_dir: Path,
    *,
    fetcher: Fetcher = default_fetcher,
    use_llm: bool = True,
    router: LLMRouter | None = None,
    markets_fetcher: MarketsFetcher | None = None,
) -> Edition:
    log.info(
        "刊別 %s，範圍 %s → %s（起點依據：%s）",
        plan.edition_id,
        plan.window_from.strftime("%m-%d %H:%M"),
        plan.now.strftime("%m-%d %H:%M"),
        plan.previous_id or f"往回 {settings.edition.default_lookback_hours} 小時",
    )

    fetched = fetcher(sources, settings)
    log.info("抓到 %d 則，%d 個來源失敗", len(fetched.items), len(fetched.errors))
    for err in fetched.errors:
        log.warning("來源失敗 %s：%s", err.source_id, err.error)
    if not fetched.items:
        raise NoItemsError("所有來源都沒有抓到任何新聞")

    seen = recent_source_urls(data_dir / "editions", plan.edition_id, plan.now)
    items = [localize(i) for i in filter_window(fetched.items, plan, seen)]
    log.info("時間範圍內且未刊出過：%d 則", len(items))

    proc = settings.processing
    clusters = cluster_items(
        items, proc.title_similarity_threshold, proc.title_similarity_threshold_cjk
    )
    merged = sum(1 for c in clusters if len(c.items) > 1)
    log.info("合併成 %d 個事件（其中 %d 個有多個來源）", len(clusters), merged)
    clusters = select_clusters(clusters, proc.max_items_per_edition, plan.now)

    if use_llm and router is None:
        router = LLMRouter.from_settings(settings.llm)
    if use_llm and router is not None and router.providers:
        ai = process_with_llm(clusters, router, settings, plan.now, plan.edition_type)
        events, headlines, briefing = ai.events, ai.headlines, ai.briefing
        usage = router.usage()
    else:
        reason = "使用 --no-llm" if not use_llm else "沒有可用的 LLM provider（未設定任何 API key）"
        log.warning("本刊不經 AI 整理：%s", reason)
        events = [degraded_event(c, f"evt_{i:03d}") for i, c in enumerate(clusters, start=1)]
        events = select_for_edition(
            events,
            proc.edition_size,
            proc.edition_max_per_category,
            proc.edition_min_per_subcategory,
        )
        headlines = pick_headlines(
            events,
            proc.headlines_min,
            proc.headlines_max,
            proc.headlines_max_per_category,
            proc.headlines_exclude_categories,
        )
        briefing = []
        usage = LLMUsage(degraded=True, errors=[reason])

    if usage.degraded:
        log.warning("本刊未經 AI 整理（降級版）")
    else:
        log.info(
            "LLM：%d 次呼叫，輸入 %d／輸出 %d tokens（主要 %s %s）",
            usage.calls,
            usage.input_tokens,
            usage.output_tokens,
            usage.provider,
            usage.model,
        )
    markets: list[MarketQuote] = []
    if markets_fetcher is not None:
        try:
            markets = markets_fetcher(settings, plan.now)
        except Exception as e:  # noqa: BLE001 — 行情失敗不影響出刊
            log.warning("行情抓取失敗：%s", e)
        ok = sum(1 for m in markets if m.price is not None)
        log.info("行情：%d／%d 項有資料", ok, len(settings.markets.items))

    return Edition(
        edition_id=plan.edition_id,
        edition_type=plan.edition_type,
        published_at=plan.now,
        window=TimeWindow(from_=plan.window_from, to=plan.now),
        briefing=briefing,
        headlines=headlines,
        markets=markets,
        events=events,
        llm_usage=usage,
        source_errors=fetched.errors,
    )


def skip_reason(
    now: datetime, settings: Settings, data_dir: Path, target: str | None
) -> str | None:
    """自動觸發該不該略過；要出刊時回傳 None。target 是 None 表示「現在該出的那一刊」。"""
    due = due_edition_id(now, settings.edition)
    if due is None or (target is not None and target != due):
        return f"現在（{now:%m-%d %H:%M}）不是 {target or '任何一刊'} 的出刊時段"
    if (data_dir / "editions" / f"{due}.json").exists():
        return f"{due} 已經出刊過"
    return None


def _edition_id(value: str) -> str:
    if not EDITION_ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"刊別 ID 要像 2026-11-24-noon，收到 {value!r}")
    return value


def _summary(ed: Edition) -> str:
    cats = Counter(e.category for e in ed.events)
    multi = sum(1 for e in ed.events if len(e.sources) > 1)
    return (
        f"{ed.edition_id}：{len(ed.events)} 則事件"
        f"（台灣 {cats['taiwan']}、國際 {cats['world']}、財經 {cats['finance']}、電競 {cats['esports']}、Threads {cats['threads']}；"
        f"多來源 {multi}）、今日重點 {len(ed.headlines)} 則、來源錯誤 {len(ed.source_errors)} 個、"
        f"{'降級版' if ed.llm_usage.degraded else 'AI 整理版'}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="產生一刊新聞")
    ap.add_argument("--edition", choices=["auto", "noon", "night"], default="auto")
    ap.add_argument("--now", type=datetime.fromisoformat, help="假裝的執行時間（ISO 8601，含時區）")
    ap.add_argument("--no-llm", action="store_true", help="不呼叫 LLM，直接輸出降級版")
    ap.add_argument("--dry-run", action="store_true", help="不寫檔")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    auto = ap.add_mutually_exclusive_group()
    auto.add_argument(
        "--target",
        type=_edition_id,
        help="外部觸發用：只出指定的這一刊（例 2026-11-24-noon），出過或不在該刊時段內就略過",
    )
    auto.add_argument(
        "--due",
        action="store_true",
        help="GitHub 排程備援用：出「現在該出、但還沒出」的那一刊，沒有就略過",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.now and args.now.tzinfo is None:
        ap.error("--now 要包含時區，例如 2026-11-15T11:40:00+08:00")

    load_dotenv(ROOT / ".env", override=False)  # 本機用 .env；Actions 用 Secrets（環境變數優先）
    settings = load_settings()
    now = (args.now or now_taipei()).astimezone(TAIPEI)
    choice: EditionChoice = args.edition
    if args.due or args.target:
        choice = "auto"  # 自動觸發的刊別由時段決定，不能另外指定
        if reason := skip_reason(now, settings, args.data_dir, args.target):
            print(f"{reason}，略過")
            return 0
    plan = plan_edition(now, choice, settings.edition, args.data_dir / "editions")

    try:
        edition = build_edition(
            plan,
            settings,
            load_sources(),
            args.data_dir,
            use_llm=not args.no_llm,
            markets_fetcher=default_markets_fetcher,
        )
    except NoItemsError as e:
        log.error("%s，不出刊", e)
        return 2

    if args.dry_run:
        log.info("dry-run，不寫檔")
    else:
        path = write_edition(edition, args.data_dir)
        log.info("已寫入 %s", path)
    print(_summary(edition))
    return 0


if __name__ == "__main__":
    sys.exit(main())
