"""測試用的假 LLM provider：不連網路，依劇本回應或丟錯。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pipeline.llm.base import Completion
from pipeline.llm.router import LLMRouter

Handler = Callable[[str, str, dict], dict]


class FakeProvider:
    def __init__(self, name: str, script: list[Any] | Handler, model: str = "fake-1"):
        """script：依序回傳的 dict 或要丟出的 Exception；也可以是函式 (system, user, schema) -> dict。"""
        self.name = name
        self.model = model
        self.min_interval_seconds = 0
        self.max_batch_items = None
        self.max_output_tokens = None
        self.last_max_output_tokens = None
        self.script = script
        self.calls = 0

    def complete_json(self, system, user, schema, *, max_output_tokens) -> Completion:
        self.calls += 1
        self.last_max_output_tokens = max_output_tokens
        if callable(self.script):
            data = self.script(system, user, schema)
        else:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            data = step
        return Completion(
            data=data, input_tokens=100, output_tokens=50, provider=self.name, model=self.model
        )


def make_router(*providers: FakeProvider) -> tuple[LLMRouter, list[float]]:
    sleeps: list[float] = []
    router = LLMRouter(
        providers=list(providers),
        retry_backoff_seconds=20,
        max_retry_wait_seconds=60,
        sleep=sleeps.append,
    )
    return router, sleeps
