"""Event-driven dispatcher. See DESIGN.md §4.4.

Walks each active paper's status + state.json and derives the next task.
P1: Problem-Smith → (PI approves abstract) → Structurer → (PI approves
structure) → Writer (one section at a time) → Polisher → "ready for review"
report.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import budget, paper_state, state
from .config import CONFIG

log = logging.getLogger(__name__)

_AGENT_REGISTRY: dict[str, Callable[[dict[str, Any]], None]] = {}


def register_agent(role: str, runner: Callable[[dict[str, Any]], None]) -> None:
    _AGENT_REGISTRY[role] = runner


@dataclass(frozen=True)
class Task:
    role: str
    paper_id: str | None
    fields: tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    def kwargs(self) -> dict[str, Any]:
        return {"paper_id": self.paper_id, **dict(self.fields)}

    def dedup_key(self) -> tuple[str, str | None, tuple]:
        return (self.role, self.paper_id, self.fields)


class Dispatcher:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=CONFIG.thread_pool_size, thread_name_prefix="agent")
        self._inflight: set[tuple] = set()
        self._inflight_lock = threading.Lock()

    def trigger_paper(self, paper_id: str) -> None:
        paper = state.get_paper(paper_id)
        if not paper or paper["paused"] or paper["status"] in ("killed", "submitted", "shelved"):
            return

        if self._too_many_waiting(paper_id):
            log.debug("backpressure: %s has too many reports waiting", paper_id)
            return

        ok, reason = budget.can_dispatch(paper_id)
        if not ok:
            log.info("budget skip on %s: %s", paper_id, reason)
            return

        for task in self._derive_tasks(paper):
            self._submit(task)

    def trigger_all(self) -> None:
        for paper in state.list_papers():
            self.trigger_paper(paper["id"])

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---- Task derivation -------------------------------------------------

    def _derive_tasks(self, paper: dict[str, Any]) -> list[Task]:
        """Per-status routing. Returns at most one task per role."""
        status = paper["status"]
        paper_id = paper["id"]

        if status == "idea":
            data = paper_state.read(paper_id)
            if not data.get("abstract"):
                return [Task("problem_smith", paper_id)]
            return []

        if status == "formal":
            # Waiting on PI to approve the abstract; nothing to dispatch.
            return []

        if status == "drafting":
            data = paper_state.read(paper_id)
            sections = data.get("structure", {}).get("sections") or []

            if not sections:
                return [Task("structurer", paper_id)]
            if not data.get("structure_approved"):
                # Waiting on PI to approve the structure.
                return []

            # Draft incomplete sections one at a time.
            for s in sections:
                if s.get("status") == "incomplete":
                    return [Task("writer", paper_id, (("section_id", s["id"]),))]

            # Polish complete-but-unpolished sections one at a time.
            for s in sections:
                if s.get("status") == "complete" and not s.get("polished"):
                    return [Task("polisher", paper_id, (("section_id", s["id"]),))]

            # Everything done. Emit a single "ready for review" decision_needed
            # report if we haven't already.
            if paper_state.all_sections_polished(data) and not _has_open_review_handoff(paper_id):
                _emit_ready_for_review(paper_id, data)
            return []

        return []

    # ---- Submission ------------------------------------------------------

    def _submit(self, task: Task) -> None:
        runner = _AGENT_REGISTRY.get(task.role)
        if runner is None:
            log.warning("no agent registered for role %r", task.role)
            return

        key = task.dedup_key()
        with self._inflight_lock:
            if key in self._inflight:
                return
            self._inflight.add(key)

        def _go() -> None:
            try:
                runner(task.kwargs())
            except Exception:
                log.exception("agent %s failed", task.role)
            finally:
                with self._inflight_lock:
                    self._inflight.discard(key)
                if task.paper_id:
                    self.trigger_paper(task.paper_id)

        self.pool.submit(_go)

    def _too_many_waiting(self, paper_id: str) -> bool:
        with state.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM reports WHERE paper_id=? AND status='waiting' "
                "AND kind != 'fyi'",
                (paper_id,),
            ).fetchone()[0]
        return n >= 3


# ---- Ready-for-review report ----------------------------------------------

def _has_open_review_handoff(paper_id: str) -> bool:
    rows = state.list_reports(paper_id=paper_id, status="waiting")
    return any((r["payload"].get("decision_question") or "").startswith("Send to review")
               for r in rows)


def _emit_ready_for_review(paper_id: str, data: dict[str, Any]) -> None:
    payload = {
        "role": "structurer",
        "persona_name": None,
        "paper_id": paper_id,
        "work_type": "writing",
        "kind": "decision_needed",
        "severity": "yellow",
        "summary": f"Draft complete: {data.get('title', paper_id)}",
        "self_diagnosis": "All sections drafted and polished.",
        "confidence": "medium",
        "made_up_flag": False,
        "decision_question": "Send to review now?",
        "artifact": "Sections: " + ", ".join(s["id"] for s in data.get("structure", {}).get("sections", [])),
        "options": [
            {"id": "send_to_review", "label": "Send to review",
             "consequence": "(P4) reviewer panel will run. P1 stops here."},
            {"id": "more_revisions", "label": "Hold for revisions",
             "consequence": "leave in drafting; PI will direct further work"},
        ],
    }
    state.insert_report(payload)


DISPATCHER: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    global DISPATCHER
    if DISPATCHER is None:
        DISPATCHER = Dispatcher()
    return DISPATCHER
