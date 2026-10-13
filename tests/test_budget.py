"""T-BUDGET: 예산을 넘을 호출은 시작하지 않는다."""
import pytest

from chimera.budget import Budget, BudgetExceeded
from chimera.config import Limits, ModelCfg
from chimera.db import connect
from chimera.enums import CallRole, StopReason

M = ModelCfg("m", "mock", "mock-target", 1.0, 5.0, max_tokens=1000)  # 출력 상한 예약 $0.005


def budget(run=0.02, day=1.0, calls=100, run_id="r"):
    conn = connect(":memory:")
    return Budget(conn, run_id, Limits(run, day, 10, calls, 10, 3))


def test_reserve_refuses_before_exceeding_run_cap():
    b = budget(run=0.012)
    b.reserve(CallRole.TARGET, M, 1000)  # 0.001 + 0.005
    b.reserve(CallRole.TARGET, M, 1000)  # 누적 0.012
    with pytest.raises(BudgetExceeded) as e:
        b.reserve(CallRole.TARGET, M, 1000)
    assert e.value.reason == StopReason.BUDGET_EXHAUSTED


def test_settle_frees_unused_reservation():
    b = budget(run=0.012)
    rid, _ = b.reserve(CallRole.TARGET, M, 1000)
    b.settle(rid, CallRole.TARGET, 0.0001)
    b.reserve(CallRole.TARGET, M, 1000)
    b.reserve(CallRole.TARGET, M, 0)  # 남은 여유 안에서는 허용
    assert b.run_committed() <= 0.012 + 1e-9


def test_day_cap_spans_runs():
    b1 = budget(run=1.0, day=0.01)
    b1.reserve(CallRole.TARGET, M, 1000)
    b2 = Budget(b1.conn, "other", b1.limits)
    with pytest.raises(BudgetExceeded):
        b2.reserve(CallRole.TARGET, M, 1000)


def test_call_count_cap():
    b = budget(run=1.0, calls=2)
    b.reserve(CallRole.TARGET, M, 0)
    b.reserve(CallRole.TARGET, M, 0)
    with pytest.raises(BudgetExceeded):
        b.reserve(CallRole.TARGET, M, 0)


def test_open_reservations_settled_conservatively():
    b = budget(run=1.0)
    _, amt = b.reserve(CallRole.TARGET, M, 1000)
    assert b.settle_open_conservatively() == 1
    assert b.run_settled() == pytest.approx(amt)
    assert b.settle_open_conservatively() == 0


def test_cache_read_price_override():
    m = ModelCfg("o", "anthropic", "x", 4.0, 20.0, 100, cache_read_usd_per_mtok=0.2)
    assert m.cost(0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.2)
    m2 = ModelCfg("s", "anthropic", "x", 2.0, 10.0, 100)
    assert m2.cost(0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.2)
