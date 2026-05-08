"""Structurer. See DESIGN.md §3.1 / §6.1.

Reads the approved abstract and proposes a section list. Writes:
- `papers/<id>/state.json` ← structure.sections (each marked incomplete)
- `papers/<id>/workspace/main.tex` ← skeleton with title/abstract + \\input{}s
- `papers/<id>/workspace/sections/<sid>.tex` ← stub `\\section{...}` files

Then emits a `decision_needed` report asking the PI to approve the structure.
Once approved, Writer picks up each section.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator import paper_state
from orchestrator.dispatcher import register_agent

from .base import Agent, Report, render_main_tex


class Structurer(Agent):
    role = "structurer"
    work_type = "writing"
    default_model = "claude-sonnet-4-6"
    max_tokens = 2000

    def execute(self, task: dict[str, Any], workspace_path: Path | None) -> Report:
        paper_id = task["paper_id"]
        data = paper_state.read(paper_id)

        if data.get("structure", {}).get("sections"):
            return Report(
                role=self.role, paper_id=paper_id, kind="fyi", severity="green",
                summary="Structure already proposed; skipping",
                self_diagnosis="state.json already has sections", confidence="high",
            )

        llm_task = {
            "paper_id": paper_id,
            "title": data.get("title", ""),
            "abstract": data.get("abstract", ""),
            "formal_problem": data.get("formal_problem", ""),
        }
        out = self.call_json(paper_id, llm_task)
        raw_sections = out.get("sections") or []

        sections: list[dict[str, Any]] = []
        for s in raw_sections:
            if not s.get("id") or not s.get("title"):
                continue
            sections.append({
                "id": s["id"], "title": s["title"],
                "status": "incomplete", "polished": False, "critiqued": False,
            })
        if not sections:
            return Report(
                role=self.role, paper_id=paper_id, kind="stuck_no_question",
                severity="yellow",
                summary="Structurer returned no sections",
                self_diagnosis="LLM output had no usable section list",
                confidence="low", what_was_tried="ran LLM",
                what_blocks_me="empty structure",
            )

        data.setdefault("structure", {})["sections"] = sections
        paper_state.write(paper_id, data)
        paper_state.log_decision(paper_id, by=self.role,
                                 what=f"Proposed sections: {', '.join(s['id'] for s in sections)}")

        # Materialize the workspace skeleton.
        if workspace_path is not None:
            (workspace_path / "sections").mkdir(parents=True, exist_ok=True)
            (workspace_path / "main.tex").write_text(render_main_tex(data))
            for s in sections:
                p = workspace_path / "sections" / f"{s['id']}.tex"
                if not p.exists():
                    p.write_text(f"\\section{{{s['title']}}}\\label{{sec:{s['id']}}}\n\n% to be drafted\n")

        outline = "\n".join(f"{i + 1}. **{s['title']}** (`{s['id']}`)" for i, s in enumerate(sections))
        return Report(
            role=self.role, paper_id=paper_id, kind="decision_needed", severity="green",
            summary=f"Proposed structure ({len(sections)} sections)",
            decision_question="Approve this structure? Writers will start drafting incomplete sections.",
            artifact=f"## Proposed structure\n\n{outline}",
            options=[
                {"id": "approve", "label": "Approve",
                 "consequence": "writers start drafting in parallel"},
                {"id": "regen", "label": "Regenerate",
                 "consequence": "discard sections; Structurer retries"},
                {"id": "kill", "label": "Kill",
                 "consequence": "shelve this paper"},
            ],
            self_diagnosis="Standard 5-section layout, scaled to the abstract.",
            confidence="medium",
        )


def _runner(task: dict[str, Any]) -> None:
    Structurer().run(task)


register_agent("structurer", _runner)
