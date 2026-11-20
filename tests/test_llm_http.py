"""Gemini／Groq 的 HTTP 回應解析（用 httpx.MockTransport，不連網路）。"""

import json

import httpx
import pytest

from pipeline.llm.base import FatalProviderError, InvalidOutput, RateLimited
from pipeline.llm.gemini import GeminiProvider
from pipeline.llm.groq import GroqProvider, _parse_reset


def _client(status: int, body: dict, headers: dict | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _gemini(status, body, **kw):
    return GeminiProvider(
        "k",
        "gemini-test",
        options={"thinking_level": "minimal"},
        client=_client(status, body, **kw),
    )


def test_gemini_success_parses_json_and_usage():
    seen: list = []
    body = {
        "candidates": [{"content": {"parts": [{"text": '{"answer": 1}'}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 83,
            "candidatesTokenCount": 21,
            "thoughtsTokenCount": 5,
        },
    }
    c = _gemini(200, body, seen=seen).complete_json(
        "sys", "user", {"type": "object"}, max_output_tokens=99
    )
    assert c.data == {"answer": 1}
    assert (c.input_tokens, c.output_tokens) == (83, 26)
    gen = seen[0]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseJsonSchema"] == {"type": "object"}
    assert gen["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert gen["maxOutputTokens"] == 99
    assert seen[0]["systemInstruction"]["parts"][0]["text"] == "sys"


def test_gemini_429_reads_retry_delay():
    body = {"error": {"code": 429, "details": [{"@type": "RetryInfo", "retryDelay": "37s"}]}}
    with pytest.raises(RateLimited) as e:
        _gemini(429, body).complete_json("s", "u", {}, max_output_tokens=10)
    assert e.value.retry_after == 37


def test_gemini_bad_key_is_fatal():
    body = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}
    with pytest.raises(FatalProviderError, match="API key not valid"):
        _gemini(400, body).complete_json("s", "u", {}, max_output_tokens=10)


def test_gemini_truncated_output_is_invalid():
    body = {
        "candidates": [{"content": {"parts": [{"text": '{"a": '}]}, "finishReason": "MAX_TOKENS"}]
    }
    with pytest.raises(InvalidOutput, match="MAX_TOKENS"):
        _gemini(200, body).complete_json("s", "u", {}, max_output_tokens=10)


def test_groq_success_and_request_shape():
    seen: list = []
    body = {
        "choices": [{"message": {"content": '{"answer": 2}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 254, "completion_tokens": 251},
    }
    p = GroqProvider(
        "k",
        "openai/gpt-oss-120b",
        options={"reasoning_effort": "low"},
        client=_client(200, body, seen=seen),
    )
    c = p.complete_json("sys", "user", {"type": "object"}, max_output_tokens=50)
    assert c.data == {"answer": 2} and (c.input_tokens, c.output_tokens) == (254, 251)
    rf = seen[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert seen[0]["reasoning_effort"] == "low"


def test_groq_429_uses_retry_after_header():
    p = GroqProvider("k", "m", client=_client(429, {}, headers={"retry-after": "12"}))
    with pytest.raises(RateLimited) as e:
        p.complete_json("s", "u", {}, max_output_tokens=10)
    assert e.value.retry_after == 12


def test_groq_reset_format():
    assert _parse_reset("1m26.4s") == pytest.approx(86.4)
    assert _parse_reset("5.497s") == pytest.approx(5.497)
    assert _parse_reset("7") == 7
    assert _parse_reset(None) is None
