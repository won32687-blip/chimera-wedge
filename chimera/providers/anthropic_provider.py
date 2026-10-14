"""Claude API Provider. 키는 ANTHROPIC_API_KEY에서만 읽고(프로젝트 .env → config.load_env_file이 넣어 줌),
로그·DB에 남기지 않는다.

- SDK 자체 재시도는 끈다(max_retries=0). 재시도·비용 정산은 executor가 직접 한다.
- 서버 측 fallbacks(다른 모델로 대신 응답)는 쓰지 않는다. 시험 대상 모델이 바뀌면 판정이 무의미해지기 때문.
"""
from __future__ import annotations

from ..config import ModelCfg
from .base import CallResult, ErrorKind, ProviderError


class AnthropicProvider:
    def __init__(self):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ProviderError(ErrorKind.BLOCKED,
                                "anthropic 패키지가 없습니다. `pip install -e .`를 실행하세요") from e
        self._clients = {}

    def _client(self, timeout: float):
        import anthropic
        if timeout not in self._clients:
            import os
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ProviderError(ErrorKind.BLOCKED, "ANTHROPIC_API_KEY가 없습니다. "
                                    "`python -m chimera set-key`로 프로젝트 .env에 저장하세요")
            self._clients[timeout] = anthropic.Anthropic(max_retries=0, timeout=timeout)
        return self._clients[timeout]

    def complete(self, model: ModelCfg, system: str, user: str, *, cache_system: bool = False) -> CallResult:
        import anthropic
        system_param = [{"type": "text", "text": system}]
        if cache_system:
            system_param[0]["cache_control"] = {"type": "ephemeral"}
        kwargs = dict(model=model.model_id, max_tokens=model.max_tokens, system=system_param,
                      messages=[{"role": "user", "content": user}])
        if model.effort:
            kwargs["output_config"] = {"effort": model.effort}
        try:
            resp = self._client(model.timeout_s).messages.create(**kwargs)
        except anthropic.APIConnectionError as e:  # APITimeoutError 포함
            raise ProviderError(ErrorKind.RETRYABLE, f"연결 오류: {type(e).__name__}") from e
        except anthropic.RateLimitError as e:
            ra = e.response.headers.get("retry-after") if e.response is not None else None
            raise ProviderError(ErrorKind.RETRYABLE, "요청 한도(429)", 429,
                                float(ra) if ra and ra.replace(".", "", 1).isdigit() else None) from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            raise ProviderError(ErrorKind.BLOCKED, f"인증·권한·모델 오류({e.status_code})", e.status_code) from e
        except anthropic.APIStatusError as e:
            kind = ErrorKind.RETRYABLE if e.status_code >= 500 else ErrorKind.FATAL
            raise ProviderError(kind, f"API 오류({e.status_code})", e.status_code) from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        u = resp.usage
        return CallResult(
            text=text, input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
            stop_reason=resp.stop_reason,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
