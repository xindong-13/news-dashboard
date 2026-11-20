"""Provider fallback chain：依設定順序嘗試，失敗退避重試一次，再換下一個。"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from pipeline.config import LLMSettings, ProviderSettings
from pipeline.llm.base import (
    FatalProviderError,
    InvalidOutput,
    LLMError,
    LLMProvider,
    RateLimited,
    RequestTooLarge,
)
from pipeline.models import LLMUsage, ProviderUsage

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

MAX_ATTEMPTS = 2  # 同一個 provider 失敗後退避重試一次
MAX_RATE_LIMIT_WAITS = 3  # 每分鐘額度用完時，同一次呼叫最多等幾輪
STRIKES_TO_DISABLE = 2  # 連續幾次「呼叫失敗」就停用這個 provider


class AllProvidersFailed(LLMError):
    pass


def build_provider(cfg: ProviderSettings, timeout: float) -> LLMProvider | None:
    """依設定建立 provider；沒有 API key 就回傳 None（跳過）。"""
    key = os.environ.get(cfg.api_key_env, "").strip()
    if not cfg.enabled or not key:
        return None
    kw: dict[str, Any] = dict(
        timeout=timeout,
        min_interval_seconds=cfg.min_interval_seconds,
        options=cfg.options,
        max_batch_items=cfg.max_batch_items,
        max_output_tokens=cfg.max_output_tokens,
    )
    if cfg.name == "gemini":
        from pipeline.llm.gemini import GeminiProvider

        return GeminiProvider(key, cfg.model, **kw)
    if cfg.name == "groq":
        from pipeline.llm.groq import GroqProvider

        return GroqProvider(key, cfg.model, **kw)
    if cfg.name == "anthropic":
        from pipeline.llm.anthropic import AnthropicProvider

        return AnthropicProvider(key, cfg.model, **kw)
    raise ValueError(f"未知的 LLM provider：{cfg.name}")


@dataclass
class _Usage:
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMRouter:
    providers: list[LLMProvider]
    retry_backoff_seconds: float = 20
    max_retry_wait_seconds: float = 90
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    errors: list[str] = field(default_factory=list)
    _dead: set[int] = field(default_factory=set)
    _strikes: dict[int, int] = field(default_factory=dict)
    _last_call: dict[int, float] = field(default_factory=dict)
    _usage: dict[str, _Usage] = field(default_factory=dict)
    _successes: int = 0

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> LLMRouter:
        providers = []
        for cfg in settings.providers:
            p = build_provider(cfg, settings.request_timeout_seconds)
            if p is None:
                log.info("LLM provider %s 未啟用或沒有 API key，跳過", cfg.name)
            else:
                providers.append(p)
        return cls(
            providers=providers,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            max_retry_wait_seconds=settings.max_retry_wait_seconds,
        )

    # -- 狀態 ----------------------------------------------------------------
    @property
    def available(self) -> bool:
        return any(i not in self._dead for i in range(len(self.providers)))

    def batch_limit(self, default: int) -> int:
        """下一次呼叫會先用的 provider 能吃多大的批次（例如 Groq 每分鐘 token 少，要切小批）。"""
        for i, p in enumerate(self.providers):
            if i not in self._dead:
                return min(default, p.max_batch_items or default)
        return default

    # -- 內部 ----------------------------------------------------------------
    def _pace(self, idx: int, p: LLMProvider) -> None:
        last = self._last_call.get(idx)
        if last is not None and p.min_interval_seconds:
            wait = last + p.min_interval_seconds - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last_call[idx] = self.clock()

    def _record(self, p: LLMProvider, input_tokens: int, output_tokens: int) -> None:
        u = self._usage.setdefault(f"{p.name}:{p.model}", _Usage(model=p.model))
        u.calls += 1
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens

    def _note(self, task: str, p: LLMProvider, reason: str) -> None:
        msg = f"{task} / {p.name}：{reason}"
        log.warning(msg)
        self.errors.append(msg[:300])

    def _disable(self, idx: int, p: LLMProvider, why: str) -> None:
        self._dead.add(idx)
        log.warning("LLM provider %s 本次執行停用（%s）", p.name, why)

    # -- 公開 ----------------------------------------------------------------
    def run(
        self,
        task: str,
        system: str,
        user: str,
        output_model: type[T],
        schema: dict[str, Any],
        *,
        max_output_tokens: int,
    ) -> T:
        """依序嘗試各 provider，回傳驗證過的結果；全部失敗丟 AllProvidersFailed。

        每個 provider：
        - 回應不合格式、暫時性錯誤（5xx、逾時）：退避後重試一次，再失敗換下一家
        - 429：依對方建議等待後重試（最多 3 輪）；要等太久（每日額度用完）就停用這家
        - 請求太大（413）：換下一家，不重試
        - key 錯誤、模型不存在：停用這家
        - 連續 2 次呼叫都失敗（不是單次的兩個 attempt）：停用這家，之後的批次直接用下一家
        """
        for idx, p in enumerate(self.providers):
            if idx in self._dead:
                continue
            out_tokens = min(max_output_tokens, p.max_output_tokens or max_output_tokens)
            failures = rate_waits = 0
            while True:
                self._pace(idx, p)
                wait = 0.0
                try:
                    c = p.complete_json(system, user, schema, max_output_tokens=out_tokens)
                    self._record(p, c.input_tokens, c.output_tokens)
                    result = output_model.model_validate(c.data)
                    self._successes += 1
                    self._strikes[idx] = 0
                    return result
                except ValidationError as e:
                    first = e.errors()[0]
                    where = ".".join(str(x) for x in first["loc"])
                    self._note(
                        task,
                        p,
                        f"回應不符合格式（{e.error_count()} 個錯誤，例如 {where}：{first['msg']}）",
                    )
                    failures += 1
                except InvalidOutput as e:
                    self._note(task, p, str(e))
                    failures += 1
                except RequestTooLarge as e:
                    self._note(task, p, f"{e}，換下一家")
                    break
                except RateLimited as e:
                    if e.retry_after and e.retry_after > self.max_retry_wait_seconds:
                        self._note(task, p, f"{e}（要等 {e.retry_after:.0f} 秒，視為額度用完）")
                        self._disable(idx, p, "額度用完")
                        break
                    rate_waits += 1
                    self._note(task, p, f"{e}（第 {rate_waits} 次）")
                    if rate_waits > MAX_RATE_LIMIT_WAITS:
                        failures = MAX_ATTEMPTS
                    else:
                        self.sleep(e.retry_after or self.retry_backoff_seconds)
                        continue
                except FatalProviderError as e:
                    self._note(task, p, str(e))
                    self._disable(idx, p, "設定錯誤")
                    break
                except LLMError as e:
                    self._note(task, p, str(e))
                    failures += 1
                    wait = self.retry_backoff_seconds

                if failures >= MAX_ATTEMPTS:
                    self._strikes[idx] = self._strikes.get(idx, 0) + 1
                    if self._strikes[idx] >= STRIKES_TO_DISABLE:
                        self._disable(idx, p, f"連續 {STRIKES_TO_DISABLE} 次呼叫失敗")
                    break
                if wait:
                    self.sleep(wait)
        raise AllProvidersFailed(f"{task}：所有 LLM provider 都失敗")

    def usage(self) -> LLMUsage:
        breakdown = [
            ProviderUsage(
                provider=key.split(":")[0],
                model=u.model,
                calls=u.calls,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
            )
            for key, u in self._usage.items()
        ]
        main = max(breakdown, key=lambda b: b.calls, default=None)
        return LLMUsage(
            provider=main.provider if main else None,
            model=main.model if main else None,
            input_tokens=sum(b.input_tokens for b in breakdown),
            output_tokens=sum(b.output_tokens for b in breakdown),
            calls=sum(b.calls for b in breakdown),
            degraded=self._successes == 0,
            breakdown=breakdown,
            errors=self.errors[:30],
        )
