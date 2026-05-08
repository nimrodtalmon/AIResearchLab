"""Flask app. See DESIGN.md §9.

P1 routes:
  GET  /                       — meeting view (default)
  GET  /flat                   — flat severity-sorted view
  GET  /reports/<id>           — decision card
  GET  /papers/<id>            — paper detail (workspace files, decisions log)
  POST /papers                 — create a new paper (seed Problem-Smith)
  POST /papers/<id>/pause      — toggle paused flag
  POST /reports/<id>/resolve   — resolve / kill / defer + remark + outcome
                                 + state-machine transitions per role+option
  POST /principles             — manual promote
  POST /principle_candidates/<id>/decide  — promote / dismiss a candidate

No auth in P0/P1 — bind to localhost only.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

import agents  # noqa: F401  — registers all agents at import time
from lab import principles as principles_mod
from lab import remarks as remarks_mod

from . import meeting, paper_state, state
from .config import CONFIG, ROOT, ensure_dirs
from .dispatcher import get_dispatcher

log = logging.getLogger(__name__)


def create_app() -> Flask:
    ensure_dirs()
    app = Flask(
        __name__,
        template_folder=str((ROOT / "dashboard" / "templates").resolve()),
        static_folder=str((ROOT / "dashboard" / "static").resolve()),
    )
    app.secret_key = CONFIG.flask_secret_key

    dispatcher = get_dispatcher()

    # ---------- Views ----------------------------------------------------

    @app.get("/")
    def meeting_view():
        view = meeting.lab_meeting()
        return render_template(
            "meeting.html",
            view=view,
            candidates=principles_mod.list_candidates(),
            principles=principles_mod.list_active(),
        )

    @app.get("/flat")
    def flat_view():
        view = meeting.flat_view()
        return render_template("flat.html", view=view)

    @app.get("/reports/<report_id>")
    def report_card(report_id: str):
        report = state.get_report(report_id)
        if not report:
            abort(404)
        partial = request.headers.get("HX-Request") == "true"
        template = "partials/card_body.html" if partial else "card.html"
        return render_template(template, report=report)

    @app.get("/papers/<paper_id>")
    def paper_detail(paper_id: str):
        paper = state.get_paper(paper_id)
        if not paper:
            abort(404)
        data = paper_state.read(paper_id)
        files = _list_workspace_files(paper_id)
        reports = state.list_reports(paper_id=paper_id)
        return render_template("paper.html", paper=paper, state=data, files=files, reports=reports)

    @app.get("/papers/<paper_id>/file")
    def paper_file(paper_id: str):
        rel = request.args.get("rel", "")
        ws = CONFIG.papers_dir / paper_id / "workspace"
        path = (ws / rel).resolve()
        if not str(path).startswith(str(ws.resolve())) or not path.is_file():
            abort(404)
        return path.read_text(errors="replace"), 200, {"Content-Type": "text/plain; charset=utf-8"}

    # ---------- Mutating endpoints ---------------------------------------

    @app.post("/papers")
    def create_paper():
        title = request.form.get("title", "").strip()
        problem = request.form.get("problem", "").strip()
        venue = request.form.get("target_venue", "").strip() or None
        if not title:
            abort(400, "title required")
        paper_id = state.create_paper(title, problem, venue)
        # Seed state.json so Problem-Smith has the seed problem to work from.
        from .workspace import ensure_workspace
        ensure_workspace(paper_id)
        paper_state.write(paper_id, paper_state.empty(
            paper_id=paper_id, title=title, target_venue=venue, problem=problem,
        ))
        dispatcher.trigger_paper(paper_id)
        return redirect(url_for("paper_detail", paper_id=paper_id))

    @app.post("/papers/<paper_id>/pause")
    def toggle_pause(paper_id: str):
        paper = state.get_paper(paper_id)
        if not paper:
            abort(404)
        state.set_paper_paused(paper_id, not paper["paused"])
        return redirect(url_for("meeting_view"))

    @app.post("/reports/<report_id>/resolve")
    def resolve(report_id: str):
        report = state.get_report(report_id)
        if not report:
            abort(404)

        action = request.form.get("action", "approve")     # approve|kill|defer
        option_id = request.form.get("option")             # which radio
        remark_text = request.form.get("remark", "").strip()
        save_principle = request.form.get("save_as_principle") == "on"
        principle_scope = request.form.get("principle_scope", "lab")
        outcome = request.form.get("outcome")              # accept|rework|kill

        target_status = {"approve": "resolved", "kill": "archived", "defer": "deferred"}.get(action, "resolved")
        state.resolve_report(report_id, status=target_status)

        remark_id = None
        if remark_text:
            remark_id = remarks_mod.log_remark(
                report_id=report_id, paper_id=report["paper_id"],
                role=report["role"], text=remark_text,
                pi_tagged=save_principle,
                suggested_scope=principle_scope if save_principle else None,
            )
            if save_principle:
                scope_value = _resolve_scope(principle_scope, report)
                principles_mod.insert_principle(remark_text, scope_value, derived_from=remark_id)
                principles_mod.regenerate_markdown()

        if outcome in ("accept", "rework", "kill"):
            state.record_task_outcome(
                role=report["role"], persona_name=report.get("persona_name"),
                paper_id=report["paper_id"], report_id=report_id, outcome=outcome,
            )

        # Drive paper-status state machine based on the role + action + option.
        if action == "approve" and report["paper_id"]:
            _apply_state_transition(report, option_id or "approve", remark_text)
        elif action == "kill" and report["paper_id"]:
            state.update_paper(report["paper_id"], status="killed")

        if report["paper_id"]:
            dispatcher.trigger_paper(report["paper_id"])
        return redirect(url_for("meeting_view"))

    @app.post("/principles")
    def add_principle():
        text = request.form.get("text", "").strip()
        scope = request.form.get("scope", "lab")
        if not text:
            abort(400, "text required")
        principles_mod.insert_principle(text, scope)
        principles_mod.regenerate_markdown()
        return redirect(url_for("meeting_view"))

    @app.post("/principle_candidates/<candidate_id>/decide")
    def decide_candidate(candidate_id: str):
        decision = request.form.get("decision", "dismissed")
        if decision == "promoted":
            text = request.form.get("text", "").strip()
            scope = request.form.get("scope", "lab")
            if text:
                principles_mod.insert_principle(text, scope, derived_from=candidate_id)
                principles_mod.regenerate_markdown()
        principles_mod.decide_candidate(candidate_id, decision)
        return redirect(url_for("meeting_view"))

    # ---------- Health ---------------------------------------------------

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "papers": len(state.list_papers())}

    return app


def _resolve_scope(scope: str, report: dict) -> str:
    if scope == "lab":
        return "lab"
    if scope == "role":
        return f"role:{report['role']}"
    if scope == "paper" and report.get("paper_id"):
        return f"project:{report['paper_id']}"
    return "lab"


def _apply_state_transition(report: dict, option_id: str, remark_text: str) -> None:
    """Translate PI's action on a Decision Card into a paper-status transition.

    See DESIGN.md §5.3 for the state machine. P1 only handles the four real
    agents (problem_smith, structurer, writer, polisher) and the
    ready-for-review handoff.
    """
    role = report["role"]
    paper_id = report["paper_id"]
    paper = state.get_paper(paper_id)
    if not paper:
        return

    if option_id == "kill":
        state.update_paper(paper_id, status="killed")
        return

    if role == "problem_smith":
        if option_id == "approve" and paper["status"] == "formal":
            state.update_paper(paper_id, status="drafting")
            paper_state.log_decision(paper_id, by="PI", what="Approved abstract", report_id=report["id"])
        elif option_id == "regen":
            data = paper_state.read(paper_id)
            data["abstract"] = ""
            data["formal_problem"] = ""
            data["claims"] = []
            data["status"] = "idea"
            paper_state.write(paper_id, data)
            state.update_paper(paper_id, status="idea")
            paper_state.log_decision(paper_id, by="PI", what="Asked Problem-Smith to regen abstract", report_id=report["id"])
        return

    if role == "structurer":
        if option_id == "approve":
            data = paper_state.read(paper_id)
            data["structure_approved"] = True
            paper_state.write(paper_id, data)
            paper_state.log_decision(paper_id, by="PI", what="Approved structure", report_id=report["id"])
        elif option_id == "regen":
            data = paper_state.read(paper_id)
            data.setdefault("structure", {})["sections"] = []
            data["structure_approved"] = False
            paper_state.write(paper_id, data)
            paper_state.log_decision(paper_id, by="PI", what="Asked Structurer to regen", report_id=report["id"])
        elif option_id == "send_to_review":
            # Ready-for-review handoff. P4 brings the reviewer panel online;
            # P1 stops the loop here cleanly.
            state.update_paper(paper_id, status="reviewing")
            paper_state.log_decision(paper_id, by="PI", what="Sent draft to review", report_id=report["id"])
        elif option_id == "more_revisions":
            paper_state.log_decision(paper_id, by="PI", what="Hold for revisions; remark: " + (remark_text or "(none)"), report_id=report["id"])


def _list_workspace_files(paper_id: str) -> list[dict[str, str]]:
    ws = CONFIG.papers_dir / paper_id / "workspace"
    if not ws.exists():
        return []
    out: list[dict[str, str]] = []
    for p in sorted(ws.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(ws)
        if any(part.startswith(".git") for part in rel.parts):
            continue
        if p.suffix.lower() in {".aux", ".log", ".out", ".fls", ".fdb_latexmk"}:
            continue
        out.append({"rel": str(rel), "size": p.stat().st_size})
    return out
