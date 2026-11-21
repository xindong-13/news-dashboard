"""AI 處理流程：分類、重複合併、摘要、導讀、部分失敗的降級。"""

import json
from datetime import date

from pipeline.config import OffseasonPeriod, load_settings
from pipeline.fetchers import FetchResult
from pipeline.llm.base import FatalProviderError, LLMError
from pipeline.llm.schemas import BRIEFING_SCHEMA, CLASSIFY_SCHEMA, DEDUPE_SCHEMA, SUMMARY_SCHEMA
from pipeline.main import build_edition
from pipeline.processing.ai import in_offseason, process_with_llm
from pipeline.processing.cluster import Cluster
from pipeline.processing.edition import plan_edition
from pipeline.taiwan_terms import to_taiwan
from tests.fakes import FakeProvider, make_router
from tests.helpers import item, tpe

NOW = tpe("2026-11-16 11:40")


def _payload(user: str) -> list[dict]:
    return json.loads(user[user.index("[") :])


def scripted_llm(
    classify_fn, summary_fn=None, briefing=("導讀一", "導讀二", "導讀三"), dedupe_fn=None
):
    """依 schema 判斷是哪個步驟，回傳對應的假結果。"""

    def handler(system, user, schema):
        if schema is CLASSIFY_SCHEMA:
            return {"items": [classify_fn(x) for x in _payload(user)]}
        if schema is DEDUPE_SCHEMA:
            return {"groups": dedupe_fn(_payload(user)) if dedupe_fn else []}
        if schema is SUMMARY_SCHEMA:
            fn = summary_fn or (
                lambda x: {
                    "id": x["id"],
                    "summary_zh": f"{x['title_zh']}的摘要。",
                    "why_it_matters": "很重要。",
                }
            )
            return {"items": [fn(x) for x in _payload(user)]}
        if schema is BRIEFING_SCHEMA:
            return {"briefing": list(briefing)}
        raise AssertionError("unknown schema")

    return handler


def cls(x, **kw):
    base = {
        "id": x["id"],
        "category": x["hint"]["category"],
        "subcategory": "other",
        "importance": 3,
        "esports_tags": [],
        "league": None,
        "region_tags": [],
        "title_zh": x["title"],
        "duplicate_of": None,
    }
    return base | kw


def _clusters(*specs):
    return [Cluster(items=[item(t, u, name=n, **kw)]) for t, u, n, kw in specs]


def test_classify_translate_summarize_and_brief():
    clusters = _clusters(
        (
            "Fed signals rate hike",
            "https://cnbc/1",
            "CNBC",
            {"category": "finance", "language": "en"},
        ),
        ("BLG wins Worlds", "https://dex/1", "Dexerto", {"category": "esports", "language": "en"}),
        ("某地方小新聞", "https://ltn/1", "自由時報", {}),
    )

    def classify_fn(x):
        if "Fed" in x["title"]:
            return cls(x, subcategory="macro", importance=5, title_zh="美聯儲暗示加息")
        if "BLG" in x["title"]:
            return cls(
                x,
                subcategory="lol",
                importance=5,
                esports_tags=["tournament", "result", "result"],
                league="lpl",
                title_zh="BLG 奪得世界賽冠軍",
            )
        return cls(x, subcategory="不存在的分類", importance=1, league="lck")

    router, _ = make_router(FakeProvider("gemini", scripted_llm(classify_fn)))
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")

    by_title = {e.title_zh: e for e in res.events}
    fed = by_title["聯準會暗示升息"]  # LLM 給中國用語，後處理改成台灣用語
    assert fed.ai_processed and fed.importance == 5 and fed.subcategory == "macro"
    assert fed.original_title == "Fed signals rate hike"
    assert fed.summary_zh == "聯準會暗示升息的摘要。"
    assert fed.why_it_matters == "很重要。"

    blg = by_title["BLG 奪得世界賽冠軍"]
    assert blg.league == "lpl" and blg.esports_tags == ["tournament", "result"]

    small = by_title["某地方小新聞"]
    assert small.subcategory == "other"  # 不合法的子分類改成 other
    assert small.league is None  # 非電競不留 league
    assert small.summary_zh == ""  # 重要度 1 不寫摘要

    assert [e.id for e in res.events] == ["evt_001", "evt_002", "evt_003"]
    assert res.events[-1].importance == 1  # 依重要度排序
    assert res.briefing == ["導讀一", "導讀二", "導讀三"]
    assert router.usage().degraded is False


