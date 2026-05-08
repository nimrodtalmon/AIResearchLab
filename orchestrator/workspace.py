"""Workspace transactions. See DESIGN.md §4.3.

For P0 we run with stubbed compile / push gates — see .env STUB_* flags. Real
LaTeX and GitHub integration land in later phases.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from git import Repo

from .config import CONFIG

log = logging.getLogger(__name__)

# Per-paper mutexes. The dict itself is guarded so we hand out a stable lock
# object per paper.
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(paper_id: str) -> threading.Lock:
    with _locks_guard:
        if paper_id not in _locks:
            _locks[paper_id] = threading.Lock()
        return _locks[paper_id]


class GateFailure(RuntimeError):
    """Raised when a compile / sandbox gate rejects a transaction."""


class MutexTimeout(RuntimeError):
    """Raised when we can't acquire the workspace mutex within the deadline."""


@dataclass
class WorkspaceContext:
    paper_id: str
    role: str
    persona_name: str | None
    workspace: Path
    repo: Repo


def ensure_workspace(paper_id: str) -> Path:
    """Create the per-paper workspace + git repo if missing. Idempotent."""
    workspace = CONFIG.papers_dir / paper_id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (CONFIG.papers_dir / paper_id / "transcripts").mkdir(parents=True, exist_ok=True)

    if not (workspace / ".git").exists():
        repo = Repo.init(workspace, initial_branch="main")
        # Seed with a no-op LaTeX file so the compile gate has something to act on.
        main_tex = workspace / "main.tex"
        if not main_tex.exists():
            main_tex.write_text(
                "\\documentclass{article}\n"
                "\\title{Untitled}\n"
                "\\author{The Lab}\n"
                "\\begin{document}\\maketitle\n"
                "\\section{Placeholder}\nWork in progress.\n"
                "\\end{document}\n"
            )
        gitignore = workspace / ".gitignore"
        gitignore.write_text("*.aux\n*.log\n*.out\n*.pdf\n")
        repo.index.add([str(main_tex.relative_to(workspace)), ".gitignore"])
        # Configure local identity so commits work without a global git config.
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Paper Factory")
            cw.set_value("user", "email", "lab@local")
        repo.index.commit("init: seed workspace")
    return workspace


@contextmanager
def transaction(
    paper_id: str,
    role: str,
    persona_name: str | None = None,
    timeout_seconds: int = 300,
) -> Iterator[WorkspaceContext]:
    """Acquire mutex, snapshot HEAD, yield ctx, commit on success / reset on failure.

    On exit-without-exception: stage all changes, run gates, commit (and push
    if mirror enabled). On exception or gate failure: hard-reset to snapshot.
    """
    workspace = ensure_workspace(paper_id)
    repo = Repo(workspace)
    lock = _lock_for(paper_id)

    if not lock.acquire(timeout=timeout_seconds):
        raise MutexTimeout(f"workspace mutex timeout for {paper_id}")

    try:
        if not CONFIG.stub_github_mirror:
            try:
                repo.remotes.origin.pull("main", rebase=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("pull --rebase failed for %s: %s", paper_id, exc)

        snapshot = repo.head.commit.hexsha
        ctx = WorkspaceContext(paper_id, role, persona_name, workspace, repo)
        try:
            yield ctx
        except Exception:
            repo.git.reset("--hard", snapshot)
            repo.git.clean("-fd")
            raise

        if not _has_changes(repo):
            return

        try:
            _run_gates(workspace)
            repo.git.add("-A")
            author = f"{role}[{persona_name}]" if persona_name else role
            repo.index.commit(f"{author}: agent commit")
        except Exception:
            repo.git.reset("--hard", snapshot)
            repo.git.clean("-fd")
            raise

        if not CONFIG.stub_github_mirror:
            try:
                repo.remotes.origin.push("main")
            except Exception as exc:  # noqa: BLE001
                log.warning("push failed for %s: %s", paper_id, exc)
    finally:
        lock.release()


def _has_changes(repo: Repo) -> bool:
    return bool(repo.is_dirty(untracked_files=True))


def _run_gates(workspace: Path) -> None:
    """Run language-specific gates. Reset is the caller's job on failure."""
    tex_files = list(workspace.glob("**/*.tex"))
    py_files = list((workspace / "sim").glob("**/*.py")) if (workspace / "sim").exists() else []

    if tex_files and not CONFIG.stub_latex_compile:
        if shutil.which("latexmk") is None:
            raise GateFailure("latexmk not on PATH; set STUB_LATEX_COMPILE=1 to bypass")
        result = subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "main.tex"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise GateFailure(f"latexmk failed:\n{result.stdout[-2000:]}\n{result.stderr[-1000:]}")

    for py in py_files:
        result = subprocess.run(
            ["python", "-m", "py_compile", str(py)], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise GateFailure(f"py_compile failed for {py.name}:\n{result.stderr}")
        if not CONFIG.stub_python_sandbox:
            run = subprocess.run(
                ["python", str(py)], cwd=workspace, capture_output=True, text=True, timeout=60
            )
            if run.returncode != 0:
                raise GateFailure(f"sim run failed for {py.name}:\n{run.stderr[-2000:]}")
