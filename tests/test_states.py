"""T-ENUM · T-MAP · T-RESEED · T-PROMOTE: 상태 축 분리가 DB 수준에서 지켜지는지."""
import sqlite3

import pytest

from chimera.db import connect
from chimera.enums import (EvaluationStatus, VerificationStatus, WEDGE_RESULT_MAP, WEDGE_STATE_MAP,
                           WedgeState)


@pytest.fixture
def conn():
    c = connect(":memory:")
    c.execute("INSERT INTO runs(run_id,target_id,spec_hash,mode,run_state,limits_json) VALUES ('r','t','h','EXPLORE','CREATED','{}')")
    c.execute("INSERT INTO tests(test_id,run_id,invariant_id,purpose,oracle_id,test_set,created_at) VALUES ('t1','r','I','PROBE','O','EXPLORATORY','x')")
    return c


def test_execution_status_rejects_evaluation_values(conn):  # T-ENUM
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO attempts(attempt_id,test_id,idempotency_key,attempt_no,target_spec_hash,execution_status)"
                     " VALUES ('a','t1','k',1,'h','PASS')")


def test_evaluation_status_rejects_verification_values(conn):  # T-ENUM
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO evaluations(evaluation_id,test_id,evaluation_status,oracle_id,judge_type,evidence_level,created_at)"
                     " VALUES ('e','t1','VERIFIED','O','RUBRIC_BASED','E1','x')")


def test_fail_requires_violated_condition(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO evaluations(evaluation_id,test_id,evaluation_status,oracle_id,judge_type,evidence_level,created_at)"
                     " VALUES ('e','t1','FAIL','O','RUBRIC_BASED','E1','x')")


def test_only_verified_can_enter_regression_set(conn):  # T-PROMOTE
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO findings(finding_id,target_id,fingerprint,invariant_id,violated_condition_id,first_test_id,"
                     "verification_status,in_regression_set,created_at,updated_at) VALUES ('f','t','fp','I','R','t1','PENDING',1,'x','x')")


def test_wedge_vocabulary_map():  # T-MAP
    assert WEDGE_RESULT_MAP["CORRECT"] == EvaluationStatus.PASS
    assert WEDGE_RESULT_MAP["INCORRECT"] == EvaluationStatus.FAIL
    assert WEDGE_RESULT_MAP["UNRESOLVED"] == EvaluationStatus.INCONCLUSIVE
    assert WEDGE_RESULT_MAP["NOT_EXECUTED"] == EvaluationStatus.NOT_RUN
    assert "DISPUTED" not in WEDGE_RESULT_MAP  # DISPUTED는 평가 결과가 아니라 검증 상태
    assert VerificationStatus("DISPUTED")


def test_reseed_renamed_without_behavior_change():  # T-RESEED
    assert WEDGE_STATE_MAP["RESEED"] == WedgeState.NEXT_INVARIANT
    assert "RESEED" not in [s.value for s in WedgeState]
