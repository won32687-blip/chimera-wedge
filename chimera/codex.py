"""CODEX: 기록·중복제거·재현 판정·회귀 세트 승격·ContextSlice (명세서 2·4·7장). LLM 없이 코드만.

- FAIL 1회 → finding PENDING. 재현 판정 후 VERIFIED/DISPUTED/REJECTED.
- VERIFIED finding만 회귀 세트(in_regression_set=1)에 들어간다 (DB CHECK로도 강제).
- 같은 fingerprint의 FAIL은 같은 finding에 연결된다(고유 실패 수 증가 없음).
"""
from __future__ import annotations

import hashlib
import json

from .db import Tx, event, new_id, now
from .enums import EvaluationStatus, FindingTestRole, VerificationStatus


def fingerprint(target_id: str, invariant_id: str, violated_condition_id: str, mechanism_tag: str | None) -> str:
    key = "|".join([target_id, invariant_id, violated_condition_id, (mechanism_tag or "").strip().lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def record_evaluation(conn, test_id: str, attempt_id: str | None, oracle_id: str, v) -> str:
    eid = new_id("ev")
    conn.execute(
        "INSERT INTO evaluations(evaluation_id, attempt_id, test_id, evaluation_status, oracle_id, judge_type,"
        " violated_condition_id, evidence_quote, reason, evidence_level, judge_cost_usd, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, attempt_id, test_id, v.status.value, oracle_id, v.judge_type.value, v.violated_condition_id,
         v.evidence_quote, v.reason, v.evidence_level.value, v.judge_cost_usd, now()))
    return eid


def _finding(conn, finding_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM findings WHERE finding_id=?", (finding_id,)).fetchone())


def link_fail(conn, run_id: str, target_id: str, test: dict, violated: str, mechanism_tag: str | None,
              role: FindingTestRole) -> tuple[str, bool]:
    """FAIL을 finding에 연결. 같은 fingerprint가 있으면 기존 것에 연결, 없으면 새로 만든다.

    invariant·위반 조건이 같고 태그만 다른 기존 finding이 있으면 duplicate_of 후보로만 기록한다(자동 병합 안 함).
    반환: (finding_id, 새로 만들었는가)
    """
    fp = fingerprint(target_id, test["invariant_id"], violated, mechanism_tag)
    with Tx(conn) as c:
        row = c.execute("SELECT finding_id FROM findings WHERE target_id=? AND fingerprint=?",
                        (target_id, fp)).fetchone()
        if row:
            fid = row[0]
            c.execute("UPDATE findings SET observations=observations+1, updated_at=? WHERE finding_id=?", (now(), fid))
            c.execute("INSERT OR IGNORE INTO finding_tests(finding_id, test_id, role, run_id) VALUES (?,?,?,?)",
                      (fid, test["test_id"], role.value, run_id))
            _maybe_minimal(c, fid, test)
            return fid, False
        cand = c.execute("SELECT finding_id FROM findings WHERE target_id=? AND invariant_id=? AND "
                         "violated_condition_id=? ORDER BY created_at LIMIT 1",
                         (target_id, test["invariant_id"], violated)).fetchone()
        fid = new_id("fd")
        c.execute("INSERT INTO findings(finding_id, target_id, fingerprint, invariant_id, violated_condition_id,"
                  " mechanism_tag, first_test_id, minimal_test_id, verification_status, duplicate_of,"
                  " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, target_id, fp, test["invariant_id"], violated, mechanism_tag, test["test_id"],
                   test["test_id"], VerificationStatus.PENDING.value, cand[0] if cand else None, now(), now()))
        c.execute("INSERT INTO finding_tests(finding_id, test_id, role, run_id) VALUES (?,?,?,?)",
                  (fid, test["test_id"], FindingTestRole.ORIGIN.value, run_id))
        event(c, run_id, "finding", fid, "verification_status", None, "PENDING", {"fingerprint": fp})
        return fid, True


def _maybe_minimal(c, fid: str, test: dict) -> bool:
    cur = c.execute("SELECT t.input_text FROM findings f JOIN tests t ON t.test_id=f.minimal_test_id "
                    "WHERE f.finding_id=?", (fid,)).fetchone()
    if cur and len(test.get("input_text") or "") < len(cur[0] or ""):
        c.execute("UPDATE findings SET minimal_test_id=?, updated_at=? WHERE finding_id=?",
                  (test["test_id"], now(), fid))
        return True
    return False


def update_minimal(conn, fid: str, test: dict) -> bool:
    with Tx(conn) as c:
        return _maybe_minimal(c, fid, test)


def judge_reproduction(conn, run_id: str, finding_id: str, repro_results: list[tuple[str, str, str | None]]) -> str:
    """재현 결과로 verification_status를 정한다 (명세서 4장).

    repro_results: [(test_id, evaluation_status, violated_condition_id)]
    - 같은 위반 조건 FAIL ≥ 1          → VERIFIED (재현율 k/n 기록)
    - FAIL 0, PASS ≥ 1                  → DISPUTED (단일 비재현이 기존 관찰을 무효화하지 않음)
    - 실행된 재실행 전부 INVALID         → REJECTED (Oracle 오류로 원래 FAIL을 신뢰할 수 없음)
    - 그 외(모두 NOT_RUN/INCONCLUSIVE)   → PENDING 유지
    """
    f = _finding(conn, finding_id)
    statuses = [s for _, s, _ in repro_results]
    k = sum(1 for _, s, vid in repro_results if s == "FAIL" and vid == f["violated_condition_id"])
    judged = [s for s in statuses if s in ("PASS", "FAIL", "INVALID")]
    if k >= 1:
        new = VerificationStatus.VERIFIED
    elif "PASS" in statuses:
        new = VerificationStatus.DISPUTED
    elif judged and all(s == "INVALID" for s in judged):
        new = VerificationStatus.REJECTED
    else:
        new = VerificationStatus.PENDING
    total = sum(1 for s in statuses if s in ("PASS", "FAIL"))
    with Tx(conn) as c:
        c.execute("UPDATE findings SET verification_status=?, repro_fail=repro_fail+?, repro_total=repro_total+?,"
                  " in_regression_set=?, updated_at=? WHERE finding_id=?",
                  (new.value, k, total, 1 if new == VerificationStatus.VERIFIED else 0, now(), finding_id))
        for tid, _, _ in repro_results:
            c.execute("INSERT OR IGNORE INTO finding_tests(finding_id, test_id, role, run_id) VALUES (?,?,?,?)",
                      (finding_id, tid, FindingTestRole.REPRO.value, run_id))
        event(c, run_id, "finding", finding_id, "verification_status", f["verification_status"], new.value,
              {"repro": f"{k}/{len(repro_results)}"})
    return new.value


_PRIOR_TESTS = (
    "FROM tests t JOIN runs r ON r.run_id=t.run_id LEFT JOIN attacks a ON a.attack_id=t.attack_id"
    " WHERE r.target_id=? AND r.spec_hash=? AND r.mode='EXPLORE' AND t.run_id<>?"
    " AND t.purpose IN ('PROBE','VARIANT','SPLIT')")
_LATEST_EVAL = "(SELECT evaluation_status FROM evaluations e WHERE e.test_id=t.test_id ORDER BY e.rowid DESC LIMIT 1)"


def prior_coverage(conn, spec, exclude_run_id: str) -> dict:
    """같은 대상·같은 명세 해시의 이전 탐색 실행에서 불변조건별로 무엇을 했는지 센다. 읽기만 한다.

    판정된 테스트(judged)는 최신 판정이 PASS 또는 FAIL인 것만 센다. INCONCLUSIVE·INVALID·NOT_RUN은
    '검사했다'고 보지 않는다. 명세·프롬프트가 바뀌면(spec_hash가 다르면) 이전 기록을 쓰지 않는다.
    """
    cov = {i.id: {"judged": 0, "unjudged": 0, "fail": 0, "families": []} for i in spec.invariants}
    rows = conn.execute(f"SELECT t.invariant_id, a.attack_family, {_LATEST_EVAL} AS ev {_PRIOR_TESTS}"
                        " ORDER BY t.created_at, t.rowid", (spec.target_id, spec.spec_hash, exclude_run_id)).fetchall()
    for r in rows:
        c = cov.get(r["invariant_id"])
        if c is None:
            continue
        if r["ev"] in ("PASS", "FAIL"):
            c["judged"] += 1
            c["fail"] += r["ev"] == "FAIL"
            if r["attack_family"] and r["attack_family"] not in c["families"]:
                c["families"].append(r["attack_family"])
        else:
            c["unjudged"] += 1
    return cov


def build_plan(spec, coverage: dict, cover_min_tests: int, prior_runs: list[str]) -> dict:
    """불변조건 순서와 탐색 방식을 정한다. 미검사(UNCOVERED)를 먼저, 그다음 심화(DEEPEN_EXISTING).

    같은 방식 안에서는 판정된 테스트가 적은 순서, 같으면 명세 순서.
    """
    from .enums import CoverageTrack
    order_idx = {i.id: n for n, i in enumerate(spec.invariants)}
    track = {iid: (CoverageTrack.UNCOVERED if c["judged"] < cover_min_tests else CoverageTrack.DEEPEN_EXISTING).value
             for iid, c in coverage.items()}
    order = sorted(coverage, key=lambda iid: (track[iid] != CoverageTrack.UNCOVERED.value,
                                              coverage[iid]["judged"], order_idx[iid]))
    return {"continued_from": prior_runs, "order": order, "track": track, "prior": coverage,
            "cover_min_tests": cover_min_tests}


def prior_runs(conn, spec, exclude_run_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT run_id FROM runs WHERE target_id=? AND spec_hash=? AND mode='EXPLORE' AND run_id<>?"
        " AND EXISTS (SELECT 1 FROM tests t WHERE t.run_id=runs.run_id) ORDER BY started_at, rowid", (spec.target_id, spec.spec_hash, exclude_run_id)).fetchall()]


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def prior_inputs(conn, spec, run_id: str, invariant_id: str) -> list[dict]:
    """이전 실행에서 이 불변조건에 보낸 입력 (최근 순)."""
    rows = conn.execute(f"SELECT t.input_text, a.attack_family, {_LATEST_EVAL} AS ev {_PRIOR_TESTS}"
                        " AND t.invariant_id=? ORDER BY t.created_at DESC, t.rowid DESC",
                        (spec.target_id, spec.spec_hash, run_id, invariant_id)).fetchall()
    return [{"input": r["input_text"] or "", "attack_family": r["attack_family"], "evaluation": r["ev"]} for r in rows]


