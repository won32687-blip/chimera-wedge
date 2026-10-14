# chimera-wedge 작업 규칙 (Claude·Codex 등 코딩 AI 공통)

사용자는 코드를 직접 쓰지 않고, **실행 결과와 리포트를 보며 흐름을 이해하고 결정**한다. 아래 규칙은 그 흐름을 사용자가 계속 따라올 수 있게 하기 위한 것이다. (SpecProof 작업 규칙에서 이 프로젝트에 맞는 것만 가져왔다.)

## 1. 대화 규칙
- 한국어로 답한다. 결론 먼저. 전문 용어는 처음 나올 때 한 줄로 풀어 쓴다.
- **수정하기 전에** 이번 변경이 `WEDGE(공격 제안) → EXECUTOR(실행·예산·중단) → VERIFIER(판정) → CODEX(기록·재현·회귀)` 중 어디에 해당하는지 먼저 말한다.
- **수정한 뒤에는** 어떤 파일을 왜 바꿨는지 설명하고, 사용자가 직접 확인할 방법(어떤 명령을 실행해 리포트의 어디를 보면 되는지)을 적는다.
- 사용자 가이드(`docs/guide/`)를 쓰거나 고칠 때는 [docs/문서-작성-원칙.md](docs/문서-작성-원칙.md)를 따른다. 운영·개발 문서(`docs/dev/`, 이 파일, 상태 문서)는 명령어·체크리스트를 써도 된다.
- 근거 표시: (사용자 확정) (실행 기록) (코드 확인) (AI 생성, 미검증) (미확인). 실행하지 않은 것을 됐다고 말하지 않는다.

## 2. 고정 제약 (명세서 v0.1)
- 범위는 **단일 WEDGE 루프**다. 다중엔진(COMMANDER·CAVALRY·HANDOFF 전체 등)은 만들지 않는다. 필요해 보이면 `docs/state/backlog.md`에 적는다.
- 상태값은 `chimera/enums.py`에만 정의한다. 실행 상태 · 평가 결과 · 검증 상태 · 워크플로 단계는 서로 다른 축이며 섞지 않는다 (DB CHECK로도 강제).
- PASS/FAIL은 "대상 프롬프트가 테스트 1개에서 규칙을 지켰는가"만 뜻한다. 공격 성공 = 대상 FAIL.
- LLM 채점기는 응답에 없는 근거를 인용하거나 위반이 불명확하면 확정하지 않는다(INCONCLUSIVE). 이 검사를 약하게 바꾸지 않는다.
- 모델 이름·단가·키를 코드에 박지 않는다. 모델과 단가는 `config.toml`의 모델 등록표, 키는 `ANTHROPIC_API_KEY` 하나이며 프로젝트 `.env`에만 둔다(`python -m chimera set-key`, PC 전체 환경 변수·`setx` 금지). 코딩 AI는 `.env`를 열어 읽지 않고, 키 값은 화면·로그·DB에 출력하지 않는다(있다/없다만 확인). `.env`·키를 커밋하지 않는다.
- 승인된 대상(`[allowed] targets`)만 실행한다. 예산 상한(1회 $1, 하루 $3)은 설정값이며 호출 전에 예약으로 강제한다.

## 3. 작업 순서
1. `main`을 최신으로 받고 새 브랜치를 만든다.
2. 수정 → `python -m pytest -q` 실행 → 결과 원문을 사용자에게 보여 준다. 루프 동작을 바꿨다면 Mock 실행(`python -m chimera run --target mock_demo --config config.mock.toml`)의 리포트 요약도 보여 준다.
3. PR을 만든다. CI(Ubuntu·Windows 테스트)를 통과해야 한다. **`main` 병합은 사용자만 한다.**
   - PR 설명 맨 위에 **흐름 카드**: ① 바뀐 곳 (`WEDGE / EXECUTOR / VERIFIER / CODEX / CLI·문서` 중) ② 한 줄 흐름 그림 ③ 사용자가 볼 곳 (명령·리포트 위치)
   - 그 아래 `사람 확인 필요: 예 / 아니오`와 이유.
   - **"사람이 직접 확인할 것 3가지"** (실행할 명령과 확인할 값)를 반드시 적는다.
4. 워크플로(`.github/workflows/`)를 바꾸는 PR은 ⚠️로 따로 표시한다.

## 4. 자동으로 진행 / 멈추고 보고
- **확인 없이 진행**: 테스트 실패 수정 후 같은 브랜치에 다시 푸시, Mock 실행, 병합된 브랜치 정리.
- **실제 API 비용이 드는 실행은 1회 시험 호출까지만** 자동으로 한다. 2회 이상(탐색 run 등)은 `python -m chimera check`의 예상 비용을 먼저 보고하고 승인을 받는다.
- **멈추고 보고**: 상태 축·판정 규칙·재현 규칙 변경, DB 스키마 변경, 예산·중단 로직 약화, 대상 명세의 불변조건·판정 조건 변경(사용자 확정 사항), 워크플로 변경.

## 5. 구조
- `chimera/controller.py` 루프 제어 · `wedge.py` WEDGE 호출·출력 검사 · `executor.py` 모든 모델 호출(허용 검사·예산·재시도·중단) · `verifier.py` 판정 · `codex.py` 기록·중복·재현·ContextSlice · `budget.py` · `stopper.py` · `db.py` · `report.py` · `cli.py`
- `providers/` 모델 교체 계층 (`anthropic`, `mock`) · `prompts/` WEDGE·채점기 프롬프트 (`source/`는 원본 보존) · `targets/` 대상 명세
- 명세서 v0.1과 정합성 보고서 링크: [docs/README.md](docs/README.md)

## 6. 상태 문서 (기록의 원본)
- 진행 상태: [docs/state/PROJECT_STATE.md](docs/state/PROJECT_STATE.md) — 새 작업을 시작할 때 먼저 읽는다
- 미룬 결정·할 일: [docs/state/backlog.md](docs/state/backlog.md)
- 인계: [docs/HANDOFF.md](docs/HANDOFF.md) — 대화·세션이 바뀔 때 갱신한다
- 상태 문서와 실제(코드·GitHub)가 다르면 고치지 말고 보고한다.
