"""Per-paper `state.json` helpers. See DESIGN.md §5.4.

This file lives at `papers/<id>/state.json`, sibling to the workspace. It's
the authoritative paper context: structure, claims, sims, decisions log.

Writes happen inside `workspace.transaction(paper_id, ...)` so the per-paper
mutex covers them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CONFIG
from . import state as state_db


def _path(paper_id: str) -> Path:
    return CONFIG.papers_dir / paper_id / "state.json"


def empty(paper_id: str, title: str = "", target_venue: str | None = None,
          problem: str = "") -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": title,
        "target_venue": target_venue,
        "status": "idea",
        "seed_problem": problem,
        "abstract": "",
        "formal_problem": "",
        "structure": {"sections": [], "figures": []},
        "structure_approved": False,
        "claims": [],
        "sims": [],
        "decisions_log": [],
    }


def read(paper_id: str) -> dict[str, Any]:
    p = _path(paper_id)
    if not p.exists():
        return empty(paper_id)
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        # Per DESIGN.md §12: log + restore. P1 just returns an empty shell;
        # the dispatcher's stuck-report flow surfaces the corruption.
        return {**empty(paper_id), "_corrupted": True}


def write(paper_id: str, data: dict[str, Any]) -> None:
    p = _path(paper_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def log_decision(paper_id: str, by: str, what: str, report_id: str | None = None) -> None:
    data = read(paper_id)
    entry: dict[str, Any] = {"timestamp": state_db.now(), "by": by, "what": what}
    if report_id:
        entry["report_id"] = report_id
    data.setdefault("decisions_log", []).append(entry)
    write(paper_id, data)


def section_by_id(data: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    for s in data.get("structure", {}).get("sections", []):
        if s.get("id") == section_id:
            return s
    return None


def all_sections_polished(data: dict[str, Any]) -> bool:
    sections = data.get("structure", {}).get("sections", [])
    if not sections:
        return False
    return all(s.get("polished") for s in sections)
