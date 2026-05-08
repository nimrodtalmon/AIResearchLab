"""Agent base class + Report schema. See DESIGN.md §5.5."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from orchestrator import paper_state, prompt_builder, state
from orchestrator.config import CONFIG
from orchestrator.workspace import GateFailure, MutexTimeout, transaction

log = logging.getLogger(__name__)


REPORT_KINDS = ("decision_needed", "fyi", "stuck_no_question")
SEVERITIES = ("green", "yellow", "red")


@dataclass
class Report:
    role: str
    paper_id: str | None
    kind: str                    # decision_needed | fyi | stuck_no_question
    summary: str
    severity: str = "green"
    persona_name: str | None = None
    work_type: str | None = None
    self_diagnosis: str = ""
    confidence: str = "medium"
    made_up_flag: bool = False
    transcript_ref: str | None = None

    decision_question: str | None = None
    artifact: str | None = None
    options: list[dict[str, str]] = field(default_factory=list)

    what_was_tried: str | None = None
    what_blocks_me: str | None = None

    def validate(self) -> None:
        if self.kind not in REPORT_KINDS:
            raise ValueError(f"bad kind: {self.kind}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"bad severity: {self.severity}")
        if not self.summary.strip():
            raise ValueError("summary required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Agent(ABC):
    role: str = ""
    default_model: str = "claude-sonnet-4-6"
    work_type: str | None = None
    max_tokens: int = 4000

    def __init__(self, persona_name: str | None = None) -> None:
        self.persona_name = persona_name

    @abstractmethod
    def execute(self, task: dict[str, Any], workspace_path: Path | None) -> Report:
        ...

    # ---------- Driver ----------------------------------------------------

    def run(self, task: dict[str, Any]) -> str | None:
        paper_id = task.get("paper_id")
        try:
            if paper_id:
                with transaction(paper_id, self.role, self.persona_name) as ctx:
                    report = self.execute(task, ctx.workspace)
            else:
                report = self.execute(task, None)
        except (GateFailure, MutexTimeout) as exc:
            report = self._stuck_report(paper_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("agent crashed")
            report = self._stuck_report(paper_id, f"agent crash: {exc}")

        report.role = self.role
        report.persona_name = self.persona_name
        report.work_type = self.work_type
        report.paper_id = paper_id
        report.validate()
        return state.insert_report(report.to_dict())

    # ---------- LLM helpers ----------------------------------------------

    def call_json(self, paper_id: str | None, task: dict[str, Any]) -> dict[str, Any]:
        """Assemble prompt, call LLM, parse JSON."""
        prompt = prompt_builder.assemble(self.role, self.persona_name, paper_id, task)
        data, resp = prompt_builder.call_llm_json(
            self.role, paper_id, self.persona_name, self.default_model,
            prompt, max_tokens=self.max_tokens, task=task,
        )
        # Persist transcript for traceability.
        self.write_transcript(paper_id, [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
            {"role": "assistant", "content": resp.text},
        ])
        return data

    # ---------- Plumbing -------------------------------------------------

    def _stuck_report(self, paper_id: str | None, message: str) -> Report:
        return Report(
            role=self.role, paper_id=paper_id, kind="stuck_no_question",
            severity="yellow",
            summary=f"{self.role} could not complete its task",
            self_diagnosis=message, confidence="low",
            what_was_tried="see error", what_blocks_me=message,
        )

    def write_transcript(self, paper_id: str | None, payload: list[dict[str, str]]) -> str | None:
        if not paper_id:
            return None
        d = CONFIG.papers_dir / paper_id / "transcripts"
        d.mkdir(parents=True, exist_ok=True)
        ts = state.now().replace(":", "").replace("-", "").replace("Z", "Z")
        path = d / f"{self.role}_{ts}.jsonl"
        with path.open("w") as f:
            for line in payload:
                f.write(json.dumps(line) + "\n")
        return str(path)


# ---------- Common helpers reused across agents --------------------------

def render_main_tex(data: dict[str, Any]) -> str:
    """Compose `main.tex` from state.json. Sections \\input{} their own files."""
    title = (data.get("title") or "Untitled").replace("&", "\\&")
    abstract = (data.get("abstract") or "").strip()
    section_inputs = "\n".join(
        f"\\input{{sections/{s['id']}}}" for s in data.get("structure", {}).get("sections", [])
    )
    abs_block = f"\\begin{{abstract}}\n{abstract}\n\\end{{abstract}}\n" if abstract else ""
    return (
        "\\documentclass{article}\n"
        "\\usepackage{amsmath,amssymb,amsthm}\n"
        "\\usepackage{hyperref}\n"
        f"\\title{{{title}}}\n"
        "\\author{The Lab}\n"
        "\\date{\\today}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        f"{abs_block}"
        f"{section_inputs}\n"
        "\\end{document}\n"
    )
