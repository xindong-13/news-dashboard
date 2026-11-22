"""LLM 回傳格式：pydantic 用來驗證，JSON schema 送給 provider 做 structured output。

JSON schema 手寫成「嚴格模式」相容的形式（每個屬性都 required、additionalProperties: false、
可為 null 的欄位用 type 陣列），三家 provider 都能吃。改欄位時兩邊要一起改。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pipeline.models import SUBCATEGORIES, Category, EsportsTag, League

CATEGORIES = ["taiwan", "world", "finance", "esports", "threads"]
ALL_SUBCATEGORIES = sorted({s for subs in SUBCATEGORIES.values() for s in subs})
ESPORTS_TAGS = ["transfer", "retirement", "tournament", "result", "controversy", "roster", "other"]
LEAGUES = ["lck", "lpl", "international", "other"]


# ---------------------------------------------------------------------------
# 分類＋翻標題
# ---------------------------------------------------------------------------


class ClassifiedItem(BaseModel):
    id: str
    category: Category
    subcategory: str
    importance: int = Field(ge=1, le=5)
    esports_tags: list[EsportsTag] = []
    league: League | None = None
    region_tags: list[Literal["taiwan"]] = []
    title_zh: str = Field(min_length=1)
    duplicate_of: str | None = None


class ClassifyOutput(BaseModel):
    items: list[ClassifiedItem]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


CLASSIFY_SCHEMA: dict[str, Any] = _obj(
    {
        "items": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "subcategory": {"type": "string", "enum": ALL_SUBCATEGORIES},
                    "importance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "esports_tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ESPORTS_TAGS},
                    },
                    "league": {"type": ["string", "null"], "enum": [*LEAGUES, None]},
                    "region_tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["taiwan"]},
                    },
                    "title_zh": {"type": "string"},
                    "duplicate_of": {"type": ["string", "null"]},
                }
            ),
        }
    }
)


# ---------------------------------------------------------------------------
# 跨批去重
# ---------------------------------------------------------------------------


class DuplicateGroup(BaseModel):
    keep: str
    duplicates: list[str]


class DedupeOutput(BaseModel):
    groups: list[DuplicateGroup]


DEDUPE_SCHEMA: dict[str, Any] = _obj(
    {
        "groups": {
            "type": "array",
            "items": _obj(
                {
                    "keep": {"type": "string"},
                    "duplicates": {"type": "array", "items": {"type": "string"}},
                }
            ),
        }
    }
)


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------


class SummaryItem(BaseModel):
    id: str
    summary_zh: str = ""  # 空字串的那則略過就好，不要因為一則讓整批作廢（Groq 實測會發生）
    why_it_matters: str | None = None


class SummaryOutput(BaseModel):
    items: list[SummaryItem]


SUMMARY_SCHEMA: dict[str, Any] = _obj(
    {
        "items": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "string"},
                    "summary_zh": {"type": "string"},
                    "why_it_matters": {"type": ["string", "null"]},
                }
            ),
        }
    }
)


# ---------------------------------------------------------------------------
# 導讀
# ---------------------------------------------------------------------------


class BriefingOutput(BaseModel):
    briefing: list[str] = Field(min_length=1, max_length=6)


BRIEFING_SCHEMA: dict[str, Any] = _obj({"briefing": {"type": "array", "items": {"type": "string"}}})
