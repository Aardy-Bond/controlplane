"""Localization baselines — what you would do without this system.

The central performance claim is that binary search over a prefix-evaluable
ledger finds the last good step exactly, in O(log N) deterministic evaluations
and zero model calls. A claim like that is only worth stating next to the
alternatives, measured on the same runs rather than quoted from a paper.

Four baselines, in rough order of how often you meet them in practice:

* ``detected_at`` — blame the step where the alarm went off. This is what a
  plain guardrail gives you, and on a long run it is usually wrong by exactly
  the distance the fault travelled, which is the quantity that matters.
* ``last_tool_call`` — blame the most recent tool call. The natural human first
  guess when reading a trace backwards.
* ``random`` — uniform over the prefix. The floor. If a method cannot beat
  this, it is not a method.
* ``llm_whole_trace`` — hand the entire trace to a model and ask which step
  went wrong. This is the honest competitor: it is what most agent-debugging
  tooling does today, and it costs a model call over a long context.

They all return the same shape as the real engine, so the harness scores them
with identical code. Anything else would be grading on a curve.
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from ..ledger import Ledger
from ..types import Localization, Violation

__all__ = ["BASELINES", "localize_with_baseline"]


def _blank(method: str, step: int, evaluations: int, wall_ms: float, **kw) -> Localization:
    return Localization(
        last_good_step=step,
        method=f"baseline:{method}",
        quality="estimated",
        evaluations=evaluations,
        wall_ms=wall_ms,
        confidence_low=step,
        confidence_high=step,
        candidates=[step],
        **kw,
    )


def _detected_at(ledger: Ledger, violation: Violation, **_) -> Localization:
    started = time.perf_counter()
    step = max(-1, min(violation.detected_at_step, ledger.last_step) - 1)
    return _blank("detected_at", step, 0, (time.perf_counter() - started) * 1000)


def _last_tool_call(ledger: Ledger, violation: Violation, **_) -> Localization:
    started = time.perf_counter()
    hi = min(violation.detected_at_step, ledger.last_step)
    step = -1
    for cp in ledger.prefix(hi):
        if cp.pending_call is not None:
            step = cp.step
    return _blank("last_tool_call", max(-1, step - 1), 0, (time.perf_counter() - started) * 1000)


def _random(ledger: Ledger, violation: Violation, seed: int = 7, **_) -> Localization:
    started = time.perf_counter()
    hi = min(violation.detected_at_step, ledger.last_step)
    rng = random.Random(seed * 1009 + violation.detected_at_step)
    step = rng.randint(-1, max(-1, hi - 1))
    return _blank("random", step, 0, (time.perf_counter() - started) * 1000)


_TRACE_PROMPT = """You are debugging a tool-using AI agent that went wrong.

Below is the full execution trace, one step per line. A check failed at step \
{detected} with this message:

  {detail}

Identify the LAST step at which the agent's state was still correct — the step \
we could safely roll back to. Everything after it is suspect.

Answer with JSON only: {{"last_good_step": <integer>, "why": "<one sentence>"}}

TRACE
{trace}
"""


def _render_trace(ledger: Ledger, hi: int) -> str:
    lines = []
    for cp in ledger.prefix(hi):
        call = cp.pending_call
        tool = call.tool if call else "-"
        args = json.dumps(call.args, default=str)[:220] if call else ""
        src = (cp.result.source_class if cp.result else cp.source_class).value
        preview = json.dumps(cp.result.preview, default=str)[:220] if cp.result else ""
        lines.append(f"step {cp.step}: {tool}({args}) -> [{src}] {preview}")
    return "\n".join(lines)


def _llm_whole_trace(ledger: Ledger, violation: Violation, judge: Any = None, **_) -> Localization:
    """The competitor: one model call over the entire trace.

    With no judge configured this returns a sentinel of -1 rather than quietly
    falling back to a cheaper heuristic, because a baseline that silently
    becomes a different baseline would corrupt the comparison it exists for.
    """
    started = time.perf_counter()
    hi = min(violation.detected_at_step, ledger.last_step)
    if judge is None:
        loc = _blank("llm_whole_trace", -1, 0, (time.perf_counter() - started) * 1000)
        loc.rca = {"error": "no judge configured; baseline not run"}
        return loc

    prompt = _TRACE_PROMPT.format(
        detected=violation.detected_at_step,
        detail=violation.detail,
        trace=_render_trace(ledger, hi),
    )
    resp = judge.complete(
        [{"role": "user", "content": prompt}],
        max_tokens=200,
        cache_salt=f"baseline-loc-{ledger.run_id}-{violation.detected_at_step}",
    )
    step = -1
    match = re.search(r'"last_good_step"\s*:\s*(-?\d+)', resp.text or "")
    if match:
        step = max(-1, min(int(match.group(1)), hi))

    loc = _blank("llm_whole_trace", step, 1, (time.perf_counter() - started) * 1000)
    loc.rca = {"raw": (resp.text or "")[:400], "usd": resp.usd, "tokens": resp.total_tokens}
    return loc


BASELINES = {
    "detected_at": _detected_at,
    "last_tool_call": _last_tool_call,
    "random": _random,
    "llm_whole_trace": _llm_whole_trace,
}


def localize_with_baseline(
    name: str,
    ledger: Ledger,
    violation: Violation,
    judge: Any = None,
    seed: int = 7,
) -> Localization:
    if name not in BASELINES:
        raise KeyError(f"unknown baseline {name!r}; have {sorted(BASELINES)}")
    return BASELINES[name](ledger, violation, judge=judge, seed=seed)
