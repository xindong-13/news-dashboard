"""Google Gemini（generateContent REST API）。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from pipeline.llm.base import Completion, FatalProviderError, InvalidOutput, LLMError, RateLimited

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _retry_delay(body: dict) -> float | None:
    """從 429 回應的 RetryInfo 取出建議等待秒數，例如 "37s"。"""
    for d in body.get("error", {}).get("details", []):
        m = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
        if m:
            return float(m[1])
    return None


class GeminiProvider:
    name = "gemini"

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
        self._headers = {"x-goog-api-key": api_key}

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], *, max_output_tokens: int
    ) -> Completion:
        gen: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": max_output_tokens,
            "temperature": self.options.get("temperature", 0.2),
        }
        if "thinking_level" in self.options:
            gen["thinkingConfig"] = {"thinkingLevel": self.options["thinking_level"]}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        try:
            r = self._client.post(API.format(model=self.model), headers=self._headers, json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"gemini 連線失敗：{type(e).__name__}") from e

        payload = (
            r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        )
        if r.status_code == 429:
            raise RateLimited("gemini 429", _retry_delay(payload))
        if r.status_code in (400, 401, 403, 404):
            msg = payload.get("error", {}).get("message", r.text[:200])
            raise FatalProviderError(f"gemini {r.status_code}：{msg}")
        if r.status_code != 200:
            raise LLMError(f"gemini HTTP {r.status_code}")

        cands = payload.get("candidates") or []
        if not cands:
            reason = payload.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise InvalidOutput(f"gemini 沒有回應內容：{reason}")
        finish = cands[0].get("finishReason")
        if finish not in (None, "STOP"):
            raise InvalidOutput(f"gemini finishReason={finish}")
        parts = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise InvalidOutput(f"gemini 回傳不是 JSON：{e}") from e

        usage = payload.get("usageMetadata", {})
        return Completion(
            data=data,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            provider=self.name,
            model=self.model,
        )
