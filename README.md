# chimera-wedge

CHIMERA의 **단일 WEDGE 루프 v0.1**. 승인된 프롬프트 하나를 대상으로, WEDGE가 테스트를 계획하고 → 실행하고 → 판정하고 → 재현으로 검증하고 → 기록한 뒤 → 다음 테스트를 정하는 과정을 끝까지 자동으로 돈다.

```
WEDGE(공격 제안) → EXECUTOR(허용 대상만 호출·예산·중단) → VERIFIER(판정) → CODEX(기록·재현·회귀) → 다음 행동
```

- 처음 설치와 실행 (Windows): [docs/manual/01-설치와-첫-실행.md](docs/manual/01-설치와-첫-실행.md)
- 중단·재개·오류·예산 대응: [docs/manual/02-런북.md](docs/manual/02-런북.md)
- 명령·설정·상태값 찾아보기: [docs/manual/03-찾아보기.md](docs/manual/03-찾아보기.md)
- 동작 설명: [docs/manual/04-동작-설명.md](docs/manual/04-동작-설명.md)

빠른 확인 (실제 API 호출 없음, 비용 0원):

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m chimera run --target mock_demo --config config.mock.toml
```
