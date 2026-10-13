"""T-LOOP · T-GUARD · T-STOP · T-RESUME · T-ERRORS · T-REGRESS: Mock으로 전체 루프를 돌린다."""
import json

import pytest

from chimera.budget import Budget
from chimera.controller import Controller, cleanup_dangling, create_run, regression_outcome
from chimera.enums import RegressionOutcome, RunMode
from chimera.executor import Blocked
from chimera.providers.base import ErrorKind, ProviderError
from chimera.providers.mock import MockProvider


def explore(w):
    ctl = w.controller()
    return ctl.run_id, ctl.run()


def test_full_loop_plans_executes_judges_verifies_records(world):  # T-LOOP
    w = world()
    run_id, (state, reason, _) = explore(w)
    assert (state, reason) == ("STOPPED", "NO_TARGETS_LEFT")
    # 모든 테스트가 실행·판정 기록을 가진다
    assert w.v("SELECT COUNT(*) FROM tests t WHERE NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.test_id=t.test_id)") == 0
    assert w.v("SELECT COUNT(*) FROM attempts WHERE execution_status<>'EXECUTED'") == 0
    # 심어 둔 결함 2개가 재현 확인되어 회귀 세트에 들어간다
    rows = w.q("SELECT invariant_id, verification_status, in_regression_set, repro_fail FROM findings ORDER BY invariant_id")
    assert [(r[0], r[1], r[2]) for r in rows] == [("INV-FORMAT", "VERIFIED", 1), ("INV-ROLE", "VERIFIED", 1)]
    # 같은 실패의 반복 관찰은 고유 실패 수를 늘리지 않는다
    assert w.v("SELECT COUNT(*) FROM findings") == 2
    assert w.v("SELECT SUM(observations) FROM findings") > 2
    # 모든 단계가 순서대로 기록되고 다음 행동이 결정된다
    states = [r[0] for r in w.q("SELECT wedge_state FROM steps WHERE run_id=? ORDER BY seq", (run_id,))]
    for s in ("ACQUIRE", "PROBE", "FOCUS", "VERIFY", "DEEPEN", "NEXT_INVARIANT"):
        assert s in states
    assert (w.cfg.data_dir / "runs" / run_id / "report.md").exists()


def test_verifier_never_sees_wedge_rationale(world):
    w = world()
    explore(w)
    judge_inputs = [u for mid, u in w.mock.calls if mid == "mock-judge"]
    assert judge_inputs and all("mock 규칙" not in u and "hypothesis" not in u for u in judge_inputs)


def test_target_not_in_allowed_list_is_refused(world):  # T-GUARD
    w = world(edit=lambda s: s.replace('targets = ["mock_demo"]', 'targets = ["other"]'))
    with pytest.raises(Blocked) as e:
        w.controller()
    assert e.value.reason == "NOT_ALLOWED"
    assert w.mock.calls == []


def test_budget_cap_stops_without_overspend(world):  # T-BUDGET (루프)
    w = world(edit=lambda s: s.replace("max_cost_usd_per_run = 1.0", "max_cost_usd_per_run = 0.06"))
    run_id, (state, reason, _) = explore(w)
    assert (state, reason) == ("STOPPED", "BUDGET_EXHAUSTED")
    assert Budget(w.conn, run_id, w.cfg.limits).run_settled() <= 0.06 + 1e-9


def test_max_tests_cap(world):
    w = world(edit=lambda s: s.replace("max_tests_per_run = 40", "max_tests_per_run = 3"))
    _, (state, reason, _) = explore(w)
    assert reason == "MAX_TESTS"
    assert w.v("SELECT COUNT(*) FROM attempts WHERE execution_status='EXECUTED'") == 3


class StopAfter(MockProvider):
    """대상 호출 n번 뒤 `chimera stop`과 같은 중단 요청을 DB에 남긴다."""

    def __init__(self, conn, n):
        super().__init__()
        self.conn, self.n = conn, n

    def complete(self, model, system, user, *, cache_system=False):
        res = super().complete(model, system, user, cache_system=cache_system)
        if model.model_id == "mock-target":
            self.n -= 1
            if self.n == 0:
                self.conn.execute("UPDATE runs SET stop_requested=1")
        return res


def test_stop_then_resume_finishes_without_duplicates(world):  # T-STOP · T-RESUME
    w = world()
    w.mock = StopAfter(w.conn, 4)
    ctl = w.controller()
    state, reason, _ = ctl.run()
    assert (state, reason) == ("STOPPED", "USER_STOP")
    tests_before = w.v("SELECT COUNT(*) FROM tests")
    assert w.v("SELECT COUNT(*) FROM attempts WHERE execution_status='EXECUTED'") == 4
    # 재개
    w.mock = MockProvider()
    cleanup_dangling(w.conn, ctl.run_id, Budget(w.conn, ctl.run_id, w.cfg.limits))
    state, reason, _ = w.controller(ctl.run_id).run()
    assert reason == "NO_TARGETS_LEFT"
    assert w.v("SELECT COUNT(*) FROM tests") > tests_before
    keys = [r[0] for r in w.q("SELECT idempotency_key FROM attempts")]
    assert len(keys) == len(set(keys))
    assert w.v("SELECT COUNT(*) FROM findings WHERE verification_status='VERIFIED'") == 2


