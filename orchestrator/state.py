"""SQLite access. WAL mode + thread-safe connection-per-call.

Helpers here are deliberately thin — agents and dispatcher modules build queries
on top. See DESIGN.md §5.1 for the schema.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import CONFIG

# Serialize all writes from this process. SQLite's WAL allows concurrent reads
# but only one writer; this lock keeps Python-side retry storms out of the way.
_WRITE_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG.db_path, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Serialized writer. Use for INSERT / UPDATE / DELETE."""
    with _WRITE_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def init_db(migrations_dir: Path | None = None) -> None:
    migrations_dir = migrations_dir or (Path(__file__).resolve().parent.parent / "scripts" / "migrations")
    CONFIG.db_path.parent.mkdir(parents=True, exist_ok=True)
    # Migrations run outside our write() helper because executescript() commits
    # any pending transaction, which conflicts with an explicit BEGIN IMMEDIATE.
    with _WRITE_LOCK:
        conn = _connect()
        try:
            applied: set[int] = set()
            if _table_exists(conn, "schema_migrations"):
                applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                version = int(sql_file.stem.split("_")[0])
                if version in applied:
                    continue
                conn.executescript(sql_file.read_text())
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, now()),
                )
        finally:
            conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ---------- Papers ---------------------------------------------------------

def create_paper(title: str, problem_statement: str, target_venue: str | None = None) -> str:
    paper_id = new_id("p")
    workspace_path = str(CONFIG.papers_dir / paper_id / "workspace")
    with write() as conn:
        conn.execute(
            """INSERT INTO papers(id, title, status, workspace_path, target_venue,
               started_at, last_activity, spend_usd) VALUES (?,?,?,?,?,?,?,0)""",
            (paper_id, title, "idea", workspace_path, target_venue, now(), now()),
        )
    return paper_id


def get_paper(paper_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    return dict(row) if row else None


def list_papers(include_killed: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM papers"
    if not include_killed:
        sql += " WHERE status != 'killed'"
    sql += " ORDER BY last_activity DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def update_paper(paper_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with write() as conn:
        conn.execute(
            f"UPDATE papers SET {cols}, last_activity=? WHERE id=?",
            (*fields.values(), now(), paper_id),
        )


def set_paper_paused(paper_id: str, paused: bool) -> None:
    with write() as conn:
        conn.execute("UPDATE papers SET paused=? WHERE id=?", (1 if paused else 0, paper_id))


# ---------- Reports --------------------------------------------------------

def insert_report(payload: dict[str, Any]) -> str:
    """`payload` is the full Report dict (see DESIGN.md §5.5)."""
    report_id = new_id("r")
    with write() as conn:
        conn.execute(
            """INSERT INTO reports(id, paper_id, role, persona_name, work_type,
                kind, status, severity, summary, payload_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                report_id,
                payload.get("paper_id"),
                payload["role"],
                payload.get("persona_name"),
                payload.get("work_type"),
                payload["kind"],
                "waiting",
                payload.get("severity", "green"),
                payload["summary"],
                json.dumps(payload),
                now(),
            ),
        )
    return report_id


def get_report(report_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out["payload_json"])
    return out


def list_reports(
    paper_id: str | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if paper_id:
        clauses.append("paper_id = ?")
        params.append(paper_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    sql = "SELECT * FROM reports"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"])
        out.append(d)
    return out


def resolve_report(report_id: str, status: str = "resolved") -> None:
    with write() as conn:
        conn.execute(
            "UPDATE reports SET status=?, resolved_at=? WHERE id=?",
            (status, now(), report_id),
        )


# ---------- Remarks --------------------------------------------------------

def insert_remark(
    report_id: str | None,
    paper_id: str | None,
    role: str | None,
    text: str,
    pi_tagged: bool,
    suggested_scope: str | None,
) -> str:
    rid = new_id("rm")
    with write() as conn:
        conn.execute(
            """INSERT INTO remarks(id, report_id, paper_id, role, text,
                pi_tagged_for_principle, suggested_scope, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rid, report_id, paper_id, role, text, 1 if pi_tagged else 0, suggested_scope, now()),
        )
    return rid


# ---------- Task outcomes --------------------------------------------------

def record_task_outcome(
    role: str,
    persona_name: str | None,
    paper_id: str | None,
    report_id: str | None,
    outcome: str,
) -> None:
    with write() as conn:
        conn.execute(
            """INSERT INTO task_outcomes(role, persona_name, paper_id, report_id,
                outcome, rated_at) VALUES (?,?,?,?,?,?)""",
            (role, persona_name, paper_id, report_id, outcome, now()),
        )
