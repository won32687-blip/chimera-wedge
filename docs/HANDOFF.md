# 인계 문서

새 대화·새 세션·다른 코딩 AI가 이어받을 때 **이것 하나만 읽으면** 이어서 일할 수 있게 유지한다. 작업 단위가 끝날 때마다 갱신한다.

## 1. 한 줄 요약
CHIMERA 단일 WEDGE 루프 v0.1. Mock으로 전체 루프 검증 완료, 실제 Claude API 시험 전.

## 2. 먼저 읽을 것
1. `CLAUDE.md` 작업 규칙
2. `docs/state/PROJECT_STATE.md` 진행 상태·확정 결정
3. 명세서 v0.1: https://claude.ai/code/artifact/6b52cc04-9e48-4462-be0c-761f2fe881a4

## 3. 지금 상태 (2026-10-13)
- 브랜치·PR: 첫 PR(v0.1 구현) 검토 대기
- 마지막 확인: `python -m pytest -q` → 57 passed (실행 기록)
- 실제 API 호출: 아직 0회

## 4. 사용자가 해 줄 것
- Devil's Advocate 프롬프트 원문 제공
- `ANTHROPIC_API_KEY` 설정 (매뉴얼 1-4)

## 5. 주의 (실수하기 쉬운 곳)
- 실제 API 2회 이상 실행은 예상 비용 보고 후 승인 (CLAUDE.md 4절).
- 상태 축(실행/평가/검증/단계)을 섞지 않는다. 새 상태값은 `enums.py`에만.
- 채점기 근거 검사(인용문이 실제 응답에 있어야 FAIL)를 약하게 바꾸지 않는다.
- `prompts/source/`는 원본 보존용. 수정은 `prompts/wedge_system.md`에서.

## 6. 열린 질문
- `docs/state/backlog.md` "결정 대기" 참고.
