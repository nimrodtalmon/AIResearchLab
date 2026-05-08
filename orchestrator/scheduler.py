"""APScheduler setup. See DESIGN.md §4.1.

Time-based jobs:
- safety sweep: every DISPATCHER_SWEEP_SECONDS, re-evaluate all active papers.
- nightly backup: dump SQLite to backups/state-YYYYMMDD.sql at 03:00.

Real Scout / Gap-Hunter / Lab Notebook crons land in P2.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from .config import CONFIG
from .dispatcher import get_dispatcher

log = logging.getLogger(__name__)


def _safety_sweep() -> None:
    log.debug("safety sweep")
    get_dispatcher().trigger_all()


def _nightly_backup() -> None:
    if not CONFIG.db_path.exists():
        return
    dest = CONFIG.backups_dir / f"state-{datetime.utcnow():%Y%m%d}.sql"
    CONFIG.backups_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(CONFIG.db_path, dest.with_suffix(".db"))
    _prune_old_backups(CONFIG.backups_dir)


def _prune_old_backups(directory: Path, keep: int = 30) -> None:
    files = sorted(directory.glob("state-*.db"), reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_safety_sweep, "interval", seconds=CONFIG.dispatcher_sweep_seconds, id="sweep")
    sched.add_job(_nightly_backup, "cron", hour=3, minute=0, id="backup")
    sched.start()
    return sched
