"""規則式去重與合併：同網址去重，標題相似的合併成同一事件。

跨語言（英文 vs 中文）的同一事件這裡合併不了，交給 M3 的 LLM 判斷。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from rapidfuzz import fuzz, process

from pipeline.models import RawItem
from pipeline.processing.normalize import normalize_url, title_key

# 太短的標題不做模糊比對，避免誤合併（例如 Leaguepedia 的「Crisp leaves.」vs「Izo leaves.」）
MIN_KEY_LEN_CJK = 8
MIN_KEY_LEN_LATIN = 15
MAX_CLUSTER_SIZE = 10  # 防止 A≈B、B≈C 的連鎖合併把整串新聞黏成一團

_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def is_cjk(key: str) -> bool:
    return bool(_CJK_RE.search(key))


@dataclass
class Cluster:
    """一個事件：同一件事的多篇報導。items[0] 是代表（權重最高、最早）。"""

    items: list[RawItem] = field(default_factory=list)

    @property
    def primary(self) -> RawItem:
        return self.items[0]

    @property
    def source_names(self) -> set[str]:
        # 「自由時報（Google 新聞）」與「自由時報」算同一家
        return {publisher(i) for i in self.items}

    @property
    def max_weight(self) -> float:
        return max(i.weight for i in self.items)


def dedupe_by_url(items: list[RawItem]) -> list[RawItem]:
    """同一網址只留一則（保留權重最高的那個來源）。"""
    best: dict[str, RawItem] = {}
    for it in items:
        key = normalize_url(it.url)
        if key not in best or it.weight > best[key].weight:
            best[key] = it
    return list(best.values())


def publisher(item: RawItem) -> str:
    """實際發稿的媒體：「自由時報（Google 新聞）」→「自由時報」。"""
    return item.source_name.split("（")[0].strip()


class _UnionFind:
    """合併時追蹤每群的媒體集合，用來擋掉同一家媒體的模板標題被誤合併。"""

    def __init__(self, publishers: list[str]):
        n = len(publishers)
        self.parent = list(range(n))
        self.size = [1] * n
        self.pubs: list[set[str]] = [{p} for p in publishers]

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int, *, fuzzy: bool = False) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if fuzzy:
            # 模糊合併的限制：大小上限，且兩群不能有同一家媒體
            # （同一家不會把同一件事寫兩次，相似通常是模板標題，例如「Factset 最新調查：○○」）
            if self.size[ra] + self.size[rb] > MAX_CLUSTER_SIZE:
                return False
            if self.pubs[ra] & self.pubs[rb]:
                return False
        lo, hi = min(ra, rb), max(ra, rb)
        self.parent[hi] = lo
        self.size[lo] += self.size[hi]
        self.pubs[lo] |= self.pubs[hi]
        return True


def _sort_key(it: RawItem):
    ts = it.published_at.timestamp() if it.published_at else float("inf")
    return (-it.weight, ts)


def cluster_items(items: list[RawItem], threshold: int, threshold_cjk: int) -> list[Cluster]:
    """網址去重後，標題相似的合併成同一事件。

    相似度用 rapidfuzz 的字元層級 ratio（0～100）。中文標題各家改寫幅度大，
    同一事件常只有 60～80 分，所以中日韓文用較低的門檻 threshold_cjk。
    """
    items = dedupe_by_url(items)
    if not items:
        return []

    keys = [title_key(i.title) for i in items]
    cjk = [is_cjk(k) for k in keys]
    uf = _UnionFind([publisher(i) for i in items])

    # 完全相同的標題鍵直接合併（例如 Yahoo 轉載中央社）
    first_of: dict[str, int] = {}
    for idx, k in enumerate(keys):
        if not k:
            continue
        if k in first_of:
            uf.union(first_of[k], idx)
        else:
            first_of[k] = idx

    # 模糊比對：分數由高到低合併，連鎖合併受 MAX_CLUSTER_SIZE 限制
    long_idx = [
        i
        for i, k in enumerate(keys)
        if len(k) >= (MIN_KEY_LEN_CJK if cjk[i] else MIN_KEY_LEN_LATIN)
    ]
    if len(long_idx) > 1:
        long_keys = [keys[i] for i in long_idx]
        cutoff = min(threshold, threshold_cjk)
        scores = process.cdist(
            long_keys, long_keys, scorer=fuzz.ratio, score_cutoff=cutoff, workers=-1
        )
        rows, cols = np.nonzero(np.triu(scores, k=1))
        pairs = sorted(zip(rows, cols, strict=True), key=lambda rc: -scores[rc[0], rc[1]])
        for r, c in pairs:
            a, b = long_idx[r], long_idx[c]
            need = threshold_cjk if (cjk[a] and cjk[b]) else threshold
            if scores[r, c] >= need:
                uf.union(a, b, fuzzy=True)

    groups: dict[int, list[RawItem]] = {}
    for idx, it in enumerate(items):
        groups.setdefault(uf.find(idx), []).append(it)

    return [Cluster(items=sorted(g, key=_sort_key)) for g in groups.values()]
