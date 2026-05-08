# Paper Factory

A multi-agent research-paper system organized as a research lab. PI = the user.
Agents = lab members. Papers = projects. The PI provides sharp attention and
remarks; the lab does the rest.

See `DESIGN.md` for the full design (v3).

## Status

**P1 — seeded single-paper loop.** Real Problem-Smith → Structurer → Writer →
Polisher pipeline. PI seeds a paper from the dashboard or CLI; the lab drafts
an abstract, proposes a structure, fills each section, polishes, and surfaces
"send to review" once everything compiles. The LaTeX compile gate is active
(`latexmk`); failures roll the workspace back. Mock LLM is on by default so
the loop runs without an API key — flip `USE_MOCK_LLM=0` and set
`ANTHROPIC_API_KEY` for real Claude calls. Reviewer panel + Critics + Scout +
Lab Notebook arrive in P2/P4.

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

From the dashboard: fill in title + problem statement at the top of the
meeting view and click "+ New project."

Or from the CLI:

```bash
python scripts/new_paper.py "Metric sortition under noisy signals" \
  "When does random sampling by metric sortition maximize welfare under noisy preference signals?"
```

The lab will emit a `decision_needed` report asking you to approve the
abstract. Approve it, approve the proposed structure, then watch the writers
+ polishers fill in five sections. When everything is polished the lab fires
a "Draft complete — send to review?" report and the compiled `main.pdf` is
sitting in `papers/<id>/workspace/`.

## Where to look

- Failure modes & recovery: `DESIGN.md` §12
- File layout: `DESIGN.md` §14
- Build phases: `DESIGN.md` §15
- Lab principles (auto-generated mirror): `lab/PRINCIPLES.md`

## Configuration

All runtime knobs live in `.env`. Notable:

- `USE_MOCK_LLM=1` — canned, role-aware mock responses; no API key needed.
- `STUB_LATEX_COMPILE=0` — run real `latexmk` (default; install TeXLive first).
- `STUB_GITHUB_MIRROR=1` — local git only, no `git push`.
- `STUB_PYTHON_SANDBOX=1` — basic subprocess + 60s timeout instead of real resource limits.
- `STUB_SCOUT=1` — Scout returns canned literature instead of hitting arXiv.
