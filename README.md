# Paper Factory

A multi-agent research-paper system organized as a research lab. PI = the user.
Agents = lab members. Papers = projects. The PI provides sharp attention and
remarks; the lab does the rest.

See `DESIGN.md` for the full design (v3).

## Status

**P0 skeleton.** End-to-end dispatch + queue + render path verified with a
single dummy agent. Real agents and external integrations land in later phases.

## Quick install

```bash
pip install -e .
cp .env.example .env
```

## First-day bootstrap

```bash
python scripts/init_db.py
python scripts/start.py
```

Dashboard: http://localhost:5000

No auth in P0/P1 — bind to localhost only.

## Seed a paper

```bash
python scripts/new_paper.py "Title" "Problem statement"
```

The orchestrator will dispatch a dummy agent that emits a canned Report; the
report shows up in the lab meeting view.

## Where to look

- Failure modes & recovery: `DESIGN.md` §12
- File layout: `DESIGN.md` §14
- Build phases: `DESIGN.md` §15
- Lab principles (auto-generated mirror): `lab/PRINCIPLES.md`

## Configuration

All runtime knobs live in `.env`. Notable:

- `USE_MOCK_LLM=1` — canned LLM responses; no API key needed.
- `STUB_LATEX_COMPILE=1` — pretend `latexmk` succeeded.
- `STUB_GITHUB_MIRROR=1` — local git only, no `git push`.
- `STUB_PYTHON_SANDBOX=1` — basic subprocess + 60s timeout instead of real resource limits.
- `STUB_SCOUT=1` — Scout returns canned literature instead of hitting arXiv.
