"""Regenerate lab/PRINCIPLES.md from the principles store."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import principles as principles_mod  # noqa: E402
from orchestrator.config import ensure_dirs  # noqa: E402


def main() -> None:
    ensure_dirs()
    principles_mod.regenerate_markdown()
    print("OK — lab/PRINCIPLES.md regenerated.")


if __name__ == "__main__":
    main()
