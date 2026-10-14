# 진행 상태

마지막 갱신: 2026-10-14

## 지금 단계
**v0.1 단일 WEDGE 루프: 구현 완료, Mock 검증 완료, 실제 모델 시험 전.**

## 확정된 결정 (사용자 확정 2026-10-13)
1. 첫 대상: 사용자가 작성한 Strategic Devil's Advocate 프롬프트. 목표는 공격 성능이 아니라 **루프 정상 작동 검증**. Mock 먼저, 그다음 실제 모델.
2. 비용 상한: 1회 $1, 하루 $3 (설정값). 초과 예상 시 다음 호출 차단.
3. Claude API 사용. 공격자·채점기 논리 분리. Provider 인터페이스로 모델 비종속.
4. LLM 채점기 허용. 응답에 없는 근거·불명확한 위반은 확정하지 않음.
5. GitHub 저장소 기반 개발, 최종 실행은 Windows PC.
6. AIVE 대비 VERIFIER 누락 방지 ([verifier-coverage.md](../verifier-coverage.md)). 다중엔진 개발 금지. 실행 가능성 우선.

## 완료
- 명세서 v0.1, 정합성 보고서 (링크: [docs/README.md](../README.md))
- 코드: 루프 제어·판정·기록·재현·회귀·예산·중단·재개·CLI (실행 기록: `pytest` 57개 통과, Mock run `NO_TARGETS_LEFT`, VERIFIED 2개, regress `STILL_FAILING` 2개)

## 다음
1. ~~Devil's Advocate 프롬프트 원문 받기~~ → v1.2 수령(2026-10-14), `targets/devils_advocate_prompt.md` (원본 보존: `targets/source/`)
2. 불변조건 4개 수정안(`targets/devils_advocate.toml`, ENGINE_INFERRED) 사용자 확정 대기
3. 단가 공식 페이지 재확인 (미확인)
4. 실제 API 1회 시험 호출 → 정상이면 예상 비용 보고 후 탐색 run 1회 (승인 필요)

## 기본값으로 정한 것 (바꿀 수 있음)
- 모델: 대상·WEDGE = `claude-sonnet-5-5`, 채점기 = `claude-opus-5-5`(effort low). `config.toml [roles]`에서 변경.
- 집중 탐색 비율 상한 = 테스트 상한(`max_tests_per_run`) 대비 50%로 해석 (명세 문구는 "실행 테스트 대비"였으나 그대로면 VERIFY 직후 DEEPEN이 막힘).
