"""Runtime configuration. All knobs are env-driven so they're tunable from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip() in ("1", "true", "True", "yes")


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p


@dataclass(frozen=True)
class Config:
    flask_host: str
    flask_port: int
    flask_debug: bool
    flask_secret_key: str

    data_dir: Path
    papers_dir: Path
    backups_dir: Path
    lab_dir: Path
    db_path: Path

    use_mock_llm: bool
    anthropic_api_key: str

    thread_pool_size: int
    dispatcher_sweep_seconds: int

    per_paper_daily_cap: float
    global_daily_cap: float

    stub_latex_compile: bool
    stub_github_mirror: bool
    stub_python_sandbox: bool
    stub_scout: bool


def load() -> Config:
    return Config(
        flask_host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        flask_port=int(os.environ.get("FLASK_PORT", "5000")),
        flask_debug=_bool("FLASK_DEBUG"),
        flask_secret_key=os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me"),
        data_dir=_path("DATA_DIR", "./data"),
        papers_dir=_path("PAPERS_DIR", "./papers"),
        backups_dir=_path("BACKUPS_DIR", "./backups"),
        lab_dir=_path("LAB_DIR", "./lab"),
        db_path=_path("DB_PATH", "./data/state.db"),
        use_mock_llm=_bool("USE_MOCK_LLM", "1"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        thread_pool_size=int(os.environ.get("THREAD_POOL_SIZE", "4")),
        dispatcher_sweep_seconds=int(os.environ.get("DISPATCHER_SWEEP_SECONDS", "300")),
        per_paper_daily_cap=float(os.environ.get("PER_PAPER_DAILY_CAP", "50")),
        global_daily_cap=float(os.environ.get("GLOBAL_DAILY_CAP", "200")),
        stub_latex_compile=_bool("STUB_LATEX_COMPILE", "1"),
        stub_github_mirror=_bool("STUB_GITHUB_MIRROR", "1"),
        stub_python_sandbox=_bool("STUB_PYTHON_SANDBOX", "1"),
        stub_scout=_bool("STUB_SCOUT", "1"),
    )


CONFIG = load()


def ensure_dirs() -> None:
    for d in (CONFIG.data_dir, CONFIG.papers_dir, CONFIG.backups_dir, CONFIG.lab_dir):
        d.mkdir(parents=True, exist_ok=True)
