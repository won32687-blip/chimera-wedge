"""공통 준비: 임시 폴더에 Mock 설정·대상·프롬프트를 복사해 테스트마다 새 DB로 돈다. 실제 API는 부르지 않는다."""
import shutil
from pathlib import Path

import pytest

from chimera.config import load_config, load_target
from chimera.controller import Controller, create_run
from chimera.db import connect
from chimera.enums import RunMode
from chimera.providers.mock import MockProvider

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    shutil.copy(ROOT / "config.mock.toml", tmp_path / "config.toml")
    shutil.copytree(ROOT / "targets", tmp_path / "targets")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    return tmp_path


class World:
    def __init__(self, root: Path, script=None, edit=None):
        cfg_path = root / "config.toml"
        if edit:
            cfg_path.write_text(edit(cfg_path.read_text(encoding="utf-8")), encoding="utf-8")
        self.cfg = load_config(cfg_path)
        self.spec = load_target(self.cfg, "mock_demo")
        self.conn = connect(self.cfg.data_dir / "chimera.db")
        self.mock = MockProvider(script)
        self.sleeps = []

    def controller(self, run_id=None, mode=RunMode.EXPLORE):
        run_id = run_id or create_run(self.conn, self.cfg, self.spec, mode)
        return Controller(self.conn, self.cfg, self.spec, run_id, providers={"mock": self.mock},
                          sleep=self.sleeps.append, install_sigint=False)

    def q(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def v(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()[0]


@pytest.fixture
def world(env):
    return lambda script=None, edit=None: World(env, script, edit)
