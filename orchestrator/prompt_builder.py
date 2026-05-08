"""Prompt assembly + LLM invocation. See DESIGN.md §4.2.

In P0 most pieces are stubs — the dummy agent doesn't actually call the LLM.
This module establishes the shape so later phases can fill in role prompts,
persona memory, and real Claude calls.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CONFIG, ROOT
from . import budget

log = logging.getLogger(__name__)

PROMPTS_DIR = ROOT / "prompts"


# ---------- Static-text loaders -------------------------------------------

def _read(rel: str) -> str:
    p = PROMPTS_DIR / rel
    if not p.exists():
        return ""
    return p.read_text()


def base_role_prompt(role: str) -> str:
    return _read(f"base/{role}.md")


def task_template(role: str) -> str:
    return _read(f"tasks/{role}.md")


def honesty_preamble() -> str:
    return _read("snippets/honesty.md")


def critic_pushback() -> str:
    return _read("snippets/critic_pushback.md")


# ---------- Dynamic context -----------------------------------------------

def applicable_principles(role: str, paper_id: str | None) -> str:
    """Return concatenated principle text, lab → role → project order.

    Implemented locally (not via lab.principles) to keep the prompt builder
    importable from agent threads without circular deps.
    """
    from . import state  # local import to dodge circular reference at boot

    scopes = ["lab", f"role:{role}"]
    if paper_id:
        scopes.append(f"project:{paper_id}")
    placeholders = ",".join("?" * len(scopes))
    sql = (
        "SELECT scope, text FROM principles "
        f"WHERE scope IN ({placeholders}) AND deprecated_at IS NULL "
        "ORDER BY CASE scope "
        "WHEN 'lab' THEN 0 "
        f"WHEN 'role:{role}' THEN 1 "
        "ELSE 2 END, created_at"
    )
    with state.connect() as conn:
        rows = conn.execute(sql, scopes).fetchall()
    if not rows:
        return ""
    lines = ["# Lab principles in scope"]
    for r in rows:
        lines.append(f"- ({r['scope']}) {r['text']}")
    return "\n".join(lines)


def persona_memory(role: str, name: str | None) -> str:
    if not name:
        return ""
    p = ROOT / "agents" / "memory" / role / f"{name}.md"
    if not p.exists():
        return ""
    return p.read_text()


def paper_context(paper_id: str | None, role: str, task: dict[str, Any]) -> str:
    """Compact paper-state summary. Aim for ≤ 2K tokens.

    P0 just stuffs `state.json` and the task object in. Refined renderers come
    in P1+.
    """
    if not paper_id:
        return ""
    state_json = CONFIG.papers_dir / paper_id / "state.json"
    paper_state: dict[str, Any] = {}
    if state_json.exists():
        try:
            paper_state = json.loads(state_json.read_text())
        except json.JSONDecodeError:
            paper_state = {"_corrupted": True}
    return "# Paper context\n```json\n" + json.dumps(paper_state, indent=2) + "\n```"


@dataclass
class AssembledPrompt:
    system: str
    user: str


def assemble(role: str, persona_name: str | None, paper_id: str | None, task: dict[str, Any]) -> AssembledPrompt:
    parts = [
        base_role_prompt(role),
        honesty_preamble(),
        applicable_principles(role, paper_id),
        persona_memory(role, persona_name),
        paper_context(paper_id, role, task),
    ]
    if role.startswith("critic_"):
        parts.append(critic_pushback())
    system = "\n\n".join(p for p in parts if p)

    template = task_template(role) or "Task: {task_json}"
    user = template.replace("{task_json}", json.dumps(task, indent=2))
    for k, v in task.items():
        user = user.replace("{" + k + "}", str(v))
    return AssembledPrompt(system=system, user=user)


# ---------- LLM invocation ------------------------------------------------

@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    model: str


# Pricing per million tokens (rough, used only for budget telemetry).
_PRICES = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "mock": (0.0, 0.0),
}


def call_llm(
    role: str,
    paper_id: str | None,
    persona_name: str | None,
    model: str,
    prompt: AssembledPrompt,
    max_tokens: int = 20000,
) -> LLMResponse:
    """Invoke Claude (or the mock) and log spend."""
    start = time.monotonic()

    if CONFIG.use_mock_llm or not CONFIG.anthropic_api_key:
        text = _mock_response(role, prompt)
        in_tok = max(1, len(prompt.system) + len(prompt.user)) // 4
        out_tok = max(1, len(text)) // 4
        used_model = "mock"
    else:
        from anthropic import Anthropic  # imported lazily so mock mode has no hard dep
        client = Anthropic(api_key=CONFIG.anthropic_api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        in_tok = msg.usage.input_tokens
        out_tok = msg.usage.output_tokens
        used_model = model

    in_price, out_price = _PRICES.get(used_model, (0.0, 0.0))
    cost = (in_tok * in_price + out_tok * out_price) / 1_000_000
    latency_ms = int((time.monotonic() - start) * 1000)

    budget.log_call(paper_id, role, persona_name, used_model, in_tok, out_tok, cost, latency_ms)
    return LLMResponse(text, in_tok, out_tok, cost, latency_ms, used_model)


def _mock_response(role: str, prompt: AssembledPrompt) -> str:
    """Stand-in LLM. Returns a JSON-shaped Report so agents can round-trip the schema."""
    return json.dumps({
        "kind": "fyi",
        "role": role,
        "summary": f"[mock] {role} ran with {len(prompt.user)} chars of task",
        "severity": "green",
        "self_diagnosis": "mock LLM — no real reasoning",
        "confidence": "low",
        "made_up_flag": False,
    })
