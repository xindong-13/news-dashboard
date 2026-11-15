from pipeline.processing.cluster import cluster_items, dedupe_by_url
from pipeline.processing.normalize import localize, normalize_url, title_key
from tests.helpers import item


def _cluster(items):
    return cluster_items(items, threshold=85, threshold_cjk=60)


def test_normalize_url_strips_tracking_and_variants():
    a = normalize_url("https://www.example.com/news/123/?utm_source=x&id=5#top")
    b = normalize_url("http://m.example.com/news/123?id=5&fbclid=abc")
    assert a == b == "https://example.com/news/123?id=5"


def test_title_key_strips_prefix_tags_and_punctuation():
    assert title_key("快訊／新北火燒船！6名船員獲救") == title_key("新北火燒船 6名船員獲救")
    assert title_key("【獨家】(影) 某某事件") == title_key("某某事件")


def test_same_url_deduped_keeps_higher_weight():
    out = dedupe_by_url(
        [
            item("A", "https://x.com/1?utm_source=g", source="low", weight=0.5),
            item("A", "https://www.x.com/1", source="high", weight=1.5),
        ]
    )
    assert len(out) == 1 and out[0].source_id == "high"


def test_same_event_from_different_outlets_merged_with_all_sources():
    clusters = _cluster(
        [
            item("鼻頭漁港外海漁船起火燃燒 6船員跳海逃生獲救", "https://ltn/1", name="自由時報"),
            item("瑞芳鼻頭外海漁船起火 6船員跳海逃生獲救", "https://yahoo/1", name="Yahoo 新聞"),
            item("立法院今天三讀通過預算案", "https://cna/9", name="中央社"),
        ]
    )
    sizes = sorted(len(c.items) for c in clusters)
    assert sizes == [1, 2]
    merged = next(c for c in clusters if len(c.items) == 2)
    assert {i.url for i in merged.items} == {"https://ltn/1", "https://yahoo/1"}


def test_same_publisher_template_titles_not_merged():
    clusters = _cluster(
        [
            item(
                "鉅亨速報 - Factset 最新調查：創意(3443-TW)EPS預估上修至62.41元",
                "https://c/1",
                name="鉅亨網",
            ),
            item(
                "鉅亨速報 - Factset 最新調查：嘉澤(3533-TW)EPS預估下修至77.55元",
                "https://c/2",
                name="鉅亨網",
            ),
        ]
    )
    assert len(clusters) == 2


def test_short_english_titles_not_fuzzy_merged():
    clusters = _cluster(
        [
            item("Crisp leaves.", "https://lp/1", name="A", language="en"),
            item("Izo leaves.", "https://lp/2", name="B", language="en"),
        ]
    )
    assert len(clusters) == 2


def test_english_near_duplicates_merged():
    clusters = _cluster(
        [
            item(
                "Israel kills Hamas commander in Gaza strike",
                "https://a/1",
                name="BBC",
                language="en",
            ),
            item(
                "Israel kills Hamas commander in a Gaza strike",
                "https://b/1",
                name="NPR",
                language="en",
            ),
        ]
    )
    assert len(clusters) == 1


def test_simplified_chinese_merges_after_localize():
    a = localize(
        item("中国战队BLG夺得英雄联盟全球总决赛冠军", "https://cn/1", name="新浪", language="zh-CN")
    )
    b = item("中國戰隊BLG奪得英雄聯盟全球總決賽冠軍", "https://tw/1", name="紫荊網")
    assert a.title == "中國戰隊BLG奪得英雄聯盟全球總決賽冠軍"
    assert len(_cluster([a, b])) == 1


def test_cluster_primary_is_highest_weight():
    clusters = _cluster(
        [
            item("立法院三讀通過年度總預算案", "https://g/1", name="Google", weight=0.9),
            item("立法院三讀通過年度總預算案", "https://cna/1", name="中央社", weight=1.3),
        ]
    )
    assert clusters[0].primary.source_name == "中央社"
