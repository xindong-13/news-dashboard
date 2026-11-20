"""Anthropic Claude（官方 Python SDK，structured outputs）。"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from pipeline.llm.base import Completion, FatalProviderError, InvalidOutput, LLMError, RateLimited


class AnthropicProvider:
    name = "anthropic"

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
    ):
        self.model = model
        self.min_interval_seconds = min_interval_seconds
        self.max_batch_items = max_batch_items
        self.max_output_tokens = max_output_tokens
        self.options = options or {}
        # 重試交給 router 統一處理
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], *, max_output_tokens: int
    ) -> Completion:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if "effort" in self.options:
            output_config["effort"] = self.options["effort"]
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.RateLimitError as e:
            retry = e.response.headers.get("retry-after")
            raise RateLimited("anthropic 429", float(retry) if retry else None) from e
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as e:
            raise FatalProviderError(f"anthropic {type(e).__name__}：{e.message}") from e
        except anthropic.BadRequestError as e:
            raise InvalidOutput(f"anthropic 400：{e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"anthropic HTTP {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"anthropic 連線失敗：{type(e).__name__}") from e

        if resp.stop_reason != "end_turn":
            raise InvalidOutput(f"anthropic stop_reason={resp.stop_reason}")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise InvalidOutput(f"anthropic 回傳不是 JSON：{e}") from e
        return Completion(
            data=data,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            provider=self.name,
            model=self.model,
        )
