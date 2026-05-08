"""Per-paper and global daily spend caps. See DESIGN.md §11."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import state
from .config import CONFIG


def _today_floor() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d") + "T00:00:00Z"


def spend_today(paper_id: str | None = None) -> float:
    sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM budget_log WHERE timestamp >= ?"
    params: list = [_today_floor()]
    if paper_id is not None:
        sql += " AND paper_id = ?"
        params.append(paper_id)
    with state.connect() as conn:
        return float(conn.execute(sql, params).fetchone()[0])


def can_dispatch(paper_id: str | None) -> tuple[bool, str | None]:
    if paper_id is not None:
        per_paper = spend_today(paper_id)
        if per_paper >= CONFIG.per_paper_daily_cap:
            return False, f"per-paper cap reached ({per_paper:.2f}/{CONFIG.per_paper_daily_cap})"
    total = spend_today(None)
    if total >= CONFIG.global_daily_cap:
        return False, f"global cap reached ({total:.2f}/{CONFIG.global_daily_cap})"
    return True, None


def log_call(
    paper_id: str | None,
    role: str,
    persona_name: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
) -> None:
    with state.write() as conn:
        conn.execute(
            """INSERT INTO budget_log(paper_id, role, persona_name, model,
                input_tokens, output_tokens, cost_usd, latency_ms, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (paper_id, role, persona_name, model, input_tokens, output_tokens,
             cost_usd, latency_ms, state.now()),
        )
        if paper_id:
            conn.execute(
                "UPDATE papers SET spend_usd = spend_usd + ? WHERE id=?",
                (cost_usd, paper_id),
            )
