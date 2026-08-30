"""Localization baselines — what you would do without this system.

The central performance claim is that binary search over a prefix-evaluable
ledger finds the last good step exactly, in O(log N) deterministic evaluations
and zero model calls. A claim like that is only worth stating next to the
alternatives, measured on the same runs rather than quoted from a paper.

Baselines, in rough order of how seriously they compete:

* ``previous_step`` — blame the step just before the alarm
  (``detected_at - 1``). The obvious human first guess. Exact whenever the
  fault is caught on the same step it was planted. This is the fair floor a
  real competitor has to beat.
* ``last_write`` — blame the most recent state-changing tool call
  (compensable or irreversible) before the alarm. A plausible engineer's
  heuristic when reading a trace backwards.
* ``last_tool_call`` — blame the most recent tool call of any kind.
* ``detected_at`` — report the alarm step itself as the last good step.
  Mis-specified (off by a constant offset); kept as a labelled sanity floor,
  not a real competitor.
* ``random`` — uniform over the prefix. The floor.
* ``llm_whole_trace`` — hand the entire trace to a model and ask which step
  went wrong. The honest expensive competitor.

They all return the same shape as the real engine, so the harness scores them
with identical code. Anything else would be grading on a curve.

Important: baselines must not clamp to the *post-recovery* logical ledger
length. After a rollback the logical view ends before the alarm step, and
clamping there would make every baseline look artificially bad on recovered
runs. Use the violation's ``detected_at_step`` as the authority for "when the
alarm fired", and walk the physical log for tool history up to that step.
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from ..ledger import Ledger
from ..types import Localization, Reversibility, Violation

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


def _alarm_step(ledger: Ledger, violation: Violation) -> int:
    """The step the alarm fired at — never clamped to a rolled-back view."""
    return max(0, violation.detected_at_step)


def _calls_up_to(ledger: Ledger, hi: int) -> list[tuple[int, Any]]:
    """Tool calls at or before ``hi``, read from the physical log.

    Prefers the earliest epoch at each step so a rolled-back attempt that
    actually carried the fault is still visible. Falls back to the logical
    prefix when the physical log is empty (synthetic test ledgers).
    """
    seen: dict[int, Any] = {}
    physical = ledger.physical_log
    if physical:
        for cp in physical:
            if cp.rollback_to is not None:
                continue
            if cp.step > hi:
                continue
            if cp.pending_call is None:
                continue
            # Keep the first write for each step (the attempt that ran).
            seen.setdefault(cp.step, cp.pending_call)
    else:
        for cp in ledger.prefix(min(hi, ledger.last_step)):
            if cp.pending_call is not None:
                seen[cp.step] = cp.pending_call
    return sorted(seen.items())


def _previous_step(ledger: Ledger, violation: Violation, **_) -> Localization:
    """The fair cheap guess: last good = alarm step − 1."""
    started = time.perf_counter()
    step = max(-1, _alarm_step(ledger, violation) - 1)
    return _blank("previous_step", step, 0, (time.perf_counter() - started) * 1000)


def _detected_at(ledger: Ledger, violation: Violation, **_) -> Localization:
    """Sanity floor: report the alarm step itself as last-good.

    Off by a constant +1 on every lag-0 incident. Kept so the comparison can
    show what a mis-specified baseline looks like; do not feature it as a
    real competitor.
    """
    started = time.perf_counter()
    step = _alarm_step(ledger, violation)
    return _blank("detected_at", step, 0, (time.perf_counter() - started) * 1000)


def _last_tool_call(ledger: Ledger, violation: Violation, **_) -> Localization:
    started = time.perf_counter()
    hi = _alarm_step(ledger, violation)
    step = -1
    for s, _call in _calls_up_to(ledger, hi):
        step = s
    return _blank("last_tool_call", max(-1, step - 1), 0, (time.perf_counter() - started) * 1000)


def _last_write(ledger: Ledger, violation: Violation, **_) -> Localization:
    """Blame the most recent state-changing (compensable/irreversible) call."""
    started = time.perf_counter()
    hi = _alarm_step(ledger, violation)
    step = -1
    for s, call in _calls_up_to(ledger, hi):
        rev = call.reversibility
        if rev in (Reversibility.COMPENSABLE, Reversibility.IRREVERSIBLE):
            step = s
    # If the run never wrote, fall back to the previous-step guess rather than
    # silently returning -1 and looking worse than a human would be.
    if step < 0:
        step = hi
    return _blank("last_write", max(-1, step - 1), 0, (time.perf_counter() - started) * 1000)


def _random(ledger: Ledger, violation: Violation, seed: int = 7, **_) -> Localization:
    started = time.perf_counter()
    hi = _alarm_step(ledger, violation)
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
    for step, call in _calls_up_to(ledger, hi):
        args = json.dumps(call.args, default=str)[:220]
        lines.append(f"step {step}: {call.tool}({args})")
    # Prefer physical-log narrative when available for richer context.
    by_step = {}
    for cp in ledger.physical_log:
        if cp.rollback_to is not None or cp.step > hi:
            continue
        by_step.setdefault(cp.step, cp)
    if by_step:
        lines = []
        for step in sorted(by_step):
            cp = by_step[step]
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
    hi = _alarm_step(ledger, violation)
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


# Order matters for display: strongest fair cheap baseline first, sanity
# floors last. ``llm_whole_trace`` stays near the end as the expensive peer.
BASELINES = {
    "previous_step": _previous_step,
    "last_write": _last_write,
    "last_tool_call": _last_tool_call,
    "detected_at": _detected_at,
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
