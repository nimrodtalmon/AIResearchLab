"""Lab meeting view assembly. See DESIGN.md §9.1.

Groups waiting reports by project → work-type. The flat view sorts by severity.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import budget, queue, state


WORK_TYPES = ["formalization", "theory", "simulation", "writing", "review"]


def lab_meeting() -> dict[str, Any]:
    papers = state.list_papers()
    waiting = queue.waiting_for_pi()
    fyis = queue.fyi_feed()

    # Group waiting reports per paper, then per work-type.
    by_paper: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in waiting:
        wt = r["work_type"] or "other"
        by_paper[r["paper_id"]][wt].append(r)

    projects = []
    for p in papers:
        groups = by_paper.get(p["id"], {})
        ordered = []
        for wt in WORK_TYPES + sorted(set(groups) - set(WORK_TYPES)):
            if wt in groups:
                ordered.append({"work_type": wt, "reports": groups[wt]})
        projects.append({
            "paper": p,
            "report_count": sum(len(g["reports"]) for g in ordered),
            "groups": ordered,
        })

    return {
        "projects": projects,
        "fyi_feed": fyis,
        "totals": {
            "papers": len(papers),
            "waiting": len(waiting),
            "spend_today": budget.spend_today(),
        },
    }


def flat_view() -> dict[str, Any]:
    waiting = sorted(queue.waiting_for_pi(), key=queue.severity_rank)
    return {
        "reports": waiting,
        "fyi_feed": queue.fyi_feed(),
        "totals": {
            "papers": len(state.list_papers()),
            "waiting": len(waiting),
            "spend_today": budget.spend_today(),
        },
    }
