"""T-CONTINUE: 예산 소진·중단 뒤 새 실행이 이전 기록을 이어받는다. 이전 기록은 바꾸지 않는다. 실제 API는 부르지 않는다."""
import json

import pytest

from chimera.budget import Budget
from chimera.cli import main
from chimera.config import key_source, load_config, load_target
from chimera.controller import cleanup_dangling, create_run
from chimera.enums import RunMode
from chimera.providers.mock import MockProvider
from chimera.wedge import ProposalError, parse_proposal

from test_loop import StopAfter


def snapshot(w, run_id):
    """한 실행의 테스트·시도·판정·finding 연결 기록 전체."""
    return [w.q("SELECT * FROM tests WHERE run_id=? ORDER BY rowid", (run_id,)),
            w.q("SELECT a.* FROM attempts a JOIN tests t ON t.test_id=a.test_id WHERE t.run_id=? ORDER BY a.rowid",
                (run_id,)),
            w.q("SELECT e.* FROM evaluations e JOIN tests t ON t.test_id=e.test_id WHERE t.run_id=? ORDER BY e.rowid",
                (run_id,)),
            w.q("SELECT * FROM finding_tests WHERE run_id=? ORDER BY rowid", (run_id,))]


def rows(snap):
    return [[tuple(r) for r in part] for part in snap]


def plan_of(w, run_id):
    return json.loads(w.v("SELECT cursor_json FROM runs WHERE run_id=?", (run_id,))).get("plan")


def test_budget_stop_then_new_run_tests_uncovered_first_and_keeps_old_records(world):
    w = world(edit=lambda s: s.replace("max_cost_usd_per_run = 1.0", "max_cost_usd_per_run = 0.06"))
    first = w.controller()
    assert first.run()[1] == "BUDGET_EXHAUSTED"
    before = rows(snapshot(w, first.run_id))
    assert before[0], "첫 실행이 테스트를 1개 이상 만들어야 이어하기를 볼 수 있다"

    w.cfg.limits.max_cost_usd_per_run = 1.0
    second = w.controller()
    plan = plan_of(w, second.run_id)
    assert plan["continued_from"] == [first.run_id]
    # 첫 실행이 손대지 못한 INV-FORMAT은 미검사로 맨 앞
    assert plan["track"]["INV-FORMAT"] == "UNCOVERED" and plan["order"][0] == "INV-FORMAT"
    second.run()
    first_inv = w.v("SELECT invariant_id FROM steps WHERE run_id=? AND wedge_state='ACQUIRE' ORDER BY seq LIMIT 1",
                    (second.run_id,))
    assert first_inv == "INV-FORMAT"
    # 이전 실행의 결과·판정 기록은 그대로
    assert rows(snapshot(w, first.run_id)) == before
    assert "이어하기 계획" in (w.cfg.data_dir / "runs" / second.run_id / "report.md").read_text(encoding="utf-8")


def test_deepen_existing_uses_new_families_and_new_inputs(world):
    w = world(edit=lambda s: s.replace("recent_tests_in_context = 8", "recent_tests_in_context = 8\ncover_min_tests = 1"))
    first = w.controller()
    first.run()
    second = w.controller()
    plan = plan_of(w, second.run_id)
    assert set(plan["track"].values()) == {"DEEPEN_EXISTING"}
    state, reason, _ = second.run()
    assert state == "STOPPED"
    for inv in ("INV-ROLE", "INV-FORMAT"):
        prior_fams = set(plan["prior"][inv]["families"])
        new_fams = {r[0] for r in w.q(
            "SELECT DISTINCT a.attack_family FROM tests t JOIN attacks a ON a.attack_id=t.attack_id"
            " WHERE t.run_id=? AND t.invariant_id=? AND t.purpose='PROBE'", (second.run_id, inv))}
        assert new_fams, f"{inv}: 심화 실행이 테스트를 만들어야 한다"
        assert not (new_fams & prior_fams), f"{inv}: 이전 실행에서 쓴 계열을 다시 썼다 {new_fams & prior_fams}"
        old_inputs = {r[0] for r in w.q("SELECT input_text FROM tests WHERE run_id=? AND invariant_id=?",
                                        (first.run_id, inv))}
        new_inputs = {r[0] for r in w.q("SELECT input_text FROM tests WHERE run_id=? AND invariant_id=?"
                                        " AND purpose IN ('PROBE','VARIANT','SPLIT')", (second.run_id, inv))}
        assert not (old_inputs & new_inputs)


