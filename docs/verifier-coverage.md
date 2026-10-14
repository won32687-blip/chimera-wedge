# AIVE → VERIFIER 기능 대조 (v0.1)

사용자 조건 6 "AIVE와 VERIFIER의 역할 차이로 실제 검증 기능이 누락되지 않도록"에 대한 대조표.
v0.1의 VERIFIER = AIVE **§11 TRACK 2 행동 검증 + §12 ANSWER ORACLE**의 최소 구현. 나머지는 의도적으로 미룬 것이며 아래에 이유와 함께 적는다.

| AIVE 항목 | v0.1 | 구현 위치 / 미룬 이유 |
|---|---|---|
| §11 테스트 필수 정보 (입력·기대·금지 행동·실제 응답·실행 상태·증거 수준) | ✅ | `tests`·`attempts`·`evaluations` 테이블 |
| §11 응답 없음 → NOT_RUN | ✅ | `verifier.py` 판정 순서 1 · 테스트 `test_not_executed_is_not_run` |
| §11 증거 부족 → INCONCLUSIVE | ✅ | `interpret_judge` · 테스트 `test_unclear_and_garbage_stay_inconclusive` |
| §11 실제 위반 확인 → FAIL | ✅ | 위반 조건 ID + 실제 응답 인용 필수 (DB CHECK 포함) |
| §11 테스트 PASS ≠ 전체 평가 PASS | ✅ | 리포트에 전체 PASS를 내지 않음 (§21 미구현과 연결) |
| §12 정답 기준 사전 고정 | ✅ | `targets/*.toml`의 Oracle, 명세 해시로 고정 |
| §12 정답 유형 | 부분 | DETERMINISTIC·RUBRIC_BASED·UNKNOWN만. FORMAL·EMPIRICAL은 backlog |
| §12 수용·거부 조건, ORACLE_ID | ✅ | `acceptance`·`rejection`·`checks` |
| §12 기준 불명확하면 임의 PASS/FAIL 금지 | ✅ | UNKNOWN Oracle → INCONCLUSIVE |
| §12 정답 기준 오류 ≠ 대상 실패 | ✅ | Oracle 오류 → INVALID, INVALID 2연속 시 `oracle check_needed` 이벤트 후 다음 불변조건 |
| §12 기준 변경 시 전후 결과 혼합 금지 | ✅ | 명세 해시가 바뀌면 resume 거부, 새 run |
| §12 같은 모델의 자기 평가 ≠ 독립 검증 | ✅ | 기본 설정에서 채점기(Opus)와 대상(Sonnet) 분리, `check`가 같은 모델이면 경고 |
| §16 증거 수준 E1~E4 | 부분 | 직접 실행 결과는 E1로 기록. E2~E4는 v0.1에서 생성되지 않음 |
| §15 실패 분류 | 부분 | CONFIRMED ≈ VERIFIED, HYPOTHESIZED ≈ PENDING, FALSE_POSITIVE ≈ DISPUTED/REJECTED 로 대응. 별도 칼럼은 backlog |
| §19 평가자 자기 감사 | 부분 | 중복 계산 방지(fingerprint), 근거 날조 차단, 미검사 영역 리포트 표시. 오판 사례 집계는 backlog |
| §20 승인 범위 실행 | ✅ | `[allowed] targets`, 예산·횟수·시간 상한, 중단 3종 |
| §21 최종 완료 판정 (PASS/CONDITIONAL_PASS/…) | ❌ | v0.1 목표는 루프 동작 검증. 리포트는 수치만 낸다 → backlog |
| §6~10 TRACK 1 공격 능력 평가, WEDGE·CAVALRY 능력 평가 | ❌ | 다중엔진 범위. 개발 금지 조건(사용자 확정) |
| §13~14 적응형 평가 제어·정보 이득 | 대체 | WEDGE 루프 제어(`controller.py`)가 담당 |
| §17 평가 지표 · §18 기준선·버전 비교 | 부분 | 회귀 모드(`regress`)로 수정 전후 재실행만. 지표 계산은 backlog |
