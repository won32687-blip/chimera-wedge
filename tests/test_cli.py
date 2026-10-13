"""명령줄: check는 API를 부르지 않고, run·stop·status·report가 Mock 설정으로 끝까지 돈다."""
from chimera.cli import main


def test_cli_end_to_end(env, capsys, monkeypatch):
    cfg = str(env / "config.toml")
    assert main(["check", "--target", "mock_demo", "--config", cfg]) == 0
    assert "API 호출 없음" in capsys.readouterr().out
    assert main(["run", "--target", "mock_demo", "--config", cfg]) == 0
    out = capsys.readouterr().out
    assert "NO_TARGETS_LEFT" in out
    run_id = out.split("실행 시작: ")[1].split(" ")[0]
    assert main(["status", run_id, "--config", cfg]) == 0
    assert main(["report", run_id, "--config", cfg]) == 0
    assert main(["stop", run_id, "--config", cfg]) == 0
    assert main(["regress", "--target", "mock_demo", "--config", cfg]) == 0
    assert "COMPLETED" in capsys.readouterr().out


def test_cli_refuses_target_with_empty_prompt(env, capsys):
    cfg = env / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('targets = ["mock_demo"]',
                                                            'targets = ["mock_demo", "devils_advocate"]'),
                   encoding="utf-8")
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('[roles]', '[models.sonnet-5-5]\nprovider = "mock"\n'
                                                            'model_id = "mock-target"\nmax_tokens = 100\n\n[roles]'),
                   encoding="utf-8")
    assert main(["check", "--target", "devils_advocate", "--config", str(cfg)]) == 1
    assert "비어 있습니다" in capsys.readouterr().out


def test_cli_refuses_unapproved_target(env, capsys):
    (env / "targets" / "devils_advocate_prompt.md").write_text("프롬프트", encoding="utf-8")
    assert main(["run", "--target", "devils_advocate", "--config", str(env / "config.toml")]) == 2
    assert "NOT_ALLOWED" in capsys.readouterr().out