def _ctx(track, prior_families, current_family=None):
    return {"allowed_actions": ["CONTINUE", "PIVOT", "NEXT_INVARIANT"], "current_family": current_family,
            "families_tried": [], "invariant": {"oracle_id": "O-ROLE"},
            "continuation": {"track": track, "prior_families": prior_families}}


def _proposal(action, family, text="새 입력"):
    return json.dumps({"proposed_action": action, "attack_family": family,
                       "tests": [{"purpose": "PROBE", "input_text": text, "oracle_id": "O-ROLE"}]})


def test_deepen_track_rejects_prior_family_and_seen_input(world):
    w = world()
    ctx = _ctx("DEEPEN_EXISTING", ["SOCIAL_PRESSURE"])
    with pytest.raises(ProposalError):
        parse_proposal(_proposal("CONTINUE", "SOCIAL_PRESSURE"), ctx, w.spec)
    with pytest.raises(ProposalError):
        parse_proposal(_proposal("PIVOT", "SOCIAL_PRESSURE"), _ctx("DEEPEN_EXISTING", ["SOCIAL_PRESSURE"], "X"), w.spec)
    assert parse_proposal(_proposal("CONTINUE", "SOCIAL_PRESSURE+AUTHORITY_CLAIM"), ctx, w.spec)
    # 같은 계열을 이어 가는 CONTINUE(계열을 새로 고르는 게 아님)는 막지 않는다
    assert parse_proposal(_proposal("CONTINUE", "X"), _ctx("DEEPEN_EXISTING", ["X"], "X"), w.spec)
    # 미검사 트랙은 이전 계열 재사용을 허용한다
    assert parse_proposal(_proposal("CONTINUE", "SOCIAL_PRESSURE"), _ctx("UNCOVERED", ["SOCIAL_PRESSURE"]), w.spec)
    # 이전 실행과 공백만 다른 같은 입력은 거부
    with pytest.raises(ProposalError):
        parse_proposal(_proposal("CONTINUE", "Y", "새   입력"), _ctx("UNCOVERED", []), w.spec, {"새 입력"})


def test_fresh_and_changed_spec_ignore_history(world, env):
    w = world()
    w.controller().run()
    assert plan_of(w, create_run(w.conn, w.cfg, w.spec, RunMode.EXPLORE, fresh=True)) is None
    w.conn.execute("UPDATE runs SET run_state='STOPPED' WHERE run_state='CREATED'")
    (env / "targets" / "mock_demo_prompt.md").write_text("바뀐 프롬프트", encoding="utf-8")
    changed = load_target(w.cfg, "mock_demo")
    assert changed.spec_hash != w.spec.spec_hash
    assert plan_of(w, create_run(w.conn, w.cfg, changed, RunMode.EXPLORE)) is None


def test_continuation_run_resumes_with_same_plan(world):
    w = world()
    w.controller().run()
    w.mock = StopAfter(w.conn, 2)
    ctl = w.controller()
    plan = plan_of(w, ctl.run_id)
    assert ctl.run()[1] == "USER_STOP"
    w.mock = MockProvider()
    cleanup_dangling(w.conn, ctl.run_id, Budget(w.conn, ctl.run_id, w.cfg.limits))
    state, reason, _ = w.controller(ctl.run_id).run()
    assert state == "STOPPED" and reason != "USER_STOP"
    assert plan_of(w, ctl.run_id) == plan


def test_env_file_key_is_loaded_and_beats_pc_variable(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "pc-variable-dummy")
    (env / ".env").write_text("# 주석\nANTHROPIC_API_KEY=env-file-dummy\nOTHER=1\n", encoding="utf-8")
    load_config(env / "config.toml")
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "env-file-dummy"
    assert "OTHER" not in os.environ
    assert key_source(env) == "env_file"


def test_set_key_hides_input_and_never_prints_value(env, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "  test-key-dummy-123  ")
    (env / ".env").write_text("KEEP=me\nANTHROPIC_API_KEY=old\n", encoding="utf-8")
    assert main(["set-key", "--config", str(env / "config.toml")]) == 0
    out = capsys.readouterr().out
    assert "test-key-dummy-123" not in out
    lines = (env / ".env").read_text(encoding="utf-8").splitlines()
    assert lines == ["KEEP=me", "ANTHROPIC_API_KEY=test-key-dummy-123"]


def test_check_shows_plan_without_calls(env, capsys):
    cfg = str(env / "config.toml")
    assert main(["run", "--target", "mock_demo", "--config", cfg]) == 0
    capsys.readouterr()
    assert main(["check", "--target", "mock_demo", "--config", cfg]) == 0
    out = capsys.readouterr().out
    assert "이어하기: 이전 실행 1개" in out and "API 호출 없음" in out
