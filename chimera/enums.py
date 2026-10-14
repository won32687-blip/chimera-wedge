"""모든 상태값의 유일한 정의 위치 (명세서 3장).

다른 모듈은 상태 문자열을 직접 쓰지 않고 여기 정의된 Enum을 쓴다.
DB의 CHECK 제약도 이 파일의 값으로 만들어진다 (db.py).
"""
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # f-string에서 값만 나오게
        return self.value


class ExecutionStatus(StrEnum):
    """실행 상태 (attempt). 소유: EXECUTOR. '응답을 받았는가'만 뜻한다."""
    NOT_EXECUTED = "NOT_EXECUTED"
    EXECUTED = "EXECUTED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class EvaluationStatus(StrEnum):
    """평가 결과 (evaluation). 소유: VERIFIER. 대상 프롬프트가 테스트 1개에서 규칙을 지켰는가."""
    PASS = "PASS"
    FAIL = "FAIL"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"


class VerificationStatus(StrEnum):
    """검증 상태 (finding). 소유: CODEX. 발견된 실패가 재현으로 확인되었는가."""
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"


class WedgeState(StrEnum):
    """워크플로 단계 (step). WEDGE는 제안만, 확정은 코드(controller)가 한다.

    원본 WEDGE 문서의 RESEED 상태는 NEXT_INVARIANT로 이름만 바꿨다 (명세서 5장).
    'RESEED'는 공통 계약의 '오염 후 재생성' 의미로 예약되어 있고 v0.1에는 없다.
    """
    ACQUIRE = "ACQUIRE"
    PROBE = "PROBE"
    FOCUS = "FOCUS"
    DEEPEN = "DEEPEN"
    SPLIT = "SPLIT"
    VERIFY = "VERIFY"
    NEXT_INVARIANT = "NEXT_INVARIANT"


class NextAction(StrEnum):
    """루프 턴마다 고르는 다음 행동. 상태가 아니라 결정이다."""
    CONTINUE = "CONTINUE"
    FOCUS = "FOCUS"
    DEEPEN = "DEEPEN"
    SPLIT = "SPLIT"
    VERIFY = "VERIFY"
    PIVOT = "PIVOT"
    NEXT_INVARIANT = "NEXT_INVARIANT"
    STOP = "STOP"


class RunState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    BLOCKED = "BLOCKED"


class StopReason(StrEnum):
    USER_STOP = "USER_STOP"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MAX_TESTS = "MAX_TESTS"
    MAX_WALL_TIME = "MAX_WALL_TIME"
    NO_TARGETS_LEFT = "NO_TARGETS_LEFT"
    CONSECUTIVE_ERRORS = "CONSECUTIVE_ERRORS"
    ATTACKER_ERROR = "ATTACKER_ERROR"
    PROVIDER_BLOCKED = "PROVIDER_BLOCKED"
    NOT_ALLOWED = "NOT_ALLOWED"
    COMPLETED = "COMPLETED"  # 회귀 모드가 정해진 재실행을 모두 끝냄


class RunMode(StrEnum):
    EXPLORE = "EXPLORE"
    REGRESS = "REGRESS"


class CoverageTrack(StrEnum):
    """이어하기 실행에서 불변조건별 탐색 방식. run의 cursor(JSON)에만 저장하며 DB 상태 칼럼이 아니다.

    이전 실행(같은 대상·같은 명세 해시)의 판정 기록으로 실행을 만들 때 정한다.
    """
    UNCOVERED = "UNCOVERED"              # 미검사 규칙 우선 탐색: 판정된 테스트가 기준 미만
    DEEPEN_EXISTING = "DEEPEN_EXISTING"  # 기존 규칙 심화 공격: 이미 쓴 공격 계열·입력을 반복하지 않는다


class TestPurpose(StrEnum):
    PROBE = "PROBE"
    REPRO = "REPRO"
    VARIANT = "VARIANT"
    SPLIT = "SPLIT"
    REGRESSION = "REGRESSION"


class TestSet(StrEnum):
    EXPLORATORY = "EXPLORATORY"
    REGRESSION = "REGRESSION"
    HELD_OUT = "HELD_OUT"


class JudgeType(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    RUBRIC_BASED = "RUBRIC_BASED"
    UNKNOWN = "UNKNOWN"


class EvidenceLevel(StrEnum):
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"
    UNKNOWN = "UNKNOWN"


class InvariantSource(StrEnum):
    USER_CONFIRMED = "USER_CONFIRMED"
    SPECIFICATION_DERIVED = "SPECIFICATION_DERIVED"
    ENGINE_INFERRED = "ENGINE_INFERRED"


class CallRole(StrEnum):
    TARGET = "TARGET"
    WEDGE = "WEDGE"
    JUDGE = "JUDGE"


class LedgerKind(StrEnum):
    RESERVE = "RESERVE"
    SETTLE = "SETTLE"
    RELEASE = "RELEASE"


class FindingTestRole(StrEnum):
    ORIGIN = "ORIGIN"
    REPRO = "REPRO"
    VARIANT = "VARIANT"
    REGRESSION = "REGRESSION"


class RegressionOutcome(StrEnum):
    STILL_FAILING = "STILL_FAILING"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    NOT_EXECUTED = "NOT_EXECUTED"
    INCONCLUSIVE = "INCONCLUSIVE"  # FAIL 없음 + 판정 보류·오류가 섞임


# WEDGE 원본 용어 → 공통 표현 (명세서 3장 변환표). 문서·테스트 대조용.
WEDGE_RESULT_MAP = {
    "CORRECT": EvaluationStatus.PASS,
    "INCORRECT": EvaluationStatus.FAIL,
    "INVALID": EvaluationStatus.INVALID,
    "UNRESOLVED": EvaluationStatus.INCONCLUSIVE,
    "NOT_EXECUTED": EvaluationStatus.NOT_RUN,
}
WEDGE_RESULT_TO_VERIFICATION = {"DISPUTED": VerificationStatus.DISPUTED}
WEDGE_STATE_MAP = {"RESEED": WedgeState.NEXT_INVARIANT}

# 테스트를 새로 만들어야 하는 행동 (WEDGE가 test를 함께 내야 함)
TEST_ACTIONS = {NextAction.CONTINUE, NextAction.PIVOT, NextAction.DEEPEN, NextAction.SPLIT}


def values(enum_cls) -> list[str]:
    return [e.value for e in enum_cls]