def test_llm_duplicate_and_cross_batch_title_merge():
    clusters = _clusters(
        (
            "BLG beat Gen.G to win Worlds",
            "https://dex/1",
            "Dexerto",
            {"category": "esports", "language": "en"},
        ),
        ("BLG一穿四奪冠", "https://hupu/1", "虎撲", {"category": "esports", "language": "zh-CN"}),
        ("젠지, BLG에 역전패", "https://kr/1", "뉴스1", {"category": "esports", "language": "ko"}),
    )
    titles = {
        "c1": "BLG擊敗GEN奪得2026英雄聯盟全球總決賽冠軍",
        "c2": "BLG擊敗Gen.G奪下英雄聯盟世界賽冠軍",  # 不同批也能靠標題相似度合併
        "c3": "Gen.G不敵BLG",
    }

    def classify_fn(x):
        dup = "c1" if x["id"] == "c3" else None  # LLM 自己判定 c3 是 c1 的重複
        return cls(
            x,
            subcategory="lol",
            importance=5 if x["id"] == "c1" else 4,
            title_zh=titles[x["id"]],
            duplicate_of=dup,
        )

    router, _ = make_router(FakeProvider("gemini", scripted_llm(classify_fn)))
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")
    assert len(res.events) == 1
    ev = res.events[0]
    assert ev.title_zh == titles["c1"]
    assert {s.name for s in ev.sources} == {"Dexerto", "虎撲", "뉴스1"}


def test_failed_batch_falls_back_to_rule_cards_but_others_keep_ai():
    settings = load_settings()
    settings = settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"classify_batch_size": 1})}
    )
    clusters = _clusters(
        ("新聞甲", "https://a/1", "A", {}),
        ("新聞乙", "https://b/1", "B", {}),
    )

    def classify_fn(x):
        if x["title"] == "新聞乙":
            raise LLMError("503")
        return cls(x, importance=4, title_zh="AI 標題甲")

    # 只有一個 provider：新聞乙那批重試後仍失敗 → 只有那則降級，後續步驟照常
    router, _ = make_router(FakeProvider("gemini", scripted_llm(classify_fn)))
    res = process_with_llm(clusters, router, settings, NOW, "noon")
    by_title = {e.title_zh: e for e in res.events}
    assert by_title["AI 標題甲"].ai_processed is True
    assert by_title["AI 標題甲"].summary_zh == "AI 標題甲的摘要。"
    assert by_title["新聞乙"].ai_processed is False
    assert res.briefing == ["導讀一", "導讀二", "導讀三"]


def test_batches_shrink_when_falling_back_to_small_provider():
    clusters = _clusters(*[(f"新聞{i}", f"https://x/{i}", f"媒體{i}", {}) for i in range(30)])
    sizes = []

    def classify_fn(x):
        return cls(x, title_zh=f"標題{x['id']}")

    def groq_handler(system, user, schema):
        if schema is CLASSIFY_SCHEMA:
            sizes.append(len(_payload(user)))
        return scripted_llm(classify_fn)(system, user, schema)

    g = FakeProvider("gemini", [FatalProviderError("bad key")])
    q = FakeProvider("groq", groq_handler)
    q.max_batch_items = 4
    router, _ = make_router(g, q)
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")
    # 第一批照 Gemini 的大小切（25 則），Gemini 停用後由 Groq 接手；
    # 之後的批次依 Groq 的上限切成 4 則一批
    assert sizes == [25, 4, 1]
    assert all(e.ai_processed for e in res.events)


def test_all_llm_fail_still_publishes_degraded_edition(tmp_path):
    """驗收：全部 provider 都失敗時照常出刊，標示未經 AI 整理。"""
    settings = load_settings()
    items = [item("立法院三讀通過總預算案", "https://cna/1", name="中央社", at="2026-11-16 09:00")]
    fetcher = lambda sources, s: FetchResult(items=items)  # noqa: E731
    router, _ = make_router(
        FakeProvider("gemini", [LLMError("x"), LLMError("x")]),
        FakeProvider("groq", [LLMError("y"), LLMError("y")]),
    )
    plan = plan_edition(NOW, "auto", settings.edition, tmp_path / "editions")
    ed = build_edition(plan, settings, [], tmp_path, fetcher=fetcher, router=router)
    assert ed.llm_usage.degraded is True
    assert ed.llm_usage.errors
    assert [e.title_zh for e in ed.events] == ["立法院三讀通過總預算案"]
    assert ed.events[0].ai_processed is False


def test_offseason_wraps_year():
    periods = [OffseasonPeriod(start="11-01", end="01-31")]
    assert in_offseason(date(2026, 11, 16), periods)
    assert in_offseason(date(2027, 1, 31), periods)
    assert not in_offseason(date(2027, 2, 1), periods)
    assert not in_offseason(date(2026, 10, 31), periods)


def test_taiwan_terms_safe_on_traditional_text():
    assert to_taiwan("美联储加息，这段视频在网络上疯传") == "聯準會升息，這段影片在網路上瘋傳"
    assert to_taiwan("特朗普與普京會面，英偉達芯片") == "川普與普丁會面，輝達晶片"
    # 不能誤傷台灣常用詞
    assert to_taiwan("選民支持翻轉，台灣，資訊安全") == "選民支持翻轉，台灣，資訊安全"
    assert to_taiwan("白宮宣佈新政策") == "白宮宣布新政策"


