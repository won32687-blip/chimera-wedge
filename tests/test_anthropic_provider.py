"""Claude Provider의 오류 분류와 요청 형태 (네트워크 없이 가짜 클라이언트로)."""
from types import SimpleNamespace

import anthropic
try:
    import httpx2 as httpx  # anthropic 1.x
except ImportError:  # pragma: no cover
    import httpx
import pytest

from chimera.config import ModelCfg
from chimera.providers.anthropic_provider import AnthropicProvider
from chimera.providers.base import ErrorKind, ProviderError

M = ModelCfg("s", "anthropic", "claude-sonnet-5-5", 2.0, 10.0, 100, effort="low")
REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def err(cls, status, headers=None):
    return cls("x", response=httpx.Response(status, request=REQ, headers=headers or {}), body=None)


class FakeClient:
    def __init__(self, outcome):
        self.outcome, self.kwargs = outcome, None
        self.messages = self

    def create(self, **kw):
        self.kwargs = kw
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def provider(outcome, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    p = AnthropicProvider()
    fake = FakeClient(outcome)
    p._client = lambda timeout: fake
    return p, fake


@pytest.mark.parametrize("exc,kind", [
    (err(anthropic.RateLimitError, 429, {"retry-after": "7"}), ErrorKind.RETRYABLE),
    (err(anthropic.InternalServerError, 500), ErrorKind.RETRYABLE),
    (err(anthropic.AuthenticationError, 401), ErrorKind.BLOCKED),
    (err(anthropic.NotFoundError, 404), ErrorKind.BLOCKED),
    (err(anthropic.BadRequestError, 400), ErrorKind.FATAL),
    (anthropic.APITimeoutError(REQ), ErrorKind.RETRYABLE),
])
def test_error_mapping(exc, kind, monkeypatch):
    p, _ = provider(exc, monkeypatch)
    with pytest.raises(ProviderError) as e:
        p.complete(M, "sys", "hi")
    assert e.value.kind == kind
    if kind == ErrorKind.RETRYABLE and getattr(exc, "status_code", None) == 429:
        assert e.value.retry_after == 7.0


def test_request_shape_and_usage(monkeypatch):
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="답")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=3,
                              cache_read_input_tokens=2))
    p, fake = provider(resp, monkeypatch)
    r = p.complete(M, "sys", "hi", cache_system=True)
    assert r.text == "답" and (r.input_tokens, r.output_tokens, r.cache_write_tokens, r.cache_read_tokens) == (10, 5, 3, 2)
    assert fake.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert fake.kwargs["output_config"] == {"effort": "low"}
    assert "fallbacks" not in fake.kwargs and "thinking" not in fake.kwargs


def test_missing_key_blocks(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        AnthropicProvider().complete(M, "s", "u")
    assert e.value.kind == ErrorKind.BLOCKED and "ANTHROPIC_API_KEY" in str(e.value)
