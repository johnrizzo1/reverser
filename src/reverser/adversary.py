"""Adversarial second-model validation: a one-shot, read-only skeptic that tries
to refute a claim using only the provided evidence."""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from typing import Optional

from .backends import create_backend

_VERDICTS = {"refuted", "upheld", "inconclusive"}
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)
_MD_VERDICT = re.compile(r"verdict\s*[:=]\s*([a-z]+)", re.IGNORECASE)
_MD_REASON = re.compile(r"reasoning\s*[:=]\s*(.+)", re.IGNORECASE)

_SYSTEM = (
    "You are a skeptical security reviewer. Your job is to REFUTE the claim below "
    "using ONLY the evidence provided — look for missing links, alternative "
    "explanations, and unproven assumptions. If you genuinely cannot refute it, say "
    "so. Respond with a fenced ```json block: "
    '{"verdict": "refuted|upheld|inconclusive", "reasoning": "<one sentence>"}. '
    "verdict='refuted' means the evidence does NOT support the claim; 'upheld' "
    "means you could not refute it; 'inconclusive' means you cannot tell."
)


@dataclass
class Verdict:
    verdict: str  # "refuted" | "upheld" | "inconclusive"
    reasoning: str = ""
    model: Optional[str] = None
    cost: float = 0.0
    turns: int = 0


def parse_verdict(text: str, *, model: Optional[str] = None) -> Verdict:
    """Parse a verdict. JSON block preferred, then markdown VERDICT:/REASONING:;
    unparseable or unknown verdict -> inconclusive."""
    text = text or ""
    obj = None
    for pat in (_JSON_BLOCK, _BARE_JSON):
        for m in pat.finditer(text):
            try:
                cand = _json.loads(m.group(1))
            except (ValueError, TypeError):
                continue
            if isinstance(cand, dict) and "verdict" in cand:
                obj = cand
                break
        if obj is not None:
            break
    if obj is not None:
        verdict = str(obj.get("verdict", "")).strip().lower()
        reasoning = str(obj.get("reasoning", "")).strip()
    else:
        mv = _MD_VERDICT.search(text)
        verdict = mv.group(1).strip().lower() if mv else ""
        mr = _MD_REASON.search(text)
        reasoning = mr.group(1).strip() if mr else ""
    if verdict not in _VERDICTS:
        verdict = "inconclusive"
    return Verdict(verdict=verdict, reasoning=reasoning, model=model)


async def run_adversary_validation(
    claim: str,
    evidence_text: str,
    *,
    backend_name: str,
    model: Optional[str],
    api_base: Optional[str],
    max_turns: int = 3,
    budget_usd: float = 0.10,
) -> Verdict:
    """Run a one-shot READ-ONLY adversary (no tool calls) to try to refute `claim`
    using `evidence_text`. Returns a parsed Verdict (cost/turns filled in)."""
    from .tools import ALL_TOOLS  # lazy: avoid tools->kb->adversary->tools cycle

    backend = create_backend(backend_name, ALL_TOOLS, model=model, api_base=api_base)
    prompt = (
        f"CLAIM (a hypothesis someone wants to mark CONFIRMED):\n{claim}\n\n"
        f"EVIDENCE AVAILABLE:\n{evidence_text}\n\n"
        "Try to refute the claim using only this evidence, then give your verdict."
    )
    report = ""
    cost = 0.0
    turns = 0
    async for ev in backend.run(
        prompt=prompt, system_prompt=_SYSTEM,
        max_turns=max_turns, max_budget_usd=budget_usd, allowed_tools=[],
    ):
        if ev.kind == "text" and ev.content:
            report = ev.content
        elif ev.kind == "result":
            cost = float(ev.cost or 0.0)
            turns = int(ev.turns or 0)
    v = parse_verdict(report, model=model)
    v.cost = cost
    v.turns = turns
    return v
