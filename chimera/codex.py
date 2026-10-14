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


def latest_eval(conn, test_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM evaluations WHERE test_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                     (test_id,)).fetchone()
    return dict(r) if r else None


def context_slice(conn, run_id: str, spec, cursor: dict, allowed: list[str], default: str,
                  remaining_usd: float, recent_n: int) -> dict:
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
    }


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
