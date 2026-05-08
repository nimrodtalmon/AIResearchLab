"""Dummy agent for P0. Returns a canned decision-needed Report so the
end-to-end dispatch → queue → render path can be exercised without any LLM.

Spawns one report per paper, then sleeps until the report is resolved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator import state
from orchestrator.dispatcher import register_agent

from .base import Agent, Report


class DummyAgent(Agent):
    role = "dummy"
    work_type = "writing"

    def execute(self, task: dict[str, Any], workspace_path: Path | None) -> Report:
        paper_id = task.get("paper_id")
        # Touch the workspace so we exercise the transaction path.
        if workspace_path is not None:
            log = workspace_path / "ACTIVITY.md"
            existing = log.read_text() if log.exists() else "# Activity\n"
            log.write_text(existing + f"- dummy ran for {paper_id} at {state.now()}\n")

        return Report(
            role=self.role,
            paper_id=paper_id,
            kind="decision_needed",
            severity="green",
            summary="Dummy agent ran. Approve to confirm the loop works.",
            self_diagnosis="No real work performed; this is a P0 smoke-test report.",
            confidence="high",
            decision_question="Does the end-to-end dispatch / queue / render path work?",
            artifact="(none)",
            options=[
                {"id": "approve", "label": "Approve", "consequence": "marks the report resolved"},
                {"id": "kill", "label": "Kill", "consequence": "marks the report archived"},
            ],
        )


def _runner(task: dict[str, Any]) -> None:
    DummyAgent().run(task)


# Don't auto-fire if the paper already has a waiting dummy report — keeps the
# safety sweep from spamming the queue.
def _runner_idempotent(task: dict[str, Any]) -> None:
    paper_id = task.get("paper_id")
    if paper_id:
        existing = state.list_reports(paper_id=paper_id, status="waiting")
        if any(r["role"] == "dummy" for r in existing):
            return
    _runner(task)


register_agent("dummy", _runner_idempotent)
