"""중단 기능 (명세서 10장): Ctrl+C, `chimera stop <run_id>`, STOP 파일. 세 가지 모두 같은 처리로 이어진다."""
from __future__ import annotations

import signal
from pathlib import Path


class Stopper:
    def __init__(self, conn, run_id: str, run_dir: Path):
        self.conn = conn
        self.run_id = run_id
        self.stop_file = run_dir / "STOP"
        self._flag = False
        self._prev = None

    def install_sigint(self) -> None:
        def handler(signum, frame):
            if self._flag:  # 두 번째 Ctrl+C: 즉시 중단
                raise KeyboardInterrupt
            self._flag = True
            print("\n[중단 요청] 진행 중인 호출 1건만 끝내고 멈춥니다. 즉시 멈추려면 Ctrl+C를 한 번 더 누르세요.",
                  flush=True)
        try:
            self._prev = signal.signal(signal.SIGINT, handler)
        except ValueError:  # 메인 스레드가 아닐 때(테스트 등)
            self._prev = None

    def uninstall(self) -> None:
        if self._prev is not None:
            signal.signal(signal.SIGINT, self._prev)
            self._prev = None

    def request(self) -> None:
        self._flag = True

    def requested(self) -> str | None:
        if self._flag:
            return "Ctrl+C"
        if self.stop_file.exists():
            return "STOP 파일"
        row = self.conn.execute("SELECT stop_requested FROM runs WHERE run_id=?", (self.run_id,)).fetchone()
        if row and row[0]:
            return "chimera stop"
        return None