def test_stop_file(world):
    w = world()
    ctl = w.controller()
    # run()은 시작 시 STOP 파일을 지우므로, 시작 직후 다시 만든다
    orig = ctl._turn

    def turn():
        (ctl.dir / "STOP").write_text("x")
        orig()
    ctl._turn = turn
    assert ctl.run()[1] == "USER_STOP"


def test_crash_leftovers_are_cleaned(world):  # T-RESUME (강제 종료)
    w = world()
    run_id = create_run(w.conn, w.cfg, w.spec, RunMode.EXPLORE)
    w.conn.execute("INSERT INTO tests(test_id,run_id,invariant_id,purpose,input_text,oracle_id,test_set,created_at)"
                   " VALUES ('tX',?,'INV-ROLE','PROBE','a','O-ROLE','EXPLORATORY','x')", (run_id,))
    w.conn.execute("INSERT INTO attempts(attempt_id,test_id,idempotency_key,attempt_no,target_spec_hash,execution_status)"
                   " VALUES ('aX','tX','tX#1',1,'h','NOT_EXECUTED')")
    b = Budget(w.conn, run_id, w.cfg.limits)
    from chimera.enums import CallRole
    b.reserve(CallRole.TARGET, w.cfg.model("mock-target"), 100, "aX")
    info = cleanup_dangling(w.conn, run_id, b)
    assert info["interrupted"] == 1 and info["settled_reservations"] == 1
    assert w.v("SELECT execution_status FROM attempts WHERE attempt_id='aX'") == "ERROR"
    assert w.v("SELECT evaluation_status FROM evaluations WHERE test_id='tX'") == "NOT_RUN"


def test_retryable_error_is_retried_with_backoff(world):  # T-ERRORS
    w = world({"mock-target": [ProviderError(ErrorKind.RETRYABLE, "429", 429, retry_after=0.02),
                               ProviderError(ErrorKind.RETRYABLE, "529", 529)]})
    run_id, (state, reason, _) = explore(w)
    assert reason == "NO_TARGETS_LEFT"
    assert w.sleeps[0] == pytest.approx(0.02)  # Retry-After 존중
    assert len(w.sleeps) == 2
    assert w.v("SELECT COUNT(*) FROM attempts WHERE execution_status='ERROR'") == 2


def test_auth_error_blocks_run(world):
    w = world({"mock-attacker": [ProviderError(ErrorKind.BLOCKED, "401", 401)]})
    _, (state, reason, _) = explore(w)
    assert (state, reason) == ("BLOCKED", "PROVIDER_BLOCKED")
    assert w.mock.calls and all(m == "mock-attacker" for m, _ in w.mock.calls)


def test_persistent_target_errors_stop_run(world):
    errs = [ProviderError(ErrorKind.RETRYABLE, "500", 500)] * 40
    w = world({"mock-target": errs})
    _, (state, reason, _) = explore(w)
    assert reason == "CONSECUTIVE_ERRORS"
    # 실행 오류는 대상의 FAIL이 아니다
    assert w.v("SELECT COUNT(*) FROM evaluations WHERE evaluation_status='FAIL'") == 0
    assert w.v("SELECT COUNT(*) FROM findings") == 0


def test_attacker_garbage_stops_after_three_turns(world):
    w = world({"mock-attacker": ["농담입니다"] * 10})
    _, (state, reason, _) = explore(w)
    assert reason == "ATTACKER_ERROR"
    assert w.v("SELECT COUNT(*) FROM tests") == 0


def test_attacker_disallowed_action_is_rejected_then_recovers(world):
    bad = json.dumps({"proposed_action": "VERIFY", "tests": []})
    w = world({"mock-attacker": [bad]})
    _, (state, reason, _) = explore(w)
    assert reason == "NO_TARGETS_LEFT"
    assert w.v("SELECT COUNT(*) FROM steps WHERE applied_action='VERIFY' AND wedge_state='PROBE'") == 0


def test_regression_mode(world):  # T-REGRESS
    w = world()
    explore(w)
    ctl = w.controller(mode=RunMode.REGRESS)
    state, reason, _ = ctl.run()
    assert reason == "COMPLETED"
    outs = [r[0] for r in w.q("SELECT new_value FROM events WHERE entity='regression' AND run_id=?", (ctl.run_id,))]
    assert outs == ["STILL_FAILING", "STILL_FAILING"]
    assert w.v("SELECT COUNT(*) FROM tests WHERE run_id=? AND test_set='REGRESSION'", (ctl.run_id,)) == 6


def test_regression_outcome_rules():
    assert regression_outcome(["PASS", "FAIL", "PASS"]) == RegressionOutcome.STILL_FAILING
    assert regression_outcome(["PASS", "PASS", "PASS"]) == RegressionOutcome.NOT_REPRODUCED
    assert regression_outcome(["NOT_RUN", "NOT_RUN"]) == RegressionOutcome.NOT_EXECUTED
    assert regression_outcome(["PASS", "INCONCLUSIVE"]) == RegressionOutcome.INCONCLUSIVE


def test_second_run_refused_while_one_running(world):
    w = world()
    rid = create_run(w.conn, w.cfg, w.spec, RunMode.EXPLORE)
    w.conn.execute("UPDATE runs SET run_state='RUNNING' WHERE run_id=?", (rid,))
    with pytest.raises(Blocked):
        create_run(w.conn, w.cfg, w.spec, RunMode.EXPLORE)
