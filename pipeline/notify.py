"""出刊後推播：用 LINE 官方帳號（Messaging API）傳導讀＋網站連結給自己。

GitHub Actions 在網站部署完才執行（連結才點得開）：
    uv run python -m pipeline.notify            # 傳最新一刊
    uv run python -m pipeline.notify --dry-run  # 只印出訊息，不傳

需要環境變數 LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID（名稱見 settings.yaml 的 notify）。
沒設定就略過、結束碼 0；設定了但傳送失敗就結束碼 1，讓 Actions 亮紅燈（token 失效時才會發現）。

LINE 免費方案每月 200 則，一天兩刊約 62 則，夠用。
LINE 的 API 不是抓網頁，不走 PoliteClient（跟 LLM provider 一樣直接用 httpx）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv

from pipeline.config import NotifySettings, load_settings
from pipeline.models import Edition
from pipeline.processing.edition import list_edition_files, load_edition

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_TEXT_LIMIT = 5000
WEEKDAYS = "一二三四五六日"


def edition_title(ed: Edition) -> str:
    d = date.fromisoformat(ed.edition_id[:10])
    label = "午報" if ed.edition_type == "noon" else "晚報"
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）{label}"


def build_message(ed: Edition, settings: NotifySettings) -> str:
    """導讀（沒有導讀時改列焦點標題）＋網站連結。"""
    lines = [f"📰 {edition_title(ed)}・{len(ed.events)} 則", ""]
    if ed.briefing:
        lines += [f"・{p}" for p in ed.briefing]
    else:
        by_id = {e.id: e for e in ed.events}
        heads = [by_id[h] for h in ed.headlines if h in by_id][: settings.max_headlines]
        lines += [f"・{e.title_zh}" for e in heads]
    lines += ["", f"👉 {settings.site_url}{ed.edition_id}.html"]
    text = "\n".join(lines)
    if len(text) > LINE_TEXT_LIMIT:
        text = text[: LINE_TEXT_LIMIT - 1] + "…"
    return text


def send_line(text: str, token: str, to: str, client: httpx.Client | None = None) -> None:
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"to": to, "messages": [{"type": "text", "text": text}]},
        )
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        # 401：token 錯或失效；400：LINE_USER_ID 錯；429：本月免費則數用完
        raise RuntimeError(f"LINE 推播失敗：HTTP {r.status_code} {r.text[:200]}")


def latest_edition(data_dir: Path) -> Edition | None:
    # 檔名排序不能用（"night" 字母序在 "noon" 前面），依日期＋午報／晚報排
    files = list_edition_files(data_dir / "editions")
    if not files:
        return None
    newest = max(files, key=lambda p: (p.stem[:10], p.stem.endswith("-night")))
    return load_edition(newest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="出刊後推播到 LINE")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--dry-run", action="store_true", help="只印出訊息，不傳")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    load_dotenv(ROOT / ".env", override=False)
    settings = load_settings().notify
    if settings is None:
        log.info("settings.yaml 沒有 notify 設定，不推播")
        return 0
    ed = latest_edition(args.data_dir)
    if ed is None:
        log.info("還沒有任何一刊，不推播")
        return 0

    text = build_message(ed, settings)
    if args.dry_run:
        print(text)
        return 0

    token = os.environ.get(settings.line_token_env, "").strip()
    to = os.environ.get(settings.line_to_env, "").strip()
    if not token or not to:
        log.info("沒有設定 %s／%s，不推播", settings.line_token_env, settings.line_to_env)
        return 0
    try:
        send_line(text, token, to)
    except (RuntimeError, httpx.HTTPError) as e:
        log.error("%s", e)
        return 1
    log.info("已推播 %s 到 LINE", ed.edition_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
