"""Junior pool counters + senior promotion / demotion. See DESIGN.md §8.

P0: thin record-keeping. Promotion / demotion logic is exposed but not yet
fired by the dispatcher (that activates in P3).
"""
from __future__ import annotations

from typing import Any

from orchestrator import state


def bump_junior(role: str) -> None:
    with state.write() as conn:
        conn.execute(
            """INSERT INTO junior_pools(role, tasks_completed, last_task_at)
               VALUES (?, 1, ?)
               ON CONFLICT(role) DO UPDATE SET
                 tasks_completed = tasks_completed + 1,
                 last_task_at = excluded.last_task_at""",
            (role, state.now()),
        )


def junior_status(role: str) -> dict[str, Any] | None:
    with state.connect() as conn:
        row = conn.execute("SELECT * FROM junior_pools WHERE role=?", (role,)).fetchone()
    return dict(row) if row else None


def add_senior(role: str, name: str, memory_path: str) -> None:
    with state.write() as conn:
        conn.execute(
            """INSERT INTO seniors(role, name, tasks_completed, last_task_at,
                memory_path, promoted_at) VALUES (?, ?, 0, NULL, ?, ?)""",
            (role, name, memory_path, state.now()),
        )


def list_seniors(role: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM seniors WHERE archived_at IS NULL"
    params: list = []
    if role:
        sql += " AND role = ?"
        params.append(role)
    with state.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def archive_senior(role: str, name: str, reason: str | None = None) -> None:
    with state.write() as conn:
        conn.execute(
            "UPDATE seniors SET archived_at=? WHERE role=? AND name=?",
            (state.now(), role, name),
        )


def should_promote_junior(role: str) -> bool:
    """Promotion rule: pool ≥10 tasks, last 5 outcomes have zero kills."""
    with state.connect() as conn:
        row = conn.execute(
            "SELECT tasks_completed FROM junior_pools WHERE role=?", (role,)
        ).fetchone()
        if not row or row[0] < 10:
            return False
        recent = conn.execute(
            """SELECT outcome FROM task_outcomes
               WHERE role = ? AND persona_name IS NULL
               ORDER BY rated_at DESC LIMIT 5""",
            (role,),
        ).fetchall()
    return len(recent) == 5 and not any(r[0] == "kill" for r in recent)


def should_archive_senior(role: str, name: str) -> bool:
    """Demotion rule: 3 most-recent outcomes for this senior all `kill`."""
    with state.connect() as conn:
        recent = conn.execute(
            """SELECT outcome FROM task_outcomes
               WHERE role=? AND persona_name=?
               ORDER BY rated_at DESC LIMIT 3""",
            (role, name),
        ).fetchall()
    return len(recent) == 3 and all(r[0] == "kill" for r in recent)
