"""Prompt assembly + LLM invocation. See DESIGN.md §4.2.

In P1 the assembler is real — base prompt + honesty + scoped principles +
persona memory + paper context + task instructions — and we ship a
role-aware mock LLM so the loop produces compilable output without an API
key. Flip `USE_MOCK_LLM=0` and set `ANTHROPIC_API_KEY` to use real Claude.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .config import CONFIG, ROOT
from . import budget

log = logging.getLogger(__name__)

PROMPTS_DIR = ROOT / "prompts"


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


def applicable_principles(role: str, paper_id: str | None) -> str:
    """Return concatenated principle text, lab → role → project order."""
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
    """Compact paper-state summary. Aim for ≤ 2K tokens."""
    if not paper_id:
        return ""
    from . import paper_state
    data = paper_state.read(paper_id)
    # Trim — we don't need the full decisions log every time.
    trimmed = {
        "paper_id": data.get("paper_id"),
        "title": data.get("title"),
        "status": data.get("status"),
        "target_venue": data.get("target_venue"),
        "seed_problem": data.get("seed_problem"),
        "abstract": data.get("abstract"),
        "formal_problem": data.get("formal_problem"),
        "structure": data.get("structure"),
        "claims": data.get("claims"),
        "sims": data.get("sims"),
        "recent_decisions": (data.get("decisions_log") or [])[-3:],
    }
    return "# Paper context\n```json\n" + json.dumps(trimmed, indent=2) + "\n```"


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
        user = user.replace("{" + k + "}", str(v) if v is not None else "")
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


# Pricing per million tokens (rough, for budget telemetry).
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
    max_tokens: int = 8000,
    task: dict[str, Any] | None = None,
) -> LLMResponse:
    """Invoke Claude (or the mock) and log spend."""
    start = time.monotonic()

    if CONFIG.use_mock_llm or not CONFIG.anthropic_api_key:
        text = mock_response(role, prompt, task or {})
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


def call_llm_json(
    role: str,
    paper_id: str | None,
    persona_name: str | None,
    model: str,
    prompt: AssembledPrompt,
    max_tokens: int = 8000,
    task: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], LLMResponse]:
    """Call the LLM and parse a JSON object from its response.

    Tolerates fenced code blocks and surrounding prose. Raises ValueError if
    no JSON object can be extracted.
    """
    resp = call_llm(role, paper_id, persona_name, model, prompt, max_tokens, task=task)
    return _extract_json(resp.text), resp


def _extract_json(text: str) -> dict[str, Any]:
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    # Find the outermost balanced { ... } in the text.
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in response")


# ---------- Mock LLM ------------------------------------------------------

def mock_response(role: str, prompt: AssembledPrompt, task: dict[str, Any]) -> str:
    """Role-aware canned outputs. Just realistic enough that the pipeline
    produces compileable LaTeX and a coherent state.json."""
    if role == "problem_smith":
        return _mock_problem_smith(task)
    if role == "structurer":
        return _mock_structurer(task)
    if role == "writer":
        return _mock_writer(task)
    if role == "polisher":
        return _mock_polisher(task)
    return json.dumps({
        "kind": "fyi",
        "summary": f"[mock] {role} ran",
        "severity": "green",
        "self_diagnosis": "mock LLM",
        "confidence": "low",
        "made_up_flag": False,
    })


def _mock_problem_smith(task: dict[str, Any]) -> str:
    seed = (task.get("seed_problem") or "an open question in the area").strip()
    title = task.get("title") or "Investigations"
    return json.dumps({
        "title": title,
        "abstract": (
            f"We study {seed.lower()}. We formalize the setting, present a model "
            "that captures the salient frictions, and derive structural results "
            "that connect classical limits to noisy regimes. Simulations on a "
            "synthetic instance illustrate the qualitative predictions."
        ),
        "formal_problem": (
            f"Setting. Let X be a finite set of agents and S the set of states. "
            f"Given the seed concern --- {seed} --- we study the family of "
            "policies that map observed signals to actions and characterize "
            "their welfare under varying noise."
        ),
        "claims": [
            {"id": "thm1", "text": "Under symmetric noise, the canonical policy is welfare-optimal."},
            {"id": "lem1", "text": "Welfare is monotone non-increasing in signal noise."},
        ],
    })


def _mock_structurer(task: dict[str, Any]) -> str:
    return json.dumps({
        "sections": [
            {"id": "intro",       "title": "Introduction"},
            {"id": "related",     "title": "Related Work"},
            {"id": "model",       "title": "Model"},
            {"id": "theory",      "title": "Main Results"},
            {"id": "discussion",  "title": "Discussion"},
        ],
    })


def _mock_writer(task: dict[str, Any]) -> str:
    sid = task.get("section_id") or "section"
    title = task.get("title") or sid.title()
    body = {
        "intro": (
            "Recent interest in the area has produced a patchwork of partial "
            "results. We provide a unifying frame, prove the canonical case, "
            "and discuss extensions. The contribution is threefold: a clean "
            "model, a structural theorem, and a simulation that situates the "
            "predictions empirically."
        ),
        "related": (
            "Three lines of work bear on our question. The first studies "
            "deterministic versions; the second introduces noise but assumes "
            "full observability; the third looks at observability but in "
            "different welfare formalisms. None addresses the joint setting."
        ),
        "model": (
            "We model the system as a tuple $(X, S, \\sigma, u)$ where $X$ is "
            "the agent set, $S$ the state space, $\\sigma$ a noisy signal "
            "function, and $u$ the welfare functional. Throughout we assume "
            "$\\sigma$ has bounded support and $u$ is Lipschitz."
        ),
        "theory": (
            "Our main result is Theorem~\\ref{thm:welfare}: under symmetric "
            "noise, the canonical policy attains the welfare optimum. The "
            "proof proceeds by perturbation around the noiseless limit and "
            "exploits Lipschitz continuity of $u$."
        ),
        "discussion": (
            "We have characterized welfare in a clean noisy-signal setting. "
            "The main open questions concern asymmetric noise, where the "
            "perturbation argument breaks, and the effect of correlated "
            "signals across agents. We sketch both directions briefly."
        ),
    }.get(sid, "Body to be drafted.")
    return json.dumps({"latex": f"\\section{{{title}}}\\label{{sec:{sid}}}\n\n{body}\n"})


def _mock_polisher(task: dict[str, Any]) -> str:
    # The mock Polisher is never actually invoked — Polisher.execute() takes
    # the mock-mode shortcut and prepends a marker to the existing file.
    return json.dumps({"latex": task.get("current_latex") or ""})
