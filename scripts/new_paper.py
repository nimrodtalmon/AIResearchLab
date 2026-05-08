"""Seed a paper from the CLI. See DESIGN.md §6.2."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import dummy as _dummy  # noqa: F401, E402  — registers dummy agent
from orchestrator import state  # noqa: E402
from orchestrator.config import ensure_dirs  # noqa: E402
from orchestrator.dispatcher import get_dispatcher  # noqa: E402
from orchestrator.workspace import ensure_workspace  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a new paper.")
    parser.add_argument("title")
    parser.add_argument("problem", nargs="?", default="")
    parser.add_argument("--venue", default=None)
    args = parser.parse_args()

    ensure_dirs()
    state.init_db()
    paper_id = state.create_paper(args.title, args.problem, args.venue)
    ensure_workspace(paper_id)
    get_dispatcher().trigger_paper(paper_id)
    print(f"OK — paper {paper_id} created and dispatched.")


if __name__ == "__main__":
    main()
