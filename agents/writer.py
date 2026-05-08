"""Writer. See DESIGN.md §3.1 / §6.1.

Drafts one section at a time. Picks the first `incomplete` section if a
specific section_id isn't supplied. Writes the file in the workspace,
flips the section status to `complete`, and emits an FYI report.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator import paper_state
from orchestrator.dispatcher import register_agent

from .base import Agent, Report


class Writer(Agent):
    role = "writer"
    work_type = "writing"
    default_model = "claude-sonnet-4-6"
    max_tokens = 4000

    def execute(self, task: dict[str, Any], workspace_path: Path | None) -> Report:
        paper_id = task["paper_id"]
        data = paper_state.read(paper_id)
        sections = data.get("structure", {}).get("sections") or []

        section = None
        sid = task.get("section_id")
        if sid:
            section = paper_state.section_by_id(data, sid)
        if section is None:
            for s in sections:
                if s.get("status") == "incomplete":
                    section = s
                    break
        if section is None:
            return Report(
                role=self.role, paper_id=paper_id, kind="fyi", severity="green",
                summary="No incomplete section to draft",
                self_diagnosis="all sections already complete", confidence="high",
            )

        llm_task = {
            "paper_id": paper_id,
            "section_id": section["id"],
            "title": section["title"],
            "abstract": data.get("abstract", ""),
            "formal_problem": data.get("formal_problem", ""),
            "section_titles": ", ".join(f"{s['id']}={s['title']}" for s in sections),
        }
        out = self.call_json(paper_id, llm_task)
        latex = (out.get("latex") or "").strip()
        if not latex:
            return Report(
                role=self.role, paper_id=paper_id, kind="stuck_no_question",
                severity="yellow",
                summary=f"Writer returned no LaTeX for {section['id']}",
                self_diagnosis="empty 'latex' field in LLM output",
                confidence="low",
                what_was_tried="ran LLM",
                what_blocks_me="empty draft",
            )

        if workspace_path is not None:
            sec_path = workspace_path / "sections" / f"{section['id']}.tex"
            sec_path.parent.mkdir(parents=True, exist_ok=True)
            sec_path.write_text(latex + ("\n" if not latex.endswith("\n") else ""))

        section["status"] = "complete"
        paper_state.write(paper_id, data)
        paper_state.log_decision(paper_id, by=self.role, what=f"Drafted section {section['id']}")

        return Report(
            role=self.role, paper_id=paper_id, kind="fyi", severity="green",
            summary=f"Drafted section {section['id']} ({section['title']})",
            self_diagnosis="first-pass draft from abstract + section title",
            confidence="medium",
        )


def _runner(task: dict[str, Any]) -> None:
    Writer().run(task)


register_agent("writer", _runner)
