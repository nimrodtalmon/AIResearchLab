"""Decision queue assembly. See DESIGN.md §6.4 / §9.

For P0 this is a thin wrapper over `state.list_reports`. Splits FYI into the
activity feed and decision_needed / stuck_no_question into the action queue.
"""
from __future__ import annotations

from typing import Any

from . import state


def waiting_for_pi(paper_id: str | None = None) -> list[dict[str, Any]]:
    rows = state.list_reports(paper_id=paper_id, status="waiting")
    return [r for r in rows if r["kind"] != "fyi"]


def fyi_feed(paper_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    rows = state.list_reports(paper_id=paper_id, status="waiting", kind="fyi")
    return rows[:limit]


def severity_rank(report: dict[str, Any]) -> int:
    return {"red": 0, "yellow": 1, "green": 2}.get(report["severity"], 3)