def test_cross_batch_dedupe_by_llm():
    clusters = _clusters(
        ("BLG wins Worlds", "https://dex/1", "Dexerto", {"category": "esports", "language": "en"}),
        ("BLG終結LCK四連霸", "https://hupu/1", "虎撲", {"category": "esports"}),
        ("BLG冠軍造型投票", "https://hupu/2", "虎撲", {"category": "esports"}),
        ("立法院三讀總預算", "https://cna/1", "中央社", {}),
    )
    titles = {
        "c1": "BLG擊敗LCK四隊奪得世界賽冠軍",
        "c2": "BLG擊敗Gen.G奪下世界賽首冠 終結LCK四連霸",
        "c3": "BLG冠軍造型投票結果揭曉",
        "c4": "立法院三讀通過總預算",
    }
    seen_payload = []

    def dedupe_fn(payload):
        seen_payload.extend(payload)
        # LLM 回傳：c2 是 c1 的重複；也誤把不同類別的 c4 列進來（應被擋下）；c99 不存在
        return [{"keep": "c1", "duplicates": ["c2", "c4", "c99"]}]

    def classify_fn(x):
        imp = {"c1": 4, "c2": 5, "c3": 3, "c4": 4}[x["id"]]
        return cls(
            x,
            subcategory="lol" if x["id"] != "c4" else "politics",
            importance=imp,
            title_zh=titles[x["id"]],
        )

    router, _ = make_router(FakeProvider("gemini", scripted_llm(classify_fn, dedupe_fn=dedupe_fn)))
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")
    titles_out = [e.title_zh for e in res.events]
    assert titles["c2"] not in titles_out
    assert titles["c4"] in titles_out  # 不同類別不合併
    blg = next(e for e in res.events if e.title_zh == titles["c1"])
    assert blg.importance == 5  # 取較高的重要度
    assert {s.name for s in blg.sources} == {"Dexerto", "虎撲"}
    assert {p["id"] for p in seen_payload} == {"c1", "c2", "c3", "c4"}


def test_one_empty_summary_does_not_drop_the_batch():
    clusters = _clusters(
        ("新聞甲", "https://a/1", "中央社", {}),
        ("新聞乙", "https://a/2", "中央社", {}),
    )

    def summary_fn(x):
        text = "" if x["title_zh"] == "新聞乙" else f"{x['title_zh']}的摘要。"
        return {"id": x["id"], "summary_zh": text, "why_it_matters": None}

    llm = scripted_llm(lambda x: cls(x, importance=3), summary_fn=summary_fn)
    router, _ = make_router(FakeProvider("groq", llm))
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")
    by_title = {e.title_zh: e for e in res.events}
    assert by_title["新聞甲"].summary_zh == "新聞甲的摘要。"
    assert by_title["新聞乙"].summary_zh == ""


def test_merge_by_shared_names_across_languages():
    """英文、韓文的同一則轉會新聞翻成中文後措辭差很多，靠標題裡的隊名＋選手名合併。"""
    clusters = _clusters(
        (
            "Dplus Kia Sign Doran and Kanavi for LCK 2027 Season",
            "https://hotspawn/1",
            "Hotspawn",
            {"category": "esports", "language": "en"},
        ),
        (
            "[오피셜] 디플러스 기아, '도란-카나비' 영입",
            "https://inven/1",
            "Inven",
            {"category": "esports", "language": "ko"},
        ),
        (
            "Dplus KIA reportedly close to signing Delight",
            "https://sheep/1",
            "Sheep Esports",
            {"category": "esports", "language": "en"},
        ),
    )
    titles = {
        "c1": "Dplus KIA官宣簽下Doran與Kanavi備戰2027年LCK賽季",
        "c2": "Dplus KIA 官宣網羅 Doran 與 Kanavi 打造強悍上野陣容",
        "c3": "傳聞：Delight 將加盟 Dplus KIA",  # 同隊不同事件，只共用 2 個名詞，不合併
    }

    def classify_fn(x):
        imp = {"c1": 5, "c2": 4, "c3": 3}[x["id"]]
        return cls(x, subcategory="lol", importance=imp, title_zh=titles[x["id"]])

    router, _ = make_router(FakeProvider("gemini", scripted_llm(classify_fn)))
    res = process_with_llm(clusters, router, load_settings(), NOW, "noon")
    assert len(res.events) == 2
    main = next(e for e in res.events if "Doran" in e.title_zh)
    assert main.importance == 5
    assert {s.name for s in main.sources} == {"Hotspawn", "Inven"}
