"""T-REPRO · T-DEDUP: 재현 판정과 중복 제거."""
import pytest

from chimera.codex import fingerprint, judge_reproduction, link_fail
from chimera.db import connect
from chimera.enums import FindingTestRole


@pytest.fixture
def conn():
    c = connect(":memory:")
    c.execute("INSERT INTO runs(run_id,target_id,spec_hash,mode,run_state,limits_json) VALUES ('r','T','h','EXPLORE','CREATED','{}')")
    for i in range(1, 8):
        c.execute("INSERT INTO tests(test_id,run_id,invariant_id,purpose,input_text,oracle_id,test_set,created_at)"
                  " VALUES (?, 'r','I','PROBE',?, 'O','EXPLORATORY','x')", (f"t{i}", "x" * (10 - i)))
    return c


def T(i):
    return {"test_id": f"t{i}", "invariant_id": "I", "input_text": "x" * (10 - i)}


def test_fingerprint_ignores_tag_case_and_space():
    assert fingerprint("T", "I", "R", " Tag ") == fingerprint("T", "I", "R", "tag")
    assert fingerprint("T", "I", "R", "a") != fingerprint("T", "I", "R", "b")


def test_same_fingerprint_links_without_new_unique_failure(conn):
    f1, new1 = link_fail(conn, "r", "T", T(1), "R", "tag", FindingTestRole.ORIGIN)
    f2, new2 = link_fail(conn, "r", "T", T(2), "R", "TAG", FindingTestRole.VARIANT)
    assert new1 and not new2 and f1 == f2
    row = conn.execute("SELECT observations, minimal_test_id FROM findings").fetchone()
    assert row[0] == 2 and row[1] == "t2"  # 더 짧은 입력이 최소 실패 입력이 됨


def test_different_tag_is_new_finding_with_duplicate_candidate(conn):
    f1, _ = link_fail(conn, "r", "T", T(1), "R", "a", FindingTestRole.ORIGIN)
    f2, new = link_fail(conn, "r", "T", T(2), "R", "b", FindingTestRole.ORIGIN)
    assert new and f1 != f2
    assert conn.execute("SELECT duplicate_of FROM findings WHERE finding_id=?", (f2,)).fetchone()[0] == f1


@pytest.mark.parametrize("results,expected,in_set", [
    ([("t2", "FAIL", "R"), ("t3", "PASS", None), ("t4", "PASS", None)], "VERIFIED", 1),
    ([("t2", "PASS", None), ("t3", "INCONCLUSIVE", None), ("t4", "NOT_RUN", None)], "DISPUTED", 0),
    ([("t2", "INVALID", None), ("t3", "INVALID", None), ("t4", "NOT_RUN", None)], "REJECTED", 0),
    ([("t2", "INCONCLUSIVE", None), ("t3", "NOT_RUN", None), ("t4", "NOT_RUN", None)], "PENDING", 0),
    ([("t2", "FAIL", "OTHER"), ("t3", "PASS", None), ("t4", "PASS", None)], "DISPUTED", 0),
])
def test_reproduction_rules(conn, results, expected, in_set):
    fid, _ = link_fail(conn, "r", "T", T(1), "R", "tag", FindingTestRole.ORIGIN)
    assert judge_reproduction(conn, "r", fid, results) == expected
    assert conn.execute("SELECT in_regression_set FROM findings").fetchone()[0] == in_set
