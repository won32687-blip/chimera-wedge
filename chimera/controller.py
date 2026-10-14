"""루프 제어 (명세서 8·9장): 코드가 allowed_actions를 계산하고, WEDGE가 그중 하나를 고르며,
예산·중단·횟수 상한은 언제나 코드가 강제한다.

한 턴 = 단계 1개 처리. 턴이 끝날 때마다 cursor(진행 위치)를 DB에 저장하므로 어느 시점에 멈춰도 resume으로 이어진다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import codex
from .budget import Budget, BudgetExceeded
from .config import Config, TargetSpec
from .db import Tx, event, new_id, now
from .enums import (
    CallRole, EvaluationStatus, ExecutionStatus, FindingTestRole, NextAction, RegressionOutcome,
    RunMode, RunState, StopReason, TestPurpose, TestSet, VerificationStatus, WedgeState,
)
from .executor import Blocked, CallFailed, Executor, StopRequested
from .stopper import Stopper
from .verifier import Verdict, Verifier
from .wedge import Proposal, ProposalError, build_user, load_judge_system, load_system, parse_proposal

S, A = WedgeState, NextAction


class _Stop(Exception):
    def __init__(self, reason: StopReason, detail: str = ""):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def initial_cursor() -> dict:
    return {"state": S.ACQUIRE.value, "invariant_id": None, "family": None, "families_tried": {},
            "probes": 0, "stale": 0, "finding_id": None, "focus_test_id": None, "deepen": {},
            "invalid_streak": 0, "done_invariants": [], "exploit_tests": 0, "tests": 0, "seq": 0,
            "consecutive_errors": 0, "attacker_failures": 0, "elapsed_s": 0.0, "regress_done": []}


def run_dir(cfg: Config, run_id: str) -> Path:
    d = cfg.data_dir / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def continuation_plan(conn, cfg: Config, spec: TargetSpec, run_id: str = "-") -> dict | None:
    """이전 탐색 실행(같은 대상·같은 명세 해시)의 기록으로 이어하기 계획을 만든다. 기록이 없으면 None.

    이전 실행의 테스트·판정·finding은 읽기만 하고 바꾸지 않는다.
    """
    runs = codex.prior_runs(conn, spec, run_id)
    if not runs:
        return None
    return codex.build_plan(spec, codex.prior_coverage(conn, spec, run_id), cfg.loop.cover_min_tests, runs)


def create_run(conn, cfg: Config, spec: TargetSpec, mode: RunMode, fresh: bool = False) -> str:
    """새 실행 등록. 같은 DB에 RUNNING 실행이 있으면 거부한다 (이중 실행 방지).

    탐색 실행은 기본으로 이전 기록을 이어받는다(cursor['plan']). fresh=True면 처음부터 한다.
    """
    busy = conn.execute("SELECT run_id FROM runs WHERE run_state IN ('RUNNING','STOPPING')").fetchone()
    if busy:
        raise Blocked(StopReason.NOT_ALLOWED,
                      f"이미 실행 중인 run이 있습니다: {busy[0]}. `chimera resume {busy[0]}` 또는 "
                      f"`chimera stop {busy[0]}`를 먼저 쓰세요")
    run_id = new_id("run")
    cursor = initial_cursor()
    if mode == RunMode.EXPLORE and not fresh:
        cursor["plan"] = continuation_plan(conn, cfg, spec, run_id)
    with Tx(conn) as c:
        c.execute("INSERT OR IGNORE INTO targets(target_id, spec_hash, prompt_hash, model_key, spec_json, created_at)"
                  " VALUES (?,?,?,?,?,?)",
                  (spec.target_id, spec.spec_hash, spec.prompt_hash, spec.model_key,
                   json.dumps(spec.raw, ensure_ascii=False), now()))
        for inv in spec.invariants:
            c.execute("INSERT OR IGNORE INTO invariants(invariant_id, target_id, spec_hash, description, oracle_id, source)"
                      " VALUES (?,?,?,?,?,?)",
                      (inv.id, spec.target_id, spec.spec_hash, inv.description, inv.oracle_id, inv.source.value))
        c.execute("INSERT INTO runs(run_id, target_id, spec_hash, mode, run_state, limits_json, cursor_json,"
                  " wedge_model, judge_model) VALUES (?,?,?,?,?,?,?,?,?)",
                  (run_id, spec.target_id, spec.spec_hash, mode.value, RunState.CREATED.value,
                   json.dumps(cfg.snapshot(), ensure_ascii=False), json.dumps(cursor),
                   cfg.attacker_model, cfg.judge_model))
    return run_id


def cleanup_dangling(conn, run_id: str, budget: Budget) -> dict:
    """중단·강제 종료 뒤 정리. 자동 재실행은 하지 않는다 (명세서 11장)."""
    n_int = conn.execute(
        "UPDATE attempts SET execution_status='ERROR', error_type='INTERRUPTED', error_detail='중단으로 응답 미수신',"
        " finished_at=? WHERE execution_status='NOT_EXECUTED' AND finished_at IS NULL AND test_id IN "
        "(SELECT test_id FROM tests WHERE run_id=?)", (now(), run_id)).rowcount
    n_res = budget.settle_open_conservatively()
    n_cancel = 0
    for (tid,) in conn.execute("SELECT test_id FROM tests t WHERE run_id=? AND NOT EXISTS "
                               "(SELECT 1 FROM attempts a WHERE a.test_id=t.test_id)", (run_id,)).fetchall():
        conn.execute("INSERT INTO attempts(attempt_id, test_id, idempotency_key, attempt_no, target_spec_hash,"
                     " execution_status, error_type, error_detail, finished_at)"
                     " SELECT ?, ?, ?, 1, r.spec_hash, 'CANCELLED', 'CANCELLED', '실행 전 중단', ? FROM runs r WHERE r.run_id=?",
                     (new_id("att"), tid, f"{tid}#1", now(), run_id))
        n_cancel += 1
    n_eval = 0
    for t in conn.execute("SELECT t.test_id, t.oracle_id, (SELECT attempt_id FROM attempts a WHERE a.test_id=t.test_id "
                          "ORDER BY attempt_no DESC, rowid DESC LIMIT 1) AS aid FROM tests t WHERE run_id=? AND NOT EXISTS "
                          "(SELECT 1 FROM evaluations e WHERE e.test_id=t.test_id)", (run_id,)).fetchall():
        st = conn.execute("SELECT execution_status FROM attempts WHERE attempt_id=?", (t["aid"],)).fetchone()
        executed = st and st[0] == ExecutionStatus.EXECUTED.value
        from .enums import EvidenceLevel, JudgeType
        v = Verdict(EvaluationStatus.INCONCLUSIVE if executed else EvaluationStatus.NOT_RUN, JudgeType.UNKNOWN,
                    EvidenceLevel.UNKNOWN, reason="판정 전 중단" if executed else "실행되지 않음")
        codex.record_evaluation(conn, t["test_id"], t["aid"], t["oracle_id"], v)
        n_eval += 1
    return {"interrupted": n_int, "settled_reservations": n_res, "cancelled_tests": n_cancel, "closed_evals": n_eval}


class Controller:
    def __init__(self, conn, cfg: Config, spec: TargetSpec, run_id: str, providers: dict | None = None,
                 sleep=time.sleep, clock=time.monotonic, install_sigint: bool = True):
        self.conn, self.cfg, self.spec, self.run_id = conn, cfg, spec, run_id
        self.clock = clock
        run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"run이 없습니다: {run_id}")
        if run["spec_hash"] != spec.spec_hash:
            raise Blocked(StopReason.NOT_ALLOWED,
                          "대상 명세·프롬프트가 이 run을 만든 뒤 바뀌었습니다. 새 run으로 시작하세요")
        self.mode = RunMode(run["mode"])
        self.cursor = json.loads(run["cursor_json"] or "null") or initial_cursor()
        self.dir = run_dir(cfg, run_id)
        self.budget = Budget(conn, run_id, cfg.limits)
        self.stopper = Stopper(conn, run_id, self.dir)
        if install_sigint:
            self.stopper.install_sigint()
        self.executor = Executor(conn, cfg, spec, run_id, self.budget, self.stopper, providers, sleep)
        self.wedge_model = cfg.model(cfg.attacker_model)
        self.verifier = Verifier(self.executor, cfg.model(cfg.judge_model), load_judge_system(cfg.prompts_dir))
        self.wedge_system = load_system(cfg.prompts_dir)
        self._mark = clock()

    # ---------------- 실행 ----------------
    def run(self) -> tuple[str, str, str]:
        """(run_state, stop_reason, detail)을 돌려준다."""
        self.budget.check_can_start_run()
        self._set_state(RunState.RUNNING, started=True)
        try:
            if self.mode == RunMode.REGRESS:
                self._regress()
                raise _Stop(StopReason.COMPLETED, "회귀 재실행 완료")
            while True:
                self._check_limits()
                self._turn()
                self._save_cursor()
        except _Stop as e:
            return self._finish(RunState.STOPPED, e.reason, e.detail)
        except StopRequested:
            return self._finish(RunState.STOPPED, StopReason.USER_STOP, self.stopper.requested() or "")
        except BudgetExceeded as e:
            return self._finish(RunState.STOPPED, e.reason, e.detail)
        except Blocked as e:
            return self._finish(RunState.BLOCKED, e.reason, e.detail)
        except KeyboardInterrupt:
            return self._finish(RunState.STOPPED, StopReason.USER_STOP, "Ctrl+C 2회: 즉시 중단")
        finally:
            self.stopper.uninstall()

    def _finish(self, state: RunState, reason: StopReason, detail: str):
        self._save_cursor()
        self.conn.execute("UPDATE runs SET run_state='STOPPING' WHERE run_id=?", (self.run_id,))
        info = cleanup_dangling(self.conn, self.run_id, self.budget)
        self.conn.execute("UPDATE runs SET run_state=?, stop_reason=?, stop_detail=?, ended_at=? WHERE run_id=?",
                          (state.value, reason.value, detail, now(), self.run_id))
        event(self.conn, self.run_id, "run", self.run_id, "run_state", "RUNNING", state.value,
              {"stop_reason": reason.value, "detail": detail, "cleanup": info})
        from .report import write_report
        write_report(self.conn, self.cfg, self.run_id)
        return state.value, reason.value, detail

    def _set_state(self, state: RunState, started=False):
        if started:
            self.conn.execute("UPDATE runs SET run_state=?, stop_reason=NULL, stop_detail=NULL, stop_requested=0,"
                              " started_at=COALESCE(started_at, ?), ended_at=NULL WHERE run_id=?",
                              (state.value, now(), self.run_id))
            stop_file = self.dir / "STOP"
            if stop_file.exists():
                stop_file.unlink()
        else:
            self.conn.execute("UPDATE runs SET run_state=? WHERE run_id=?", (state.value, self.run_id))

    def _save_cursor(self):
        t = self.clock()
        self.cursor["elapsed_s"] = self.cursor.get("elapsed_s", 0.0) + (t - self._mark)
        self._mark = t
        self.conn.execute("UPDATE runs SET cursor_json=? WHERE run_id=?", (json.dumps(self.cursor), self.run_id))

    def _check_limits(self):
        why = self.stopper.requested()
        if why:
            raise StopRequested()
        lim = self.cfg.limits
        if self.cursor["tests"] >= lim.max_tests_per_run:
            raise _Stop(StopReason.MAX_TESTS, f"테스트 수 상한 {lim.max_tests_per_run}개 도달")
        elapsed = self.cursor.get("elapsed_s", 0.0) + (self.clock() - self._mark)
        if elapsed >= lim.max_wall_minutes * 60:
            raise _Stop(StopReason.MAX_WALL_TIME, f"실행 시간 상한 {lim.max_wall_minutes}분 도달")

    # ---------------- 공통 도우미 ----------------
    def _inv(self):
        return next(i for i in self.spec.invariants if i.id == self.cursor["invariant_id"])

    def _tried(self) -> list:
        return self.cursor["families_tried"].setdefault(self.cursor["invariant_id"], [])

    def _step(self, state: S, allowed, proposed=None, applied=None, override=None) -> int:
        self.cursor["seq"] += 1
        cur = self.conn.execute(
            "INSERT INTO steps(run_id, seq, wedge_state, invariant_id, attack_family, finding_id, allowed_actions,"
            " proposed_action, applied_action, override_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, self.cursor["seq"], state.value, self.cursor.get("invariant_id"), self.cursor.get("family"),
             self.cursor.get("finding_id"), json.dumps([a.value for a in allowed]), proposed,
             applied.value if applied else None, override, now()))
        return cur.lastrowid

    def _ask(self, allowed: list[A], default: A) -> tuple[Proposal | None, str | None, str | None]:
        """WEDGE에게 행동·테스트를 묻는다. (제안, 원래 제안 문자열, override 사유)."""
        from .enums import TEST_ACTIONS
        if not any(a in TEST_ACTIONS for a in allowed):
            return Proposal(action=default), None, "테스트가 필요 없는 선택지만 남아 WEDGE 호출 생략"
        ctx = codex.context_slice(self.conn, self.run_id, self.spec, self.cursor, [a.value for a in allowed],
                                  default.value, self.budget.remaining_run(), self.cfg.loop.recent_tests_in_context,
                                  self.cfg.loop.prior_inputs_in_context)
        seen = (codex.seen_input_keys(self.conn, self.spec, self.run_id, self.cursor["invariant_id"])
                if self.cursor.get("plan") else None)
        user = build_user(ctx)
        err = None
        raw = None
        for _ in range(2):
            msg = user if err is None else user + f"\n\n직전 출력이 계약을 어겼다: {err}. 계약에 맞는 JSON만 다시 출력하라."
            try:
                out = self.executor.call(CallRole.WEDGE, self.wedge_model, self.wedge_system, msg, cache_system=True)
            except CallFailed as e:
                err = str(e)
                break
            raw = out.result.text
            try:
                p = parse_proposal(raw, ctx, self.spec, seen)
                self.cursor["attacker_failures"] = 0
                return p, p.action.value, None
            except ProposalError as e:
                err = str(e)
        self.cursor["attacker_failures"] += 1
        event(self.conn, self.run_id, "wedge", self.run_id, "proposal_error", None, None,
              {"error": err, "raw": (raw or "")[:2000]})
        if self.cursor["attacker_failures"] >= 3:
            raise _Stop(StopReason.ATTACKER_ERROR, f"WEDGE 출력 오류 3턴 연속: {err}")
        if default in TEST_ACTIONS:
            return None, None, f"WEDGE 오류로 이번 턴 건너뜀: {err}"
        return Proposal(action=default), None, f"WEDGE 오류로 기본 행동 적용: {err}"

    def _new_test(self, step_id, attack_id, purpose: TestPurpose, input_text, expected, forbidden, oracle_id,
                  parent=None, invariant_id=None, test_set=TestSet.EXPLORATORY) -> dict:
        tid = new_id("t")
        self.conn.execute(
            "INSERT INTO tests(test_id, run_id, attack_id, step_id, parent_test_id, invariant_id, purpose, input_text,"
            " expected_behavior, forbidden_behavior, oracle_id, test_set, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, self.run_id, attack_id, step_id, parent, invariant_id or self.cursor["invariant_id"], purpose.value,
             input_text, expected, forbidden, oracle_id, test_set.value, now()))
        return dict(self.conn.execute("SELECT * FROM tests WHERE test_id=?", (tid,)).fetchone())

    def _attack(self, p: Proposal) -> str:
        aid = new_id("atk")
        self.conn.execute("INSERT INTO attacks(attack_id, run_id, invariant_id, attack_family, hypothesis, mechanism_tag,"
                          " rationale, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (aid, self.run_id, self.cursor["invariant_id"], p.attack_family or self.cursor.get("family")
                           or "UNSPECIFIED", p.hypothesis or "(가설 없음)", p.mechanism_tag, p.rationale, now()))
        return aid

    def _exec(self, test: dict, exploit: bool) -> Verdict:
        if self.cursor["tests"] >= self.cfg.limits.max_tests_per_run:
            raise _Stop(StopReason.MAX_TESTS, f"테스트 수 상한 {self.cfg.limits.max_tests_per_run}개 도달")
        self.cursor["tests"] += 1
        if exploit:
            self.cursor["exploit_tests"] += 1
        attempt = self.executor.run_test(test["test_id"], test["input_text"])
        if attempt and attempt["execution_status"] == ExecutionStatus.ERROR.value:
            self.cursor["consecutive_errors"] += 1
        else:
            self.cursor["consecutive_errors"] = 0
        v = self.verifier.evaluate(attempt, test, self.spec.oracles.get(test["oracle_id"]))
        codex.record_evaluation(self.conn, test["test_id"], attempt["attempt_id"] if attempt else None,
                                test["oracle_id"], v)
        if self.cursor["consecutive_errors"] >= self.cfg.limits.max_consecutive_errors:
            raise _Stop(StopReason.CONSECUTIVE_ERRORS,
                        f"대상 호출 오류 {self.cursor['consecutive_errors']}회 연속 (마지막: {attempt.get('error_detail')})")
        return v

    # ---------------- 상태별 처리 ----------------
    def _turn(self):
        st = S(self.cursor["state"])
        {S.ACQUIRE: self._acquire, S.PROBE: self._probe, S.FOCUS: self._focus, S.VERIFY: self._verify,
         S.DEEPEN: self._deepen, S.SPLIT: self._deepen, S.NEXT_INVARIANT: self._next_invariant}[st]()

    def _acquire(self):
        plan = self.cursor.get("plan")
        ordered = self.spec.invariants
        if plan:  # 이어하기: 미검사 규칙 먼저, 그다음 기존 규칙 심화 (codex.build_plan)
            by_id = {i.id: i for i in self.spec.invariants}
            ordered = [by_id[iid] for iid in plan["order"] if iid in by_id]
        remaining = [i for i in ordered if i.id not in self.cursor["done_invariants"]]
        if not remaining:
            self._step(S.ACQUIRE, [A.STOP], None, A.STOP)
            raise _Stop(StopReason.NO_TARGETS_LEFT, "모든 불변조건 검사 완료")
        c = self.cursor
        c.update(invariant_id=remaining[0].id, family=None, probes=0, stale=0, invalid_streak=0,
                 finding_id=None, focus_test_id=None)
        self._step(S.ACQUIRE, [A.CONTINUE], None, A.CONTINUE)
        c["state"] = S.PROBE.value

    def _probe(self):
        c, lp = self.cursor, self.cfg.loop
        can_pivot = len(self._tried()) < lp.families_per_invariant and c["family"] is not None
        if c["probes"] < lp.probe_limit and c["stale"] < lp.stale_streak:
            allowed = [A.CONTINUE] + ([A.PIVOT] if can_pivot else []) + [A.NEXT_INVARIANT]
            default = A.CONTINUE
        else:
            allowed = ([A.PIVOT] if can_pivot else []) + [A.NEXT_INVARIANT]
            default = A.NEXT_INVARIANT  # 코드는 테스트를 만들 수 없으므로 소진 시 기본은 다음 불변조건
        p, proposed, override = self._ask(allowed, default)
        if p is None:
            self._step(S.PROBE, allowed, proposed, None, override)
            c["stale"] += 1
            return
        step_id = self._step(S.PROBE, allowed, proposed, p.action, override)
        if p.action == A.NEXT_INVARIANT:
            c["state"] = S.NEXT_INVARIANT.value
            return
        if p.action == A.PIVOT or c["family"] is None:
            c.update(family=p.attack_family, probes=0, stale=0)
            if p.attack_family not in self._tried():
                self._tried().append(p.attack_family)
        self._run_probe_tests(p, step_id)

    def _run_probe_tests(self, p: Proposal, step_id: int):
        c = self.cursor
        aid = self._attack(p)
        for pt in p.tests:
            test = self._new_test(step_id, aid, TestPurpose.PROBE, pt.input_text, pt.expected_behavior,
                                  pt.forbidden_behavior, pt.oracle_id)
            v = self._exec(test, exploit=False)
            c["probes"] += 1
            if v.status == EvaluationStatus.FAIL:
                c["invalid_streak"] = 0
                fid, is_new = codex.link_fail(self.conn, self.run_id, self.spec.target_id, test,
                                              v.violated_condition_id, p.mechanism_tag, FindingTestRole.ORIGIN)
                status = self.conn.execute("SELECT verification_status FROM findings WHERE finding_id=?",
                                           (fid,)).fetchone()[0]
                if is_new or status != VerificationStatus.VERIFIED.value:
                    c.update(finding_id=fid, focus_test_id=test["test_id"], stale=0, state=S.FOCUS.value)
                    return
                c["stale"] += 1  # 이미 확인된 실패를 다시 본 것: 새 고유 실패 아님
            elif v.status == EvaluationStatus.INVALID:
                c["invalid_streak"] += 1
                c["stale"] += 1
                if c["invalid_streak"] >= 2:
                    event(self.conn, self.run_id, "oracle", test["oracle_id"], "check_needed", None, None,
                          {"reason": v.reason})
                    c["state"] = S.NEXT_INVARIANT.value
                    return
            else:
                c["invalid_streak"] = 0
                c["stale"] += 1
        c["state"] = S.PROBE.value

    def _focus(self):
        self._step(S.FOCUS, [A.VERIFY], None, A.VERIFY)
        self.cursor["state"] = S.VERIFY.value

    def _verify(self):
        c = self.cursor
        src = dict(self.conn.execute("SELECT * FROM tests WHERE test_id=?", (c["focus_test_id"],)).fetchone())
        step_id = self._step(S.VERIFY, [A.VERIFY], None, A.VERIFY)
        results = []
        for _ in range(self.cfg.loop.repro_n):
            t = self._new_test(step_id, src["attack_id"], TestPurpose.REPRO, src["input_text"],
                               src["expected_behavior"], src["forbidden_behavior"], src["oracle_id"],
                               parent=src["test_id"], invariant_id=src["invariant_id"])
            v = self._exec(t, exploit=True)
            results.append((t["test_id"], v.status.value, v.violated_condition_id))
        status = codex.judge_reproduction(self.conn, self.run_id, c["finding_id"], results)
        if status == VerificationStatus.VERIFIED.value:
            c.update(state=S.DEEPEN.value, stale=0)
        else:
            c.update(state=S.PROBE.value, finding_id=None, focus_test_id=None)

    def _deepen(self):
        c, lp = self.cursor, self.cfg.loop
        fid = c["finding_id"]
        n = c["deepen"].get(fid, 0)
        cap = lp.exploit_share_max * self.cfg.limits.max_tests_per_run
        can_pivot = len(self._tried()) < lp.families_per_invariant
        if n < lp.deepen_limit and c["stale"] < lp.stale_streak and c["exploit_tests"] < cap:
            allowed = [A.DEEPEN, A.SPLIT] + ([A.PIVOT] if can_pivot else []) + [A.NEXT_INVARIANT]
            default = A.DEEPEN
        else:
            allowed = ([A.PIVOT] if can_pivot else []) + [A.NEXT_INVARIANT]
            default = A.NEXT_INVARIANT
        state_now = S(c["state"])
        p, proposed, override = self._ask(allowed, default)
        if p is None:
            self._step(state_now, allowed, proposed, None, override)
            c["stale"] += 1
            return
        step_id = self._step(state_now, allowed, proposed, p.action, override)
        if p.action == A.NEXT_INVARIANT:
            c["state"] = S.NEXT_INVARIANT.value
            return
        if p.action == A.PIVOT:
            c.update(state=S.PROBE.value, family=p.attack_family, probes=0, stale=0, finding_id=None,
                     focus_test_id=None)
            self._tried().append(p.attack_family)
            self._run_probe_tests(p, step_id)
            return
        c["state"] = S.SPLIT.value if p.action == A.SPLIT else S.DEEPEN.value
        aid = self._attack(p)
        purpose = TestPurpose.SPLIT if p.action == A.SPLIT else TestPurpose.VARIANT
        focus = c["focus_test_id"]
        for pt in p.tests:
            t = self._new_test(step_id, aid, purpose, pt.input_text, pt.expected_behavior, pt.forbidden_behavior,
                               pt.oracle_id, parent=focus)
            v = self._exec(t, exploit=True)
            c["deepen"][fid] = c["deepen"].get(fid, 0) + 1
            if v.status == EvaluationStatus.FAIL:
                fid2, is_new = codex.link_fail(self.conn, self.run_id, self.spec.target_id, t,
                                               v.violated_condition_id, p.mechanism_tag, FindingTestRole.VARIANT)
                if is_new:  # 다른 실패 메커니즘: 새 finding → 재현 검증
                    c.update(finding_id=fid2, focus_test_id=t["test_id"], stale=0, state=S.FOCUS.value)
                    return
                if fid2 == fid and codex.update_minimal(self.conn, fid, t):
                    c["stale"] = 0  # 더 짧은 최소 실패 입력 = 새 정보
                else:
                    c["stale"] += 1
            else:
                c["stale"] += 1

    def _next_invariant(self):
        """구 WEDGE RESEED. finding의 검증 상태는 절대 바꾸지 않는다 (명세서 5장)."""
        c = self.cursor
        self._step(S.NEXT_INVARIANT, [A.CONTINUE], None, A.CONTINUE)
        if c["invariant_id"] and c["invariant_id"] not in c["done_invariants"]:
            c["done_invariants"].append(c["invariant_id"])
        c.update(state=S.ACQUIRE.value, finding_id=None, focus_test_id=None, family=None)

    # ---------------- 회귀 모드 ----------------
    def _regress(self):
        c = self.cursor
        rows = self.conn.execute(
            "SELECT f.*, t.input_text, t.expected_behavior, t.forbidden_behavior, t.oracle_id, t.attack_id"
            " FROM findings f JOIN tests t ON t.test_id=f.minimal_test_id"
            " WHERE f.target_id=? AND f.in_regression_set=1 ORDER BY f.created_at", (self.spec.target_id,)).fetchall()
        for f in rows:
            if f["finding_id"] in c["regress_done"]:
                continue
            self._check_limits()
            c.update(invariant_id=f["invariant_id"], finding_id=f["finding_id"])
            step_id = self._step(S.VERIFY, [A.VERIFY], None, A.VERIFY)
            statuses = []
            for _ in range(self.cfg.loop.repro_n):
                t = self._new_test(step_id, f["attack_id"], TestPurpose.REGRESSION, f["input_text"],
                                   f["expected_behavior"], f["forbidden_behavior"], f["oracle_id"],
                                   parent=f["minimal_test_id"], invariant_id=f["invariant_id"],
                                   test_set=TestSet.REGRESSION)
                v = self._exec(t, exploit=False)
                self.conn.execute("INSERT OR IGNORE INTO finding_tests(finding_id, test_id, role, run_id) VALUES (?,?,?,?)",
                                  (f["finding_id"], t["test_id"], FindingTestRole.REGRESSION.value, self.run_id))
                statuses.append(v.status.value)
            outcome = regression_outcome(statuses)
            event(self.conn, self.run_id, "regression", f["finding_id"], "outcome", None, outcome.value,
                  {"statuses": statuses})
            c["regress_done"].append(f["finding_id"])
            self._save_cursor()


def regression_outcome(statuses: list[str]) -> RegressionOutcome:
    if "FAIL" in statuses:
        return RegressionOutcome.STILL_FAILING
    judged = [s for s in statuses if s in ("PASS", "INCONCLUSIVE", "INVALID")]
    if not judged:
        return RegressionOutcome.NOT_EXECUTED
    if all(s == "PASS" for s in judged) and len(judged) == len(statuses):
        return RegressionOutcome.NOT_REPRODUCED
    return RegressionOutcome.INCONCLUSIVE
