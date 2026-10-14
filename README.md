# chimera-wedge

직접 작성한 프롬프트가 공격적인 입력에도 규칙을 지키는지 자동으로 시험하는 프로그램입니다. 공격 AI가 시험 입력을 만들고, 대상 프롬프트의 응답을 별도의 채점 AI가 판정하며, 규칙 위반이 보이면 다시 시험해 재현되는지 확인한 뒤 결과를 리포트로 남깁니다. CHIMERA의 단일 WEDGE 루프 v0.1입니다.

- 사용자 가이드: [docs/guide/사용자-가이드.md](docs/guide/사용자-가이드.md)
- 설정과 상태값 참고: [docs/guide/참고-설정과-상태값.md](docs/guide/참고-설정과-상태값.md)
- 운영·개발 가이드: [docs/dev/운영-개발-가이드.md](docs/dev/운영-개발-가이드.md)

설치 후 연습 모드로 바로 확인해 볼 수 있습니다. 연습 모드는 실제 AI를 부르지 않으므로 비용이 들지 않습니다.

```powershell
python -m pip install -e ".[dev]"
python -m chimera run --target mock_demo --config config.mock.toml
```
