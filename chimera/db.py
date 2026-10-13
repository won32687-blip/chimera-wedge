"""SQLite 저장소 (명세서 7장). 상태 칼럼은 CHECK 제약으로 다른 축의 값을 거부한다."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .enums import (
    CallRole, EvaluationStatus, EvidenceLevel, ExecutionStatus, FindingTestRole,
    InvariantSource, JudgeType, LedgerKind, NextAction, RunMode, RunState,
    TestPurpose, TestSet, VerificationStatus, WedgeState, values,
)

SCHEMA_VERSION = 1


def _in(col: str, enum_cls, nullable: bool = False) -> str:
    vals = ",".join(f"'{v}'" for v in values(enum_cls))
    cond = f"{col} IN ({vals})"
    return f"CHECK ({col} IS NULL OR {cond})" if nullable else f"CHECK ({cond})"


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS targets (
  target_id TEXT NOT NULL, spec_hash TEXT NOT NULL,
  prompt_hash TEXT NOT NULL, model_key TEXT NOT NULL,
  spec_json TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (target_id, spec_hash));

CREATE TABLE IF NOT EXISTS invariants (
  invariant_id TEXT NOT NULL, target_id TEXT NOT NULL, spec_hash TEXT NOT NULL,
  description TEXT NOT NULL, oracle_id TEXT NOT NULL,
  source TEXT NOT NULL {_in('source', InvariantSource)},
  PRIMARY KEY (invariant_id, target_id, spec_hash));

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, spec_hash TEXT NOT NULL,
  mode TEXT NOT NULL {_in('mode', RunMode)},
  run_state TEXT NOT NULL {_in('run_state', RunState)},
  stop_reason TEXT, stop_detail TEXT, stop_requested INTEGER NOT NULL DEFAULT 0,
  limits_json TEXT NOT NULL, cursor_json TEXT,
  wedge_model TEXT, judge_model TEXT,
  started_at TEXT, ended_at TEXT);

CREATE TABLE IF NOT EXISTS steps (
  step_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs,
  seq INTEGER NOT NULL,
  wedge_state TEXT NOT NULL {_in('wedge_state', WedgeState)},
  invariant_id TEXT, attack_family TEXT, finding_id TEXT,
  allowed_actions TEXT NOT NULL,
  proposed_action TEXT,
  applied_action TEXT {_in('applied_action', NextAction, nullable=True)},
  override_reason TEXT,
  created_at TEXT NOT NULL, UNIQUE (run_id, seq));

CREATE TABLE IF NOT EXISTS attacks (
  attack_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
  invariant_id TEXT NOT NULL, attack_family TEXT NOT NULL,
  hypothesis TEXT NOT NULL, mechanism_tag TEXT, rationale TEXT,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tests (
  test_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs,
  attack_id TEXT REFERENCES attacks,
  step_id INTEGER REFERENCES steps, parent_test_id TEXT REFERENCES tests,
  invariant_id TEXT NOT NULL,
  purpose TEXT NOT NULL {_in('purpose', TestPurpose)},
  input_text TEXT, expected_behavior TEXT, forbidden_behavior TEXT,
  oracle_id TEXT NOT NULL,
  test_set TEXT NOT NULL {_in('test_set', TestSet)},
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY, test_id TEXT NOT NULL REFERENCES tests,
  idempotency_key TEXT NOT NULL UNIQUE,
  attempt_no INTEGER NOT NULL, target_spec_hash TEXT NOT NULL,
  execution_status TEXT NOT NULL {_in('execution_status', ExecutionStatus)},
  response_text TEXT, stop_reason TEXT, error_type TEXT, error_detail TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  reserved_usd REAL NOT NULL DEFAULT 0, cost_usd REAL,
  started_at TEXT, finished_at TEXT);

CREATE TABLE IF NOT EXISTS evaluations (
  evaluation_id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts,
  test_id TEXT NOT NULL REFERENCES tests,
  evaluation_status TEXT NOT NULL {_in('evaluation_status', EvaluationStatus)},
  oracle_id TEXT NOT NULL,
  judge_type TEXT NOT NULL {_in('judge_type', JudgeType)},
  violated_condition_id TEXT, evidence_quote TEXT, reason TEXT,
  evidence_level TEXT NOT NULL {_in('evidence_level', EvidenceLevel)},
  judge_cost_usd REAL, created_at TEXT NOT NULL,
  CHECK (evaluation_status <> 'FAIL' OR violated_condition_id IS NOT NULL));

CREATE TABLE IF NOT EXISTS findings (
  finding_id TEXT PRIMARY KEY, target_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL, invariant_id TEXT NOT NULL,
  violated_condition_id TEXT NOT NULL, mechanism_tag TEXT,
  first_test_id TEXT NOT NULL REFERENCES tests, minimal_test_id TEXT REFERENCES tests,
  verification_status TEXT NOT NULL {_in('verification_status', VerificationStatus)},
  repro_fail INTEGER NOT NULL DEFAULT 0, repro_total INTEGER NOT NULL DEFAULT 0,
  observations INTEGER NOT NULL DEFAULT 1,
  severity TEXT CHECK (severity IS NULL OR severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
  duplicate_of TEXT REFERENCES findings,
  in_regression_set INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK (in_regression_set = 0 OR verification_status = 'VERIFIED'));

CREATE TABLE IF NOT EXISTS finding_tests (
  finding_id TEXT NOT NULL REFERENCES findings, test_id TEXT NOT NULL REFERENCES tests,
  role TEXT NOT NULL {_in('role', FindingTestRole)},
  run_id TEXT,
  PRIMARY KEY (finding_id, test_id));

CREATE TABLE IF NOT EXISTS budget_ledger (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL, attempt_id TEXT,
  kind TEXT NOT NULL {_in('kind', LedgerKind)},
  call_role TEXT NOT NULL {_in('call_role', CallRole)},
  usd REAL NOT NULL, created_at TEXT NOT NULL, day TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
  entity TEXT NOT NULL, entity_id TEXT NOT NULL, field TEXT,
  old_value TEXT, new_value TEXT, detail TEXT, created_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_attempts_test ON attempts(test_id);
CREATE INDEX IF NOT EXISTS idx_eval_test ON evaluations(test_id);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON budget_ledger(run_id);
CREATE INDEX IF NOT EXISTS idx_ledger_day ON budget_ledger(day);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_day() -> str:
    """하루 예산은 사용자 PC의 현지 날짜 기준."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    return conn


class Tx:
    """BEGIN IMMEDIATE 트랜잭션. 예산 예약과 상태 변경을 원자적으로 묶는다."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def event(conn, run_id, entity, entity_id, field=None, old=None, new=None, detail=None):
    conn.execute(
        "INSERT INTO events(run_id, entity, entity_id, field, old_value, new_value, detail, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (run_id, entity, entity_id, field,
         None if old is None else str(old), None if new is None else str(new),
         None if detail is None else (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)),
         now()),
    )


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def all_rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()
