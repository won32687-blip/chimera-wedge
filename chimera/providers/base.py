"""Provider 인터페이스. 특정 모델 회사에 묶이지 않도록 모든 호출은 이 계약만 쓴다."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..config import ModelCfg


class ErrorKind(str, Enum):
    RETRYABLE = "RETRYABLE"    # 429, 5xx, 연결 오류, 시간 초과
    BLOCKED = "BLOCKED"        # 401/403/404: 키·권한·모델 문제. 재시도 없이 중단
    FATAL = "FATAL"            # 400 등 요청 자체 오류


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


@dataclass
class CallResult:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


class Provider(Protocol):
    def complete(self, model: ModelCfg, system: str, user: str, *,
                 cache_system: bool = False) -> CallResult: ...


def get_provider(name: str, registry: dict | None = None) -> Provider:
    """registry로 미리 만든 Provider를 주입할 수 있다 (테스트용)."""
    if registry and name in registry:
        return registry[name]
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if name == "mock":
        from .mock import MockProvider
        return MockProvider()
    raise ValueError(f"알 수 없는 provider: {name}")
