"""EXECUTOR: 모든 모델 호출의 유일한 통로 (명세서 9·10·11장).

- 허용 대상 검사(guard), 예산 예약 → 호출 → 정산, 재시도(429·5xx·연결 오류만), 중단 신호 확인.
- 대상 모델 호출은 attempt 행으로 기록한다. 호출 전에 NOT_EXECUTED로 먼저 남겨 중단 시에도 흔적이 남는다.
- 실행 환경 오류(ERROR)는 대상의 실패가 아니다. FAIL 판정은 VERIFIER만 한다.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .budget import Budget, BudgetExceeded, estimate_input_tokens
from .config import Config, ModelCfg, TargetSpec
from .db import Tx, event, new_id, now
from .enums import CallRole, ExecutionStatus, StopReason
from .providers import CallResult, ErrorKind, ProviderError, get_provider
from .stopper import Stopper


class Blocked(Exception):
    """실행 자체가 불가능 (허용 범위 밖, 인증 실패 등). run_state=BLOCKED."""

    def __init__(self, reason: StopReason, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class StopRequested(Exception):
    pass


class CallFailed(Exception):
    """재시도를 다 썼거나 재시도 대상이 아닌 오류. 대상 실패가 아니다."""


@dataclass
class CallOutcome:
    result: CallResult
    cost: float


def guard_target(cfg: Config, spec: TargetSpec) -> None:
    if spec.target_id not in cfg.allowed_targets:
        raise Blocked(StopReason.NOT_ALLOWED,
                      f"'{spec.target_id}'는 config.toml [allowed] targets에 없습니다. 허용 목록에 있는 대상만 실행합니다")


class Executor:
    def __init__(self, conn, cfg: Config, spec: TargetSpec, run_id: str, budget: Budget,
                 stopper: Stopper, providers: dict | None = None, sleep=time.sleep):
        guard_target(cfg, spec)
        self.conn, self.cfg, self.spec, self.run_id = conn, cfg, spec, run_id
        self.budget, self.stopper, self.sleep = budget, stopper, sleep
        self.providers = dict(providers or {})
        self.target_model = cfg.model(spec.model_key)

    def _provider(self, model: ModelCfg):
        if model.provider not in self.providers:
            self.providers[model.provider] = get_provider(model.provider)
        return self.providers[model.provider]

    def _delay(self, attempt_no: int, retry_after: float | None) -> float:
        lp = self.cfg.loop
        if retry_after is not None:
            return min(retry_after, lp.retry_max_delay_s)
        base = lp.retry_base_delay_s
        return min(base * 2 ** (attempt_no - 1) + random.uniform(0, base), lp.retry_max_delay_s)

    # ---------- WEDGE·채점기 호출 ----------
    def call(self, role: CallRole, model: ModelCfg, system: str, user: str,
             cache_system: bool = False) -> CallOutcome:
        provider = self._provider(model)
        est = estimate_input_tokens(system, user)
        last: ProviderError | None = None
        for attempt_no in range(1, self.cfg.loop.max_retries + 2):
            if self.stopper.requested():
                raise StopRequested()
            rid, _ = self.budget.reserve(role, model, est)
            try:
                res = provider.complete(model, system, user, cache_system=cache_system)
            except ProviderError as e:
                self.budget.settle(rid, role, 0.0)
                event(self.conn, self.run_id, "call", role.value, "error", None, e.kind.value,
                      {"status": e.status, "message": str(e), "attempt_no": attempt_no})
                if e.kind == ErrorKind.BLOCKED:
                    raise Blocked(StopReason.PROVIDER_BLOCKED, f"{role} 모델 호출 차단: {e}") from e
                if e.kind == ErrorKind.FATAL:
                    raise CallFailed(f"{role} 호출 실패: {e}") from e
                last = e
                if attempt_no <= self.cfg.loop.max_retries:
                    self.sleep(self._delay(attempt_no, e.retry_after))
                continue
            cost = model.cost(res.input_tokens, res.output_tokens, res.cache_write_tokens, res.cache_read_tokens)
            self.budget.settle(rid, role, cost)
            return CallOutcome(res, cost)
        raise CallFailed(f"{role} 호출 재시도 소진: {last}")

    # ---------- 대상 호출 (attempt 기록) ----------
    def run_test(self, test_id: str, input_text: str) -> dict:
        """테스트 1개를 실행하고 마지막 attempt 행을 dict로 돌려준다."""
        model = self.target_model
        provider = self._provider(model)
        est = estimate_input_tokens(self.spec.prompt_text, input_text)
        last_row = None
        for attempt_no in range(1, self.cfg.loop.max_retries + 2):
            attempt_id = new_id("att")
            if self.stopper.requested():
                self._cancel(test_id, attempt_no, "중단 요청")
                raise StopRequested()
            try:
                rid, reserved = self.budget.reserve(CallRole.TARGET, model, est, attempt_id)
            except BudgetExceeded:
                self._cancel(test_id, attempt_no, "예산 상한")
                raise
            with Tx(self.conn) as c:
                c.execute("INSERT INTO attempts(attempt_id, test_id, idempotency_key, attempt_no, target_spec_hash,"
                          " execution_status, reserved_usd, started_at) VALUES (?,?,?,?,?,?,?,?)",
                          (attempt_id, test_id, f"{test_id}#{attempt_no}", attempt_no, self.spec.spec_hash,
                           ExecutionStatus.NOT_EXECUTED.value, reserved, now()))
            try:
                res = provider.complete(model, self.spec.prompt_text, input_text)
            except ProviderError as e:
                self.budget.settle(rid, CallRole.TARGET, 0.0, attempt_id)
                status = ExecutionStatus.BLOCKED if e.kind == ErrorKind.BLOCKED else ExecutionStatus.ERROR
                self.conn.execute("UPDATE attempts SET execution_status=?, error_type=?, error_detail=?, cost_usd=0,"
                                  " finished_at=? WHERE attempt_id=?",
                                  (status.value, e.kind.value, f"{e} (status={e.status})", now(), attempt_id))
                event(self.conn, self.run_id, "attempt", attempt_id, "execution_status",
                      "NOT_EXECUTED", status.value, str(e))
                last_row = self._row(attempt_id)
                if e.kind == ErrorKind.BLOCKED:
                    raise Blocked(StopReason.PROVIDER_BLOCKED, f"대상 모델 호출 차단: {e}") from e
                if e.kind == ErrorKind.FATAL:
                    return last_row
                if attempt_no <= self.cfg.loop.max_retries:
                    self.sleep(self._delay(attempt_no, e.retry_after))
                continue
            cost = model.cost(res.input_tokens, res.output_tokens, res.cache_write_tokens, res.cache_read_tokens)
            self.budget.settle(rid, CallRole.TARGET, cost, attempt_id)
            truncated = res.stop_reason == "max_tokens"
            self.conn.execute(
                "UPDATE attempts SET execution_status=?, response_text=?, stop_reason=?, error_detail=?,"
                " input_tokens=?, output_tokens=?, cost_usd=?, finished_at=? WHERE attempt_id=?",
                (ExecutionStatus.EXECUTED.value, res.text, res.stop_reason,
                 "truncated: max_tokens" if truncated else None,
                 res.input_tokens, res.output_tokens, cost, now(), attempt_id))
            return self._row(attempt_id)
        return last_row

    def _cancel(self, test_id: str, attempt_no: int, why: str) -> None:
        aid = new_id("att")
        self.conn.execute("INSERT INTO attempts(attempt_id, test_id, idempotency_key, attempt_no, target_spec_hash,"
                          " execution_status, error_type, error_detail, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (aid, test_id, f"{test_id}#{attempt_no}", attempt_no, self.spec.spec_hash,
                           ExecutionStatus.CANCELLED.value, "CANCELLED", why, now()))

    def _row(self, attempt_id: str) -> dict:
        return dict(self.conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone())
