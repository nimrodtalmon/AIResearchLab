"""Polisher. See DESIGN.md §3.1.

Surface edits only. Reads a section that's `complete` and not `polished`,
asks the LLM to polish (preserving content), writes back. In mock mode the
polisher is a no-op pass-through with a marker comment, since the mock has
no way to round-trip multiline LaTeX through the JSON prompt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator import paper_state
from orchestrator.config import CONFIG
from orchestrator.dispatcher import register_agent

from .base import Agent, Report


class Polisher(Agent):
    role = "polisher"
    work_type = "writing"
    default_model = "claude-haiku-4-5-20251001"
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
                if s.get("status") == "complete" and not s.get("polished"):
                    section = s
                    break
        if section is None:
            return Report(
                role=self.role, paper_id=paper_id, kind="fyi", severity="green",
                summary="No section needs polishing",
                self_diagnosis="all complete sections already polished", confidence="high",
            )

        if workspace_path is None:
            return Report(
                role=self.role, paper_id=paper_id, kind="stuck_no_question",
                severity="yellow",
                summary="Polisher needs a workspace",
                self_diagnosis="workspace_path was None", confidence="low",
                what_was_tried="nothing", what_blocks_me="missing workspace",
            )

        sec_path = workspace_path / "sections" / f"{section['id']}.tex"
        if not sec_path.exists():
            return Report(
                role=self.role, paper_id=paper_id, kind="stuck_no_question",
                severity="yellow",
                summary=f"Section file missing for {section['id']}",
                self_diagnosis="Writer should have produced this file",
                confidence="low",
                what_was_tried=f"looked at {sec_path}",
                what_blocks_me="file not found",
            )

        original = sec_path.read_text()

        if CONFIG.use_mock_llm or not CONFIG.anthropic_api_key:
            polished = self._mock_polish(original)
        else:
            polished = self._real_polish(paper_id, section, original)

        sec_path.write_text(polished if polished.endswith("\n") else polished + "\n")
        section["polished"] = True
        paper_state.write(paper_id, data)
        paper_state.log_decision(paper_id, by=self.role, what=f"Polished {section['id']}")

        return Report(
            role=self.role, paper_id=paper_id, kind="fyi", severity="green",
            summary=f"Polished section {section['id']}",
            self_diagnosis="surface edits only", confidence="high",
        )

    def _mock_polish(self, original: str) -> str:
        marker = "% polished: surface edits only\n"
        if original.startswith("% polished:"):
            return original
        return marker + original

    def _real_polish(self, paper_id: str, section: dict[str, Any], original: str) -> str:
        # Real LLM call: pass current LaTeX inline. Use the section_id field to
        # let the prompt template know what we're polishing; the original text
        # is appended to the user message via the `current_latex` placeholder.
        out = self.call_json(paper_id, {
            "paper_id": paper_id,
            "section_id": section["id"],
            "title": section["title"],
            "current_latex": original,
        })
        polished = (out.get("latex") or "").strip()
        return polished or original


def _runner(task: dict[str, Any]) -> None:
    Polisher().run(task)


register_agent("polisher", _runner)
