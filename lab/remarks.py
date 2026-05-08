"""Remark log + scope tagging. See DESIGN.md §6.4 / §7."""
from __future__ import annotations

from typing import Any

from orchestrator import state


def log_remark(
    report_id: str | None,
    paper_id: str | None,
    role: str | None,
    text: str,
    pi_tagged: bool = False,
    suggested_scope: str | None = None,
) -> str:
    return state.insert_remark(
        report_id=report_id,
        paper_id=paper_id,
        role=role,
        text=text,
        pi_tagged=pi_tagged,
        suggested_scope=suggested_scope,
    )


def list_remarks(paper_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM remarks"
    params: list = []
    if paper_id:
        sql += " WHERE paper_id = ?"
        params.append(paper_id)
    sql += " ORDER BY created_at DESC"
    with state.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
