"""網址與標題正規化、簡轉繁。"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import opencc

from pipeline.models import RawItem

# 追蹤用參數，去重時忽略
_TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|from|spm|share|feature|cmpid|ocid)$",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    """去掉追蹤參數、fragment、www./m. 前綴與結尾斜線，用來判斷是否同一篇。"""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


# 標題開頭常見的標籤：【快訊】、快訊／、(影)、影/、〈紐約匯市〉、[인터뷰]
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:【[^】]{1,8}】|\[[^\]]{1,8}\]|〈[^〉]{1,8}〉|《[^》]{1,8}》|[（(][^）)]{1,4}[）)]|"
    r"(?:快訊|獨家|影|圖|直播|更新|最新)\s*[／/｜|:：])\s*"
)
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def title_key(title: str) -> str:
    """比較標題相似度用的鍵：去掉開頭標籤、標點、空白，轉小寫。"""
    t = title
    for _ in range(3):  # 可能疊好幾個標籤
        new = _TITLE_PREFIX_RE.sub("", t)
        if new == t:
            break
        t = new
    return _NON_WORD_RE.sub("", t).lower()


@lru_cache(maxsize=1)
def _s2twp() -> opencc.OpenCC:
    return opencc.OpenCC("s2twp")


def to_taiwan_chinese(text: str) -> str:
    """簡體轉繁體並換成台灣用語（视频→影片、网络→網路）。"""
    return _s2twp().convert(text) if text else text


def localize(item: RawItem) -> RawItem:
    """簡中來源的標題與摘要先轉成台灣繁中；不需要 LLM。"""
    if item.language != "zh-CN":
        return item
    return item.model_copy(
        update={"title": to_taiwan_chinese(item.title), "summary": to_taiwan_chinese(item.summary)}
    )
