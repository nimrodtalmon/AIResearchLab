"""Apply migrations from scripts/migrations/ in order."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import state  # noqa: E402
from orchestrator.config import ensure_dirs  # noqa: E402


def main() -> None:
    ensure_dirs()
    state.init_db()
    print("OK — schema applied.")


if __name__ == "__main__":
    main()
