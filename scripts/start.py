"""Boot the Flask app + APScheduler. Localhost only — no auth in P0."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import state  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from orchestrator.config import CONFIG  # noqa: E402
from orchestrator.scheduler import start_scheduler  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    state.init_db()
    app = create_app()
    scheduler = start_scheduler()
    try:
        app.run(host=CONFIG.flask_host, port=CONFIG.flask_port, debug=CONFIG.flask_debug, use_reloader=False)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
