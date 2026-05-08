"""Flask app. See DESIGN.md §9.

P0 routes:
  GET  /                       — meeting view (default)
  GET  /flat                   — flat severity-sorted view
  GET  /reports/<id>           — decision card (HTMX swap or full page)
  POST /papers                 — create a new paper
  POST /papers/<id>/pause      — toggle paused flag
  POST /reports/<id>/resolve   — resolve / kill / defer + remark + outcome rating
  POST /principles             — manual promote
  POST /principle_candidates/<id>/decide  — promote / dismiss a candidate

No auth in P0 — bind to localhost only.
"""
from __future__ import annotations

import logging

from flask import Flask, redirect, render_template, request, url_for, abort

from agents import dummy as _dummy_agent  # noqa: F401  — registers the agent
from lab import principles as principles_mod
from lab import remarks as remarks_mod

from . import meeting, state
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

    # ---------- Mutating endpoints ---------------------------------------

    @app.post("/papers")
    def create_paper():
        title = request.form.get("title", "").strip()
        problem = request.form.get("problem", "").strip()
        venue = request.form.get("target_venue", "").strip() or None
        if not title:
            abort(400, "title required")
        paper_id = state.create_paper(title, problem, venue)
        dispatcher.trigger_paper(paper_id)
        return redirect(url_for("meeting_view"))

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

        action = request.form.get("action", "approve")  # approve|kill|defer
        remark_text = request.form.get("remark", "").strip()
        save_principle = request.form.get("save_as_principle") == "on"
        principle_scope = request.form.get("principle_scope", "lab")
        outcome = request.form.get("outcome")  # accept|rework|kill

        target_status = {"approve": "resolved", "kill": "archived", "defer": "deferred"}.get(action, "resolved")
        state.resolve_report(report_id, status=target_status)

        remark_id = None
        if remark_text:
            remark_id = remarks_mod.log_remark(
                report_id=report_id,
                paper_id=report["paper_id"],
                role=report["role"],
                text=remark_text,
                pi_tagged=save_principle,
                suggested_scope=principle_scope if save_principle else None,
            )
            if save_principle:
                scope_value = _resolve_scope(principle_scope, report)
                principles_mod.insert_principle(remark_text, scope_value, derived_from=remark_id)
                principles_mod.regenerate_markdown()

        if outcome in ("accept", "rework", "kill"):
            state.record_task_outcome(
                role=report["role"],
                persona_name=report.get("persona_name"),
                paper_id=report["paper_id"],
                report_id=report_id,
                outcome=outcome,
            )

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
