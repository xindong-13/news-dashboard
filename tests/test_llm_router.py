"""Provider fallback 與 JSON 驗證。"""

import pytest
from pydantic import BaseModel

from pipeline.config import ProviderSettings
from pipeline.llm.base import (
    FatalProviderError,
    InvalidOutput,
    LLMError,
    RateLimited,
    RequestTooLarge,
)
from pipeline.llm.router import AllProvidersFailed, build_provider
from tests.fakes import FakeProvider, make_router


class Out(BaseModel):
    answer: int


SCHEMA = {"type": "object"}
OK = {"answer": 42}


def run(router):
    return router.run("測試", "sys", "user", Out, SCHEMA, max_output_tokens=100)


def test_first_provider_success():
    g = FakeProvider("gemini", [OK])
    q = FakeProvider("groq", [OK])
    router, sleeps = make_router(g, q)
    assert run(router).answer == 42
    assert (g.calls, q.calls) == (1, 0)
    usage = router.usage()
    assert usage.degraded is False
    assert usage.provider == "gemini"
    assert (usage.input_tokens, usage.output_tokens, usage.calls) == (100, 50, 1)


def test_rate_limit_backs_off_then_retries_same_provider():
    g = FakeProvider("gemini", [RateLimited("429", retry_after=37), OK])
    router, sleeps = make_router(g, FakeProvider("groq", []))
    assert run(router).answer == 42
    assert g.calls == 2
    assert sleeps == [37]


def test_long_rate_limit_means_quota_exhausted_switch_immediately():
    """每日額度用完（要等很久）就不等了，直接換下一家，並停用這家。"""
    g = FakeProvider("gemini", [RateLimited("429", retry_after=3600)])
    q = FakeProvider("groq", [OK, OK])
    router, sleeps = make_router(g, q)
    assert run(router).answer == 42
    assert sleeps == []
    run(router)
    assert g.calls == 1


def test_per_minute_rate_limit_waits_several_rounds():
    g = FakeProvider(
        "gemini", [RateLimited("429", 10), RateLimited("429", 10), RateLimited("429", 10), OK]
    )
    router, sleeps = make_router(g)
    assert run(router).answer == 42
    assert sleeps == [10, 10, 10]


def test_wrong_gemini_key_falls_back_to_groq_without_retry():
    """驗收：刻意讓 Gemini 失敗（錯誤的 key），會自動改用 Groq。"""
    g = FakeProvider("gemini", [FatalProviderError("gemini 400：API key not valid")])
    q = FakeProvider("groq", [OK, OK])
    router, sleeps = make_router(g, q)
    assert run(router).answer == 42
    assert (g.calls, q.calls) == (1, 1)
    assert sleeps == []  # key 錯誤重試也沒用，不等待
    # 之後的批次直接用 Groq，不再浪費時間試 Gemini
    run(router)
    assert (g.calls, q.calls) == (1, 2)
    usage = router.usage()
    assert usage.provider == "groq"
    assert any("API key not valid" in e for e in usage.errors)


def test_transient_errors_do_not_disable_provider_after_one_bad_call():
    """一次 503 不代表整場都不能用：下一批還會再試 Gemini。"""
    g = FakeProvider("gemini", [LLMError("503"), LLMError("503"), OK])
    q = FakeProvider("groq", [OK])
    router, sleeps = make_router(g, q)
    assert run(router).answer == 42  # 這次由 Groq 接手
    assert sleeps == [20]  # 退避重試一次
    assert run(router).answer == 42  # 下一批又回到 Gemini
    assert (g.calls, q.calls) == (3, 1)


def test_two_failed_calls_in_a_row_disable_provider():
    g = FakeProvider("gemini", [LLMError("x")] * 4)
    q = FakeProvider("groq", [OK, OK, OK])
    router, _ = make_router(g, q)
    run(router)
    run(router)  # Gemini 連續第 2 次呼叫失敗 → 停用
    run(router)
    assert (g.calls, q.calls) == (4, 3)


def test_request_too_large_switches_without_retry_or_disable():
    g = FakeProvider("groq", [RequestTooLarge("413"), OK])
    a = FakeProvider("anthropic", [OK])
    router, sleeps = make_router(g, a)
    assert run(router).answer == 42
    assert (g.calls, a.calls) == (1, 1) and sleeps == []
    assert run(router).answer == 42  # Groq 沒有被停用
    assert g.calls == 2


def test_invalid_schema_retries_once_then_next_provider():
    """驗收：LLM 回傳 JSON 驗證失敗就重試一次，再失敗換下一個。"""
    g = FakeProvider("gemini", [{"answer": "not a number"}, {"wrong": 1}])
    q = FakeProvider("groq", [OK])
    router, sleeps = make_router(g, q)
    assert run(router).answer == 42
    assert (g.calls, q.calls) == (2, 1)
    assert sleeps == []  # 格式錯誤不用等，直接重試


def test_invalid_output_then_recovers_on_retry():
    g = FakeProvider("gemini", [InvalidOutput("finishReason=MAX_TOKENS"), OK])
    router, _ = make_router(g)
    assert run(router).answer == 42


def test_all_providers_fail_raises_and_marks_degraded():
    g = FakeProvider("gemini", [LLMError("x")] * 4)
    q = FakeProvider("groq", [FatalProviderError("bad key")])
    router, _ = make_router(g, q)
    with pytest.raises(AllProvidersFailed):
        run(router)
    assert router.usage().degraded is True
    with pytest.raises(AllProvidersFailed):
        run(router)
    assert router.available is False  # Gemini 連兩次失敗、Groq key 錯誤
    with pytest.raises(AllProvidersFailed):  # 之後的呼叫立刻失敗，不再嘗試
        run(router)
    assert g.calls == 4 and q.calls == 1


def test_batch_limit_and_output_cap_follow_current_provider():
    g = FakeProvider("gemini", [FatalProviderError("bad key")])
    q = FakeProvider("groq", [OK])
    q.max_batch_items, q.max_output_tokens = 8, 3000
    router, _ = make_router(g, q)
    assert router.batch_limit(25) == 25  # 目前會先用 Gemini（沒有限制）
    run(router)  # Gemini 停用
    assert router.batch_limit(25) == 8
    assert q.last_max_output_tokens == 100  # 呼叫端要的比上限小，就用呼叫端的
    router2, _ = make_router(q2 := FakeProvider("groq", [OK]))
    q2.max_output_tokens = 3000
    router2.run("t", "s", "u", Out, SCHEMA, max_output_tokens=8192)
    assert q2.last_max_output_tokens == 3000


def test_build_provider_skips_missing_key(monkeypatch):
    cfg = ProviderSettings(name="gemini", model="m", api_key_env="TEST_GEMINI_KEY")
    monkeypatch.delenv("TEST_GEMINI_KEY", raising=False)
    assert build_provider(cfg, timeout=10) is None
    monkeypatch.setenv("TEST_GEMINI_KEY", "  ")
    assert build_provider(cfg, timeout=10) is None
    monkeypatch.setenv("TEST_GEMINI_KEY", "abc")
    assert build_provider(cfg, timeout=10).name == "gemini"
    disabled = cfg.model_copy(update={"enabled": False})
    assert build_provider(disabled, timeout=10) is None
