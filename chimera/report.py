"""실행 리포트 (명세서 12장). data/runs/<run_id>/report.md 로 쓴다.

측정하지 않은 값은 지어내지 않고 '-'로 남긴다. 숫자는 모두 DB에서 다시 센다.
"""
from __future__ import annotations

import json

from .config import Config
from .controller import run_dir


def _count(conn, sql, params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def collect(conn, run_id: str) -> dict:
    run = dict(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
    p = (run_id,)
    tests = _count(conn, "SELECT COUNT(*) FROM tests WHERE run_id=?", p)
    executed = _count(conn, "SELECT COUNT(DISTINCT a.test_id) FROM attempts a JOIN tests t ON t.test_id=a.test_id"
                            " WHERE t.run_id=? AND a.execution_status='EXECUTED'", p)
    ev_rows = conn.execute(
        "SELECT e.evaluation_status, COUNT(*) FROM evaluations e JOIN tests t ON t.test_id=e.test_id"
        " WHERE t.run_id=? AND e.rowid = (SELECT MAX(rowid) FROM evaluations WHERE test_id=e.test_id)"
        " GROUP BY e.evaluation_status", p).fetchall()
    evals = {r[0]: r[1] for r in ev_rows}
    attempts = {r[0]: r[1] for r in conn.execute(
        "SELECT a.execution_status, COUNT(*) FROM attempts a JOIN tests t ON t.test_id=a.test_id"
        " WHERE t.run_id=? GROUP BY a.execution_status", p).fetchall()}
    findings = [dict(r) for r in conn.execute(
        "SELECT DISTINCT f.* FROM findings f JOIN finding_tests ft ON ft.finding_id=f.finding_id"
        " WHERE ft.run_id=? ORDER BY f.created_at", p).fetchall()]
    cost = {r[0]: r[1] for r in conn.execute(
        "SELECT call_role, ROUND(SUM(usd), 6) FROM budget_ledger WHERE run_id=? AND kind='SETTLE'"
        " GROUP BY call_role", p).fetchall()}
    calls = {r[0]: r[1] for r in conn.execute(
        "SELECT call_role, COUNT(*) FROM budget_ledger WHERE run_id=? AND kind='RESERVE' GROUP BY call_role",
        p).fetchall()}
    checked = {r[0] for r in conn.execute("SELECT DISTINCT invariant_id FROM tests WHERE run_id=?", p).fetchall()}
    all_inv = [r[0] for r in conn.execute("SELECT invariant_id FROM invariants WHERE target_id=? AND spec_hash=?",
                                          (run["target_id"], run["spec_hash"])).fetchall()]
    regress = [dict(r) for r in conn.execute(
        "SELECT entity_id AS finding_id, new_value AS outcome, detail FROM events"
        " WHERE run_id=? AND entity='regression' ORDER BY event_id", p).fetchall()]
    overrides = _count(conn, "SELECT COUNT(*) FROM steps WHERE run_id=? AND override_reason IS NOT NULL", p)
    wedge_errors = _count(conn, "SELECT COUNT(*) FROM events WHERE run_id=? AND entity='wedge'"
                                " AND field='proposal_error'", p)
    steps = [dict(r) for r in conn.execute(
        "SELECT seq, wedge_state, applied_action, proposed_action, override_reason FROM steps"
        " WHERE run_id=? ORDER BY seq", p).fetchall()]
    return dict(run=run, tests=tests, executed=executed, evals=evals, attempts=attempts, findings=findings,
                cost=cost, calls=calls, checked=sorted(checked), unchecked=[i for i in all_inv if i not in checked],
                regress=regress, overrides=overrides, wedge_errors=wedge_errors, steps=steps)


def render(d: dict) -> str:
    r = d["run"]
    ev = d["evals"]
    total_cost = sum(d["cost"].values())
    fs = d["findings"]
    by_status = {}
    for f in fs:
        by_status.setdefault(f["verification_status"], []).append(f)
    L = [f"# 실행 리포트 {r['run_id']}", "",
         f"- 대상: `{r['target_id']}` (명세 해시 `{r['spec_hash']}`)",
         f"- 모드: {r['mode']} · 상태: **{r['run_state']}** · 멈춘 이유: **{r['stop_reason'] or '-'}**",
         f"- 상세: {r['stop_detail'] or '-'}",
         f"- 시작 {r['started_at'] or '-'} · 종료 {r['ended_at'] or '-'}",
         f"- WEDGE 모델 `{r['wedge_model']}` · 채점기 모델 `{r['judge_model']}`", "",
         "## 테스트", "",
         "| 항목 | 개수 |", "|---|---|",
         f"| 생성된 테스트 | {d['tests']} |",
         f"| 응답을 받은 테스트 (EXECUTED) | {d['executed']} |"]
    for k in ("PASS", "FAIL", "INCONCLUSIVE", "INVALID", "NOT_RUN"):
        L.append(f"| 판정 {k} | {ev.get(k, 0)} |")
    L.append(f"| 실행 시도별 상태 | {', '.join(f'{k} {v}' for k, v in sorted(d['attempts'].items())) or '-'} |")
    L += ["", "## 발견된 실패 (finding)", "",
          f"- 재현 확인(VERIFIED, 회귀 세트 등록): **{len(by_status.get('VERIFIED', []))}개**",
          f"- 확인 대기(PENDING): {len(by_status.get('PENDING', []))}개 · "
          f"재현 안 됨(DISPUTED): {len(by_status.get('DISPUTED', []))}개 · "
          f"기각(REJECTED): {len(by_status.get('REJECTED', []))}개", ""]
    if fs:
        L += ["| finding | 상태 | 불변조건 | 위반 조건 | 메커니즘 | 재현 | 관찰 수 | 중복 후보 |",
              "|---|---|---|---|---|---|---|---|"]
        for f in fs:
            L.append(f"| `{f['finding_id']}` | {f['verification_status']} | {f['invariant_id']} | "
                     f"{f['violated_condition_id']} | {f['mechanism_tag'] or '-'} | "
                     f"{f['repro_fail']}/{f['repro_total']} | {f['observations']} | {f['duplicate_of'] or '-'} |")
    else:
        L.append("발견된 실패 없음.")
    if d["regress"]:
        L += ["", "## 회귀 재실행 결과", "", "| finding | 결과 | 판정들 |", "|---|---|---|"]
        for g in d["regress"]:
            st = json.loads(g["detail"] or "{}").get("statuses", [])
            L.append(f"| `{g['finding_id']}` | {g['outcome']} | {', '.join(st)} |")
    L += ["", "## 비용 (정산 기준, USD)", "", "| 역할 | 호출 예약 수 | 정산액 |", "|---|---|---|"]
    for role in ("TARGET", "WEDGE", "JUDGE"):
        c = d["cost"].get(role)
        L.append(f"| {role} | {d['calls'].get(role, 0)} | {'-' if c is None else f'{c:.6f}'} |")
    L.append(f"| 합계 | {sum(d['calls'].values())} | {total_cost:.6f} |")
    L += ["", "## 진행", "",
          f"- 검사한 불변조건: {', '.join(d['checked']) or '-'}",
          f"- 검사하지 않은 불변조건: {', '.join(d['unchecked']) or '없음'}",
          f"- WEDGE 출력 오류: {d['wedge_errors']}회 · 코드가 행동을 대신 정한 턴: {d['overrides']}회", "",
          "<details><summary>단계 기록</summary>", "",
          "| seq | 단계 | 적용 행동 | WEDGE 제안 | 비고 |", "|---|---|---|---|---|"]
    for s in d["steps"]:
        L.append(f"| {s['seq']} | {s['wedge_state']} | {s['applied_action'] or '-'} | "
                 f"{s['proposed_action'] or '-'} | {s['override_reason'] or ''} |")
    L += ["", "</details>", ""]
    return "\n".join(L)


def write_report(conn, cfg: Config, run_id: str):
    path = run_dir(cfg, run_id) / "report.md"
    path.write_text(render(collect(conn, run_id)), encoding="utf-8")
    return path
