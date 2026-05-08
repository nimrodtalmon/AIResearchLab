"""Event-driven dispatcher. See DESIGN.md §4.4.

P0: a thread pool runs `Agent.run` on a Task. The dispatcher reacts to events
(paper created, report committed, PI action, periodic sweep) by walking each
active paper's state and enqueuing tasks the producing modules expect.

Most of the routing logic for §3.2 (junior-feed, senior round-robin) is stubbed
to "always dispatch as junior." Persona promotion lights up in P3.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from . import budget, state
from .config import CONFIG

log = logging.getLogger(__name__)

# Registry — agent modules register themselves at import time.
_AGENT_REGISTRY: dict[str, Callable[[dict[str, Any]], None]] = {}


def register_agent(role: str, runner: Callable[[dict[str, Any]], None]) -> None:
    _AGENT_REGISTRY[role] = runner


@dataclass
class Task:
    role: str
    paper_id: str | None
    fields: dict[str, Any] = field(default_factory=dict)


class Dispatcher:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=CONFIG.thread_pool_size, thread_name_prefix="agent")
        self._inflight: set[tuple[str, str | None]] = set()
        self._inflight_lock = threading.Lock()

    # ---- Public surface --------------------------------------------------

    def trigger_paper(self, paper_id: str) -> None:
        """Re-evaluate a paper's state and enqueue any newly derivable tasks."""
        paper = state.get_paper(paper_id)
        if not paper or paper["paused"] or paper["status"] in ("killed", "submitted", "shelved"):
            return

        if self._too_many_waiting(paper_id):
            log.info("backpressure: %s has ≥3 reports waiting", paper_id)
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
        """Walk paper state and yield tasks. P0: only the dummy agent fires."""
        tasks: list[Task] = []
        # Skeleton hook — until real agents land, we always wake the dummy
        # so the end-to-end loop is exercised.
        if "dummy" in _AGENT_REGISTRY:
            tasks.append(Task(role="dummy", paper_id=paper["id"]))
        return tasks

    # ---- Submission ------------------------------------------------------

    def _submit(self, task: Task) -> None:
        runner = _AGENT_REGISTRY.get(task.role)
        if runner is None:
            log.warning("no agent registered for role %r", task.role)
            return

        key = (task.role, task.paper_id)
        with self._inflight_lock:
            if key in self._inflight:
                return
            self._inflight.add(key)

        def _go() -> None:
            try:
                payload = {"paper_id": task.paper_id, **task.fields}
                runner(payload)
            except Exception:
                log.exception("agent %s failed", task.role)
            finally:
                with self._inflight_lock:
                    self._inflight.discard(key)
                # Agent finished — re-evaluate this paper for follow-up work.
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


# A single dispatcher per process. The Flask app wires this in app.py.
DISPATCHER: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    global DISPATCHER
    if DISPATCHER is None:
        DISPATCHER = Dispatcher()
    return DISPATCHER
