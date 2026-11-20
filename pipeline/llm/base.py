"""LLM provider 的共同介面與錯誤類型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    """provider 呼叫失敗（網路、5xx、壞掉的回應等）。"""


class RateLimited(LLMError):
    """429：額度用完或太頻繁。retry_after 是對方建議等待的秒數（若有）。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidOutput(LLMError):
    """回應不是合法 JSON、被截斷、或被拒答。"""


class RequestTooLarge(LLMError):
    """請求超過 provider 的單次上限（例如 Groq 免費版每分鐘 token 數）：換下一家，不重試。"""


class FatalProviderError(LLMError):
    """重試也沒用的錯誤（key 錯誤、模型不存在）：這個 provider 本次執行直接停用。"""


@dataclass
class Completion:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str
    model: str
    min_interval_seconds: float
    max_batch_items: int | None  # 這家一次最多處理幾則（None＝不限制）
    max_output_tokens: int | None  # 這家的輸出上限（None＝依呼叫端）

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], *, max_output_tokens: int
    ) -> Completion:
        """送出 system + user，要求回傳符合 schema 的 JSON 物件。"""
        ...
