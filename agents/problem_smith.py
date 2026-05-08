"""Problem-Smith. See DESIGN.md §3.1 / §6.1.

Takes a seed problem and produces:
- a working title
- a 150-word abstract
- a formal problem statement
- candidate claims

Updates `state.json` with these fields and transitions paper status idea -> formal.
Emits a `decision_needed` Report asking PI to approve the abstract.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator import paper_state, state
from orchestrator.dispatcher import register_agent

from .base import Agent, Report


class ProblemSmith(Agent):
    role = "problem_smith"
    work_type = "formalization"
    default_model = "claude-sonnet-4-6"
    max_tokens = 3000

    def execute(self, task: dict[str, Any], workspace_path: Path | None) -> Report:
        paper_id = task["paper_id"]
        data = paper_state.read(paper_id)

        # Avoid re-running once we already have an abstract.
        if data.get("abstract") and data.get("status") in ("formal", "drafting"):
            return Report(
                role=self.role, paper_id=paper_id, kind="fyi", severity="green",
                summary="Abstract already drafted; skipping",
                self_diagnosis="state.json already has an abstract", confidence="high",
            )

        llm_task = {
            "paper_id": paper_id,
            "title": data.get("title") or "",
            "seed_problem": data.get("seed_problem") or task.get("seed_problem", ""),
            "target_venue": data.get("target_venue") or "",
        }
        out = self.call_json(paper_id, llm_task)

        title = out.get("title") or data.get("title") or "Untitled"
        abstract = (out.get("abstract") or "").strip()
        formal = (out.get("formal_problem") or "").strip()
        claims = out.get("claims") or []

        if not abstract:
            return Report(
                role=self.role, paper_id=paper_id, kind="stuck_no_question",
                severity="yellow",
                summary="Problem-Smith returned no abstract",
                self_diagnosis="LLM output missing 'abstract' field",
                confidence="low", what_was_tried="ran LLM",
                what_blocks_me="empty abstract",
            )

        data["title"] = title
        data["abstract"] = abstract
        data["formal_problem"] = formal
        data["claims"] = [
            {"id": c["id"], "text": c["text"], "status": "unproven",
             "attempts_count": 0, "critiqued": False}
            for c in claims if c.get("id") and c.get("text")
        ]
        data["status"] = "formal"
        paper_state.write(paper_id, data)
        paper_state.log_decision(paper_id, by=f"{self.role}", what="Formalized seed; drafted abstract")

        # Mirror status into the SQLite row so the dashboard reflects it.
        state.update_paper(paper_id, status="formal", title=title)

        return Report(
            role=self.role, paper_id=paper_id, kind="decision_needed", severity="yellow",
            summary=f"Abstract drafted for '{title}'",
            decision_question="Approve this abstract and let the lab structure the paper?",
            artifact=f"# {title}\n\n{abstract}\n\n## Formal problem\n{formal}\n\n## Candidate claims\n" +
                     "\n".join(f"- {c['id']}: {c['text']}" for c in data["claims"]),
            options=[
                {"id": "approve", "label": "Approve",
                 "consequence": "paper enters drafting; Structurer takes over"},
                {"id": "regen", "label": "Regenerate",
                 "consequence": "discard abstract; Problem-Smith retries"},
                {"id": "kill", "label": "Kill",
                 "consequence": "shelve this paper"},
            ],
            self_diagnosis="Formalized from the seed problem. Confidence is medium until PI inspects.",
            confidence="medium",
        )


def _runner(task: dict[str, Any]) -> None:
    ProblemSmith().run(task)


register_agent("problem_smith", _runner)
