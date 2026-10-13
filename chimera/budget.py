"""예산 예약·정산 (명세서 10장). 호출 전에 최대 비용을 예약하고, 상한을 넘을 호출은 시작하지 않는다."""
from __future__ import annotations

from .config import Limits, ModelCfg
from .db import Tx, local_day, new_id, now
from .enums import CallRole, LedgerKind, StopReason


class BudgetExceeded(Exception):
    def __init__(self, reason: StopReason, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


_OPEN = ("kind='RESERVE' AND reservation_id NOT IN "
         "(SELECT reservation_id FROM budget_ledger WHERE kind IN ('SETTLE','RELEASE'))")


def estimate_input_tokens(*texts: str, ratio: float = 1.0) -> int:
    """보수적 추정: 글자 수 × ratio. 한국어는 글자당 토큰이 많아 넉넉히 잡는다. 정산은 실제 토큰으로 한다."""
    return int(sum(len(t) for t in texts) * ratio) + 50


class Budget:
    def __init__(self, conn, run_id: str, limits: Limits):
        self.conn = conn
        self.run_id = run_id
        self.limits = limits

    def _sum(self, where: str, params) -> float:
        row = self.conn.execute(f"SELECT COALESCE(SUM(usd),0) FROM budget_ledger WHERE {where}", params).fetchone()
        return float(row[0])

    def run_settled(self) -> float:
        return self._sum("run_id=? AND kind='SETTLE'", (self.run_id,))

    def run_committed(self) -> float:
        """정산된 사용액 + 아직 정산 안 된 예약액."""
        return self.run_settled() + self._sum(f"run_id=? AND {_OPEN}", (self.run_id,))

    def day_committed(self, day: str | None = None) -> float:
        day = day or local_day()
        return (self._sum("day=? AND kind='SETTLE'", (day,))
                + self._sum(f"day=? AND {_OPEN}", (day,)))

    def calls(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM budget_ledger WHERE run_id=? AND kind='RESERVE'",
                                 (self.run_id,)).fetchone()[0]

    def remaining_run(self) -> float:
        return max(0.0, self.limits.max_cost_usd_per_run - self.run_committed())

    def check_can_start_run(self) -> None:
        if self.day_committed() >= self.limits.max_cost_usd_per_day:
            raise BudgetExceeded(StopReason.BUDGET_EXHAUSTED,
                                 f"오늘 사용액이 하루 상한 ${self.limits.max_cost_usd_per_day}에 도달했습니다")

    def reserve(self, role: CallRole, model: ModelCfg, est_input_tokens: int,
                attempt_id: str | None = None) -> tuple[str, float]:
        amount = model.max_cost(est_input_tokens)
        rid = new_id("res")
        with Tx(self.conn) as c:
            if self.calls() >= self.limits.max_model_calls_per_run:
                raise BudgetExceeded(StopReason.BUDGET_EXHAUSTED,
                                     f"모델 호출 수 상한 {self.limits.max_model_calls_per_run}회 도달")
            if self.run_committed() + amount > self.limits.max_cost_usd_per_run + 1e-12:
                raise BudgetExceeded(StopReason.BUDGET_EXHAUSTED,
                                     f"다음 {role} 호출 예약액 ${amount:.4f}이 1회 상한 "
                                     f"${self.limits.max_cost_usd_per_run} 안에 들어가지 않습니다 "
                                     f"(현재 ${self.run_committed():.4f})")
            if self.day_committed() + amount > self.limits.max_cost_usd_per_day + 1e-12:
                raise BudgetExceeded(StopReason.BUDGET_EXHAUSTED,
                                     f"다음 {role} 호출 예약액 ${amount:.4f}이 하루 상한 "
                                     f"${self.limits.max_cost_usd_per_day} 안에 들어가지 않습니다")
            c.execute("INSERT INTO budget_ledger(run_id, reservation_id, attempt_id, kind, call_role, usd, created_at, day)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (self.run_id, rid, attempt_id, LedgerKind.RESERVE.value, role.value, amount, now(), local_day()))
        return rid, amount

    def settle(self, rid: str, role: CallRole, usd: float, attempt_id: str | None = None) -> None:
        self.conn.execute("INSERT INTO budget_ledger(run_id, reservation_id, attempt_id, kind, call_role, usd, created_at, day)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (self.run_id, rid, attempt_id, LedgerKind.SETTLE.value, role.value, usd, now(), local_day()))

    def release(self, rid: str, role: CallRole) -> None:
        self.conn.execute("INSERT INTO budget_ledger(run_id, reservation_id, attempt_id, kind, call_role, usd, created_at, day)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (self.run_id, rid, None, LedgerKind.RELEASE.value, role.value, 0.0, now(), local_day()))

    def settle_open_conservatively(self) -> int:
        """중단된 호출의 예약액을 '사용한 것'으로 정산 (재개 시). 정산 건수를 돌려준다."""
        rows = self.conn.execute(f"SELECT reservation_id, call_role, usd, attempt_id FROM budget_ledger "
                                 f"WHERE run_id=? AND {_OPEN}", (self.run_id,)).fetchall()
        for r in rows:
            self.settle(r["reservation_id"], CallRole(r["call_role"]), r["usd"], r["attempt_id"])
        return len(rows)
