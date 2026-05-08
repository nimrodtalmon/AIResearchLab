"""Scope-tagged principle store. See DESIGN.md §7."""
from __future__ import annotations

from typing import Any

from orchestrator import state
from orchestrator.config import CONFIG


def insert_principle(text: str, scope: str, derived_from: str | None = None) -> str:
    pid = state.new_id("pr")
    with state.write() as conn:
        conn.execute(
            """INSERT INTO principles(id, text, scope, derived_from_remarks,
                version, created_at) VALUES (?,?,?,?,1,?)""",
            (pid, text, scope, derived_from, state.now()),
        )
    return pid


def deprecate(principle_id: str, reason: str) -> None:
    with state.write() as conn:
        conn.execute(
            "UPDATE principles SET deprecated_at=?, deprecated_reason=? WHERE id=?",
            (state.now(), reason, principle_id),
        )


def list_active(scope: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM principles WHERE deprecated_at IS NULL"
    params: list = []
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    sql += " ORDER BY created_at"
    with state.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_candidates(undecided: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM principle_candidates"
    if undecided:
        sql += " WHERE decided_at IS NULL"
    sql += " ORDER BY created_at DESC"
    with state.connect() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def decide_candidate(candidate_id: str, decision: str) -> None:
    with state.write() as conn:
        conn.execute(
            "UPDATE principle_candidates SET decision=?, decided_at=? WHERE id=?",
            (decision, state.now(), candidate_id),
        )


def regenerate_markdown() -> None:
    """Write the human-readable mirror at lab/PRINCIPLES.md. Read-only — direct
    edits to that file are *not* read back. See DESIGN.md §7.3."""
    out = CONFIG.lab_dir / "PRINCIPLES.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = list_active()
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_scope.setdefault(r["scope"], []).append(r)
    lines = ["# Lab Principles", "", "_Auto-generated mirror. Do not edit by hand._", ""]
    for scope in sorted(by_scope):
        lines.append(f"## {scope}")
        for p in by_scope[scope]:
            lines.append(f"- {p['text']}")
        lines.append("")
    out.write_text("\n".join(lines))