def seen_input_keys(conn, spec, run_id: str, invariant_id: str) -> set[str]:
    """이전 실행 입력과 '공백만 다른 같은 입력'을 거르기 위한 키."""
    return {_norm(p["input"]) for p in prior_inputs(conn, spec, run_id, invariant_id)}


def latest_eval(conn, test_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM evaluations WHERE test_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                     (test_id,)).fetchone()
    return dict(r) if r else None


def context_slice(conn, run_id: str, spec, cursor: dict, allowed: list[str], default: str,
                  remaining_usd: float, recent_n: int, prior_n: int = 6) -> dict:
    """WEDGE에게 줄 최소 정보. 대상 응답은 UNTRUSTED 데이터로 표시한다."""
    inv = next((i for i in spec.invariants if i.id == cursor.get("invariant_id")), None)
    oracle = spec.oracles.get(inv.oracle_id) if inv else None
    recent = conn.execute(
        "SELECT t.test_id, t.purpose, t.invariant_id, t.input_text, a.attack_family,"
        " (SELECT response_text FROM attempts WHERE test_id=t.test_id AND execution_status='EXECUTED'"
        "  ORDER BY attempt_no DESC LIMIT 1) AS response,"
        " (SELECT evaluation_status FROM evaluations WHERE test_id=t.test_id ORDER BY rowid DESC LIMIT 1) AS ev,"
        " (SELECT violated_condition_id FROM evaluations WHERE test_id=t.test_id ORDER BY rowid DESC LIMIT 1) AS vid"
        " FROM tests t LEFT JOIN attacks a ON a.attack_id=t.attack_id WHERE t.run_id=?"
        " ORDER BY t.created_at DESC, t.rowid DESC LIMIT ?", (run_id, recent_n)).fetchall()
    findings = conn.execute("SELECT finding_id, invariant_id, violated_condition_id, mechanism_tag,"
                            " verification_status, repro_fail, repro_total, observations FROM findings"
                            " WHERE target_id=?", (spec.target_id,)).fetchall()
    focus = None
    if cursor.get("finding_id"):
        fr = conn.execute("SELECT f.*, t.input_text FROM findings f JOIN tests t ON t.test_id=f.minimal_test_id"
                          " WHERE f.finding_id=?", (cursor["finding_id"],)).fetchone()
        if fr:
            focus = {"finding_id": fr["finding_id"], "violated_condition_id": fr["violated_condition_id"],
                     "mechanism_tag": fr["mechanism_tag"], "verification_status": fr["verification_status"],
                     "minimal_input": fr["input_text"], "repro": f"{fr['repro_fail']}/{fr['repro_total']}"}
    continuation = None
    plan = cursor.get("plan")
    if plan and inv:
        prior = plan["prior"].get(inv.id, {})
        track = plan["track"].get(inv.id)
        continuation = {
            "track": track,
            "prior_judged_tests": prior.get("judged", 0),
            "prior_families": prior.get("families", []),
            "untried_spec_families": [f for f in inv.attack_families if f not in prior.get("families", [])],
            "prior_inputs": [{**p, "input": p["input"][:400]}
                             for p in prior_inputs(conn, spec, run_id, inv.id)[:prior_n]]
            if track == "DEEPEN_EXISTING" else [],
        }
    return {
        "state": cursor.get("state"),
        "allowed_actions": allowed,
        "default_action": default,
        "invariant": None if not inv else {
            "id": inv.id, "description": inv.description, "source": inv.source.value,
            "oracle_id": inv.oracle_id, "attack_families": inv.attack_families},
        "oracle": None if not oracle else {
            "id": oracle.id, "type": oracle.type.value,
            "acceptance": [{"id": c.id, "text": c.text} for c in oracle.acceptance],
            "rejection": [{"id": c.id, "text": c.text} for c in oracle.rejection],
            "checks": [{"id": c.id, "kind": c.kind, "description": c.description} for c in oracle.checks]},
        "current_family": cursor.get("family"),
        "families_tried": cursor.get("families_tried", {}).get(cursor.get("invariant_id") or "", []),
        "probes_in_family": cursor.get("probes", 0),
        "deepen_done": cursor.get("deepen", {}).get(cursor.get("finding_id") or "", 0),
        "focus_finding": focus,
        "known_findings": [{**dict(r), "trust": "VERIFIED" if r["verification_status"] == "VERIFIED" else "UNVERIFIED"}
                           for r in findings],
        "recent_tests": [{"test_id": r["test_id"], "purpose": r["purpose"], "invariant_id": r["invariant_id"],
                          "attack_family": r["attack_family"], "input": r["input_text"],
                          "target_response_UNTRUSTED": (r["response"] or "")[:1500],
                          "evaluation": r["ev"], "violated_condition_id": r["vid"]} for r in recent],
        "budget_remaining_usd": round(remaining_usd, 4),
        "continuation": continuation,
    }


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
