# 미룬 결정·할 일

정할 시점이 오면 꺼내서 사용자에게 묻는다.

## 결정 대기
- [ ] Devil's Advocate 불변조건·판정 조건 확정 (프롬프트 원문 받은 뒤)
- [ ] 실제 실행 후 단가·상한 조정 여부 (1회 $1 / 하루 $3)
- [ ] 공격자 모델을 Opus로 올릴지 (공격 성능 평가 단계에서)

## 기능 (v0.1 범위 밖)
- [ ] 최종 완료 판정 (AIVE §21 PASS / CONDITIONAL_PASS / FAIL / NOT_VERIFIED / BLOCKED)
- [ ] Oracle 유형 FORMAL·EMPIRICAL, 파이썬 검사 함수 Oracle
- [ ] 실패 분류 칼럼(AIVE §15), 증거 수준 E2~E4 기록
- [ ] 심각도(severity) 판정 — 칼럼만 있고 비어 있음
- [ ] 여러 턴 대화형 테스트 (지금은 단일 메시지)
- [ ] 정합성 보고서의 나머지 항목 (HANDOFF RESEED 오염 재생성, BEAST CORE 연동 등)
- [ ] 평가 지표 계산·버전 비교 (AIVE §17·18)

## 작업 방식 (SpecProof에서 가져올 후보)
- [ ] Gemini 교차 모델 PR 리뷰 (GitHub Secret `GEMINI_API_KEY` 필요)
- [ ] `docs/decisions/` (결정 기록), `docs/incidents/` (사고 기록) 폴더 — 첫 사례가 생기면 만든다
