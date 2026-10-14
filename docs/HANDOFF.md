# 인계 문서

새 대화·새 세션·다른 코딩 AI가 이어받을 때 **이것 하나만 읽으면** 이어서 일할 수 있게 유지한다. 작업 단위가 끝날 때마다 갱신한다.

## 1. 한 줄 요약
CHIMERA 단일 WEDGE 루프 v0.1. Mock으로 전체 루프 검증 완료. 첫 대상 프롬프트(Devil's Advocate v1.2) 수령, 불변조건 4개 확정, 실제 Claude API 실행 3회(실패 발견 0개).

## 2. 먼저 읽을 것
1. `CLAUDE.md` 작업 규칙
2. `docs/state/PROJECT_STATE.md` 진행 상태·확정 결정
3. 명세서 v0.1: https://claude.ai/code/artifact/6b52cc04-9e48-4462-be0c-761f2fe881a4

## 3. 지금 상태 (2026-10-15)
- 병합됨: PR #1(v0.1 구현), PR #2(문서 재작성), PR #3(대상 프롬프트·불변조건)
- 검토 중: `feat/run-continuation` (실행 이어짐 개선 + 키 `.env` 전환). Mock 검증만, 실제 호출 없음. `main` 병합은 사용자가 정한다
- 마지막 확인: `python -m pytest -q` → 65 passed (실행 기록, 2026-10-15)
- 폴더 위치: `C:\Users\won03\dev\chimera-wedge` (2026-10-14 이동). 바탕화면에 바로가기
- 실제 API 실행 3회 (devils_advocate, 2026-10-14):
  - run_7bbf41af0b28: BLOCKED, WEDGE 모델 401(인증·모델 오류), 비용 $0
  - run_5584ce915db2: STOPPED, 1회 상한 $1 도달, 9 PASS / 0 FAIL, $0.92
  - run_14e0d7b56b9f: STOPPED, 1회 상한 $1 도달, 12 PASS / 0 FAIL, $0.90
  - 발견된 실패 0개. 두 실행이 같은 순서·같은 공격 계열을 반복했고 NOFAB는 매번 1개만 시험 → 이어하기 개선의 근거
- 이어하기 미리 보기(`check`, 호출 없음): 다음 run은 NOFAB(미검사 우선) → HOLD·BOUNDARY·PRAISE(기존 규칙 심화) 순

## 4. 사용자가 해 줄 것
- PR 검토 후 `main` 병합 여부 결정
- `python -m chimera set-key`로 키를 프로젝트 `.env`에 저장하고, 이전 `setx` 환경 변수 삭제 (사용자 가이드 'API 키 설정')
- 연습용 프롬프트 검증·규칙 분리 제안에 대한 결정 (backlog '결정 대기')

## 5. 주의 (실수하기 쉬운 곳)
- 실제 API 2회 이상 실행은 예상 비용 보고 후 승인 (CLAUDE.md 4절).
- 상태 축(실행/평가/검증/단계)을 섞지 않는다. 새 상태값은 `enums.py`에만.
- 채점기 근거 검사(인용문이 실제 응답에 있어야 FAIL)를 약하게 바꾸지 않는다.
- `prompts/source/`는 원본 보존용. 수정은 `prompts/wedge_system.md`에서.

## 6. 열린 질문
- `docs/state/backlog.md` "결정 대기" 참고.
