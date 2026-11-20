"""Groq（OpenAI 相容的 chat completions API）。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from pipeline.llm.base import (
    Completion,
    FatalProviderError,
    InvalidOutput,
    LLMError,
    RateLimited,
    RequestTooLarge,
)

API = "https://api.groq.com/openai/v1/chat/completions"


def _parse_reset(value: str | None) -> float | None:
    """Groq 的 retry-after 是秒數；x-ratelimit-reset-* 是 "1m26.4s" 這種格式。"""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    m = re.fullmatch(r"(?:(\d+)m)?([\d.]+)s", value)
    return int(m[1] or 0) * 60 + float(m[2]) if m else None


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 120,
        min_interval_seconds: float = 0,
        options: dict[str, Any] | None = None,
        max_batch_items: int | None = None,
        max_output_tokens: int | None = None,
        client: httpx.Client | None = None,
    ):
        self.model = model
        self.min_interval_seconds = min_interval_seconds
        self.max_batch_items = max_batch_items
        self.max_output_tokens = max_output_tokens
        self.options = options or {}
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], *, max_output_tokens: int
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self.options.get("temperature", 0.2),
            "max_completion_tokens": max_output_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": schema},
            },
        }
        if "reasoning_effort" in self.options:
            body["reasoning_effort"] = self.options["reasoning_effort"]
        try:
            r = self._client.post(API, headers=self._headers, json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"groq 連線失敗：{type(e).__name__}") from e

        if r.status_code == 429:
            wait = _parse_reset(r.headers.get("retry-after")) or _parse_reset(
                r.headers.get("x-ratelimit-reset-tokens")
            )
            raise RateLimited("groq 429", wait)
        if r.status_code == 413:
            # 免費版會把「輸入＋max_completion_tokens」算進每分鐘上限，超過就回 413
            raise RequestTooLarge(f"groq 413：{r.text[:200]}")
        if r.status_code in (401, 403, 404):
            raise FatalProviderError(f"groq {r.status_code}：{r.text[:200]}")
        if r.status_code == 400:
            # 400 也可能是「輸出不符合 schema」，這種重試可能會好
            raise InvalidOutput(f"groq 400：{r.text[:200]}")
        if r.status_code != 200:
            raise LLMError(f"groq HTTP {r.status_code}")

        payload = r.json()
        choice = (payload.get("choices") or [{}])[0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise InvalidOutput(f"groq finish_reason={choice.get('finish_reason')}")
        try:
            data = json.loads(choice.get("message", {}).get("content") or "")
        except json.JSONDecodeError as e:
            raise InvalidOutput(f"groq 回傳不是 JSON：{e}") from e
        usage = payload.get("usage", {})
        return Completion(
            data=data,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            provider=self.name,
            model=self.model,
        )
