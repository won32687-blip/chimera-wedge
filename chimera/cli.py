"""명령줄 진입점. Windows PowerShell에서도 그대로 쓴다.

  chimera check  --target mock_demo            설정·대상·키·예상 비용 확인 (API 호출 없음)
  chimera run    --target mock_demo            탐색 실행
  chimera regress --target mock_demo           회귀 세트 재실행
  chimera stop   RUN_ID                        다른 창에서 중단 요청
  chimera resume RUN_ID                        멈춘 실행 이어가기
  chimera status [RUN_ID]                      최근 실행 목록 / 한 실행 상태
  chimera report RUN_ID                        리포트 다시 만들기

모든 명령은 --config (기본 config.toml)을 받는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .budget import Budget, BudgetExceeded, estimate_input_tokens
from .config import ConfigError, load_config, load_target
from .controller import Controller, cleanup_dangling, create_run, run_dir
from .db import connect
from .enums import RunMode, StopReason
from .executor import Blocked, guard_target

EXIT_OK, EXIT_ERR, EXIT_BLOCKED = 0, 1, 2


def _utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _open(args):
    cfg = load_config(args.config)
    return cfg, connect(cfg.data_dir / "chimera.db")


def _print_result(conn, cfg, run_id, result):
    state, reason, detail = result
    print(f"\n실행 {run_id} 종료: {state} / {reason}")
    if detail:
        print(f"  상세: {detail}")
    b = Budget(conn, run_id, cfg.limits)
    print(f"  이번 실행 비용: ${b.run_settled():.6f} (상한 ${cfg.limits.max_cost_usd_per_run}) · "
          f"오늘 합계: ${b.day_committed():.6f} (상한 ${cfg.limits.max_cost_usd_per_day})")
    print(f"  리포트: {run_dir(cfg, run_id) / 'report.md'}")
    return EXIT_BLOCKED if state == "BLOCKED" else EXIT_OK


def _allowed_first(cfg, target_id: str):
    """허용 목록 검사를 대상 파일 읽기보다 먼저 한다."""
    if target_id not in cfg.allowed_targets:
        raise Blocked(StopReason.NOT_ALLOWED,
                      f"'{target_id}'는 [allowed] targets에 없습니다 (허용: {cfg.allowed_targets})")


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    _allowed_first(cfg, args.target)
    spec = load_target(cfg, args.target)
    guard_target(cfg, spec)
    roles = {"대상": cfg.model(spec.model_key), "공격자(WEDGE)": cfg.model(cfg.attacker_model),
             "채점기": cfg.model(cfg.judge_model)}
    print(f"설정: {Path(args.config).resolve()}")
    print(f"대상: {spec.target_id} · 불변조건 {len(spec.invariants)}개 · Oracle {len(spec.oracles)}개 · "
          f"명세 해시 {spec.spec_hash}")
    wedge_sys = (cfg.prompts_dir / "wedge_system.md").read_text(encoding="utf-8")
    judge_sys = (cfg.prompts_dir / "verifier_judge.md").read_text(encoding="utf-8")
    est = {"대상": estimate_input_tokens(spec.prompt_text, "x" * 500),
           "공격자(WEDGE)": estimate_input_tokens(wedge_sys, "x" * 6000),
           "채점기": estimate_input_tokens(judge_sys, "x" * 3000)}
    print("\n역할별 모델과 호출 1회 최대 예약액 (입력 추정 + 출력 상한 전부):")
    need_key = False
    for name, m in roles.items():
        need_key |= m.provider == "anthropic"
        print(f"  {name:<14} {m.key} → {m.provider}:{m.model_id}  ${m.max_cost(est[name]):.4f}"
              f"  (단가 입력 ${m.input_usd_per_mtok}/출력 ${m.output_usd_per_mtok} per MTok; 출처: {m.price_source or '-'})")
    if roles["채점기"].model_id == roles["대상"].model_id:
        print("  ⚠ 채점기와 대상이 같은 모델입니다. 같은 모델의 자기 평가는 독립 검증으로 보지 않습니다 (AIVE §12).")
    turn = sum(roles[n].max_cost(est[n]) for n in roles)
    print(f"\n탐색 1턴(WEDGE 1 + 대상 1 + 채점 1) 최대 예약액 약 ${turn:.4f} · "
          f"1회 상한 ${cfg.limits.max_cost_usd_per_run} · 하루 상한 ${cfg.limits.max_cost_usd_per_day}")
    if need_key:
        print("API 키: " + ("ANTHROPIC_API_KEY 설정됨" if os.environ.get("ANTHROPIC_API_KEY")
                           else "ANTHROPIC_API_KEY 없음 → 실제 실행 전에 설정 필요"))
    conn = connect(cfg.data_dir / "chimera.db")
    b = Budget(conn, "-", cfg.limits)
    print(f"오늘 사용액: ${b.day_committed():.6f}")
    print("\n확인 완료 (API 호출 없음).")
    return EXIT_OK


def _start(args, mode: RunMode) -> int:
    cfg, conn = _open(args)
    _allowed_first(cfg, args.target)
    spec = load_target(cfg, args.target)
    guard_target(cfg, spec)
    Budget(conn, "-", cfg.limits).check_can_start_run()
    run_id = create_run(conn, cfg, spec, mode)
    print(f"실행 시작: {run_id} ({mode.value}, 대상 {spec.target_id})")
    print(f"  멈추기: Ctrl+C · 다른 창에서 `python -m chimera stop {run_id}` · "
          f"또는 {run_dir(cfg, run_id) / 'STOP'} 파일 만들기")
    ctl = Controller(conn, cfg, spec, run_id)
    return _print_result(conn, cfg, run_id, ctl.run())


def cmd_run(args) -> int:
    return _start(args, RunMode.EXPLORE)


def cmd_regress(args) -> int:
    return _start(args, RunMode.REGRESS)


def cmd_resume(args) -> int:
    cfg, conn = _open(args)
    run = conn.execute("SELECT * FROM runs WHERE run_id=?", (args.run_id,)).fetchone()
    if run is None:
        print(f"run이 없습니다: {args.run_id}")
        return EXIT_ERR
    other = conn.execute("SELECT run_id FROM runs WHERE run_state IN ('RUNNING','STOPPING') AND run_id<>?",
                         (args.run_id,)).fetchone()
    if other:
        print(f"다른 실행이 RUNNING 상태입니다: {other[0]}. 먼저 그 실행을 resume 하거나 stop 하세요")
        return EXIT_ERR
    spec = load_target(cfg, run["target_id"])
    info = cleanup_dangling(conn, args.run_id, Budget(conn, args.run_id, cfg.limits))
    print(f"이전 중단 정리: {json.dumps(info, ensure_ascii=False)}")
    ctl = Controller(conn, cfg, spec, args.run_id)
    return _print_result(conn, cfg, args.run_id, ctl.run())


def cmd_stop(args) -> int:
    cfg, conn = _open(args)
    n = conn.execute("UPDATE runs SET stop_requested=1 WHERE run_id=?", (args.run_id,)).rowcount
    if not n:
        print(f"run이 없습니다: {args.run_id}")
        return EXIT_ERR
    (run_dir(cfg, args.run_id) / "STOP").write_text("stop\n", encoding="utf-8")
    print(f"중단 요청을 남겼습니다: {args.run_id}. 진행 중인 호출 1건이 끝나면 멈춥니다.")
    return EXIT_OK


def cmd_status(args) -> int:
    cfg, conn = _open(args)
    if args.run_id:
        r = conn.execute("SELECT * FROM runs WHERE run_id=?", (args.run_id,)).fetchone()
        if r is None:
            print(f"run이 없습니다: {args.run_id}")
            return EXIT_ERR
        cur = json.loads(r["cursor_json"] or "{}")
        b = Budget(conn, args.run_id, cfg.limits)
        print(f"{r['run_id']} · {r['mode']} · {r['run_state']} · {r['stop_reason'] or '-'}")
        print(f"  상세: {r['stop_detail'] or '-'}")
        print(f"  단계 {cur.get('state')} · 불변조건 {cur.get('invariant_id')} · 테스트 {cur.get('tests')}개 · "
              f"비용 ${b.run_settled():.6f}")
        return EXIT_OK
    rows = conn.execute("SELECT run_id, target_id, mode, run_state, stop_reason, started_at FROM runs"
                        " ORDER BY started_at DESC LIMIT 10").fetchall()
    if not rows:
        print("실행 기록이 없습니다.")
    for r in rows:
        print(f"{r['run_id']}  {r['target_id']:<20} {r['mode']:<8} {r['run_state']:<9} "
              f"{r['stop_reason'] or '-':<18} {r['started_at'] or '-'}")
    print(f"오늘 사용액: ${Budget(conn, '-', cfg.limits).day_committed():.6f} / ${cfg.limits.max_cost_usd_per_day}")
    return EXIT_OK


def cmd_report(args) -> int:
    from .report import write_report
    cfg, conn = _open(args)
    if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (args.run_id,)).fetchone() is None:
        print(f"run이 없습니다: {args.run_id}")
        return EXIT_ERR
    print(write_report(conn, cfg, args.run_id))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chimera", description="CHIMERA 단일 WEDGE 루프")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--config", default="config.toml", help="설정 파일 (기본 config.toml)")
        sp.set_defaults(fn=fn)
        return sp

    add("check", cmd_check, "설정·대상·예상 비용 확인 (API 호출 없음)").add_argument("--target", required=True)
    add("run", cmd_run, "탐색 실행").add_argument("--target", required=True)
    add("regress", cmd_regress, "회귀 세트 재실행").add_argument("--target", required=True)
    add("resume", cmd_resume, "멈춘 실행 이어가기").add_argument("run_id")
    add("stop", cmd_stop, "실행 중단 요청").add_argument("run_id")
    add("status", cmd_status, "실행 상태").add_argument("run_id", nargs="?")
    add("report", cmd_report, "리포트 다시 만들기").add_argument("run_id")
    return p


def main(argv=None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, FileNotFoundError) as e:
        print(f"[설정 오류] {e}")
        return EXIT_ERR
    except Blocked as e:
        print(f"[실행 거부] {e.reason}: {e.detail}")
        return EXIT_BLOCKED
    except BudgetExceeded as e:
        print(f"[예산] {e.detail}")
        return EXIT_BLOCKED
