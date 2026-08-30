"""LOCALIZE — given a violation observed at step N, return the last good step L.

The whole design rests on one observation. If an invariant is *monotone* (once
violated, violated for every longer prefix) and *prefix-evaluable* (a pure
function of ledger[:n]), then the sequence of verdicts over prefixes is
`True, True, ..., True, False, False, ...`. That is a sorted array, and finding
the boundary is a binary search: O(log N) deterministic evaluations, zero model
calls, exact answer.

The alternative — handing 60 steps to an LLM and asking which one went wrong —
is the thing published step-level attribution work scores in the low teens on.
We only fall back to a model for non-monotone invariants, and even then it
adjudicates between three pre-scored candidates rather than reading the trace.

Crucially the same engine serves both the inline and the async path (FR-21).
Nothing here reads live tool state, so a violation discovered eight steps late,
or by a human the next morning, localizes with identical accuracy.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .invariants.base import EvalContext, Invariant
from .ledger import Ledger
from .types import RCA, Checkpoint, Localization, SourceClass, Violation

__all__ = ["LocalizationEngine", "ProvenanceGraph"]


@dataclass
class ProvenanceGraph:
    """Step-level dependency graph built from ``arg_provenance`` edges."""

    parents: dict[int, set[int]]
    children: dict[int, set[int]]
    edge_labels: dict[tuple[int, int], set[str]]

    @classmethod
    def build(cls, checkpoints: list[Checkpoint]) -> ProvenanceGraph:
        parents: dict[int, set[int]] = defaultdict(set)
        children: dict[int, set[int]] = defaultdict(set)
        labels: dict[tuple[int, int], set[str]] = defaultdict(set)
        for cp in checkpoints:
            if cp.pending_call is None:
                continue
            for arg, src in cp.pending_call.arg_provenance.items():
                if src == cp.step:
                    continue
                parents[cp.step].add(src)
                children[src].add(cp.step)
                labels[(src, cp.step)].add(arg)
        return cls(dict(parents), dict(children), dict(labels))

    def ancestors_with_distance(self, step: int) -> dict[int, int]:
        """Breadth-first walk backwards. Distance 1 = direct input."""
        seen: dict[int, int] = {}
        frontier = [(step, 0)]
        while frontier:
            node, dist = frontier.pop(0)
            for parent in self.parents.get(node, set()):
                if parent not in seen or dist + 1 < seen[parent]:
                    seen[parent] = dist + 1
                    frontier.append((parent, dist + 1))
        return seen

    def descendants(self, step: int) -> set[int]:
        out: set[int] = set()
        frontier = [step]
        while frontier:
            node = frontier.pop()
            for child in self.children.get(node, set()):
                if child not in out:
                    out.add(child)
                    frontier.append(child)
        return out


class LocalizationEngine:
    def __init__(self, judge=None) -> None:
        # ``judge`` is an LLMClient. Optional: the monotone path never uses it,
        # and the fallback degrades to its top-scored candidate without one.
        self.judge = judge
        self.judge_calls = 0

    # -- entry point -------------------------------------------------------

    def localize(
        self,
        ledger: Ledger,
        invariant: Invariant,
        ctx: EvalContext,
        violation: Violation,
    ) -> Localization:
        started = time.perf_counter()
        if invariant.monotone:
            result = self._binary_search(ledger, invariant, ctx, violation)
        else:
            result = self._provenance_fallback(ledger, invariant, ctx, violation)
        self._tighten_to_origin(result, violation)
        result.wall_ms = (time.perf_counter() - started) * 1000
        result.rca = self.build_rca(ledger, invariant, violation, result)
        return result

    @staticmethod
    def _tighten_to_origin(result: Localization, violation: Violation) -> None:
        """Pull L back to the step the violation's own evidence blames.

        Binary search answers "where did this invariant stop holding". For a
        guard that fires at a *committing* step — an outbound send, an
        irreversible write — that is later than where the fault entered. The
        invariant already names the offending source in its evidence, so use it:
        rolling back to just before the failed read is the only rollback that
        actually removes the problem. Rolling back to the send would leave the
        bad read in the prefix and the next attempt would fail identically.
        """
        # Only keys that name a *tainted* origin. `resolved_at_step`, for
        # instance, points at where the correct binding came from — tightening
        # to it would roll back past the good resolution and destroy the very
        # fact the agent needs.
        origin_keys = ("source_step", "denied_steps")
        origins: list[int] = []
        for key in origin_keys:
            value = violation.evidence.get(key)
            if isinstance(value, int):
                origins.append(value)
            elif isinstance(value, list):
                origins.extend(v for v in value if isinstance(v, int))
        if not origins:
            return
        tightened = min(origins) - 1
        if tightened < result.last_good_step:
            result.confidence_high = result.last_good_step
            result.last_good_step = max(-1, tightened)
            result.confidence_low = result.last_good_step
            result.method += "+origin_tightened"

    # -- monotone path -----------------------------------------------------

    def _binary_search(
        self,
        ledger: Ledger,
        invariant: Invariant,
        ctx: EvalContext,
        violation: Violation,
    ) -> Localization:
        """Find the smallest n with holds(prefix(n)) == False. L = n - 1."""
        evaluations = 0
        hi = min(violation.detected_at_step, ledger.last_step)

        # Confirm the right endpoint really fails; if not, the invariant was
        # mis-declared monotone or the violation came from elsewhere.
        evaluations += 1
        if invariant.evaluate(ledger.prefix(hi), ctx).holds:
            return Localization(
                last_good_step=hi,
                method="binary_search",
                quality="estimated",
                evaluations=evaluations,
                wall_ms=0.0,
                confidence_low=hi,
                confidence_high=hi,
                candidates=[hi],
            )

        lo = -1  # the empty prefix, held vacuously
        while hi - lo > 1:
            mid = (lo + hi) // 2
            evaluations += 1
            if invariant.evaluate(ledger.prefix(mid), ctx).holds:
                lo = mid
            else:
                hi = mid

        return Localization(
            last_good_step=lo,
            method="binary_search",
            quality="exact",
            evaluations=evaluations,
            wall_ms=0.0,
            confidence_low=lo,
            confidence_high=lo,
            candidates=[lo],
        )

    @staticmethod
    def expected_evaluations(n_steps: int) -> int:
        return int(math.ceil(math.log2(max(2, n_steps + 1)))) + 1

    # -- non-monotone path -------------------------------------------------

    def _provenance_fallback(
        self,
        ledger: Ledger,
        invariant: Invariant,
        ctx: EvalContext,
        violation: Violation,
    ) -> Localization:
        """Score checkpoints by provenance distance, then adjudicate top-3 only."""
        graph = ProvenanceGraph.build(ledger.checkpoints)
        origin = violation.detected_at_step
        distances = graph.ancestors_with_distance(origin)

        scored: list[tuple[float, int]] = []
        for step in range(0, origin + 1):
            cp = ledger[step]
            score = 0.0
            if step in distances:
                score += 4.0 / (1 + distances[step])
            else:
                score += 0.2
            # A step whose own result is suspect is a better candidate.
            src = cp.result.source_class if cp.result else cp.source_class
            if src in {SourceClass.ERROR_TAGGED, SourceClass.DENIED}:
                score += 2.5
            elif src == SourceClass.UNLABELLED:
                score += 0.8
            # A step that introduced the violated subject is a better candidate.
            if violation.subject and cp.pending_call:
                if violation.subject in cp.pending_call.args:
                    score += 1.5
                if violation.subject in (cp.pending_call.arg_provenance or {}):
                    score += 0.5
            scored.append((score, step))

        scored.sort(key=lambda t: (-t[0], t[1]))
        top3 = [s for _, s in scored[:3]]
        chosen = top3[0]
        evaluations = len(scored)

        if self.judge is not None and len(top3) > 1:
            adjudicated = self._adjudicate(ledger, invariant, violation, top3)
            if adjudicated is not None:
                chosen = adjudicated

        last_good = max(-1, chosen - 1)
        window = sorted(top3)
        return Localization(
            last_good_step=last_good,
            method="provenance_fallback",
            quality="estimated",
            evaluations=evaluations,
            wall_ms=0.0,
            confidence_low=max(-1, min(window) - 1),
            confidence_high=max(-1, max(window) - 1),
            candidates=window,
        )

    def _adjudicate(
        self,
        ledger: Ledger,
        invariant: Invariant,
        violation: Violation,
        candidates: list[int],
    ) -> int | None:
        """One LLM call, over three pre-scored candidates only (FR-15).

        The adjudicator never reads the whole trace and is never a first-pass
        reviewer. It breaks a tie that deterministic scoring already narrowed.
        """
        summaries = []
        for step in candidates:
            cp = ledger[step]
            summaries.append(
                {
                    "step": step,
                    "tool": cp.pending_call.tool if cp.pending_call else None,
                    "args": cp.pending_call.args if cp.pending_call else {},
                    "source_class": (
                        cp.result.source_class.value if cp.result else cp.source_class.value
                    ),
                    "result": (cp.result.preview if cp.result else {}),
                }
            )
        prompt = (
            "A runtime invariant was violated in a multi-step agent run.\n\n"
            f"Invariant: {invariant.id} — {invariant.description}\n"
            f"Violation detail: {violation.detail}\n"
            f"Observed at step: {violation.detected_at_step}\n\n"
            "Three candidate steps have already been shortlisted by provenance "
            "analysis. Choose the ONE step at which the run first went wrong.\n\n"
            f"{json.dumps(summaries, indent=2, default=str)}\n\n"
            'Reply with JSON only: {"step": <int>, "reason": "<one sentence>"}'
        )
        try:
            self.judge_calls += 1
            resp = self.judge.complete(
                [
                    {
                        "role": "system",
                        "content": "You adjudicate a shortlisted conflict. Answer with JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=250,
            )
            text = resp.text.strip()
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end == -1:
                return None
            choice = json.loads(text[start : end + 1]).get("step")
            return int(choice) if choice in candidates else None
        except Exception:  # noqa: BLE001 - adjudication is best-effort by design
            return None

    # -- RCA ---------------------------------------------------------------

    def build_rca(
        self,
        ledger: Ledger,
        invariant: Invariant,
        violation: Violation,
        loc: Localization,
    ) -> RCA:
        """Three layers, derived from the ledger rather than narrated (FR-20).

        A single "root cause" is a comfortable fiction. A fault needs a spark,
        something that let it spread, and an absence that kept it quiet — and
        the fix for each layer is different.
        """
        fault_step = max(0, loc.last_good_step + 1)
        fault_cp = (
            ledger[fault_step] if fault_step <= ledger.last_step else ledger[ledger.last_step]
        )
        graph = ProvenanceGraph.build(ledger.checkpoints)
        spread = sorted(s for s in graph.descendants(fault_step) if s <= violation.detected_at_step)
        gap = violation.detected_at_step - fault_step

        tool = fault_cp.pending_call.tool if fault_cp.pending_call else "n/a"
        src = (fault_cp.result.source_class if fault_cp.result else fault_cp.source_class).value

        trigger = (
            f"Step {fault_step} ({tool}) introduced the fault: {violation.detail}. "
            f"Result at that step was classed {src}."
        )

        if spread:
            amplifier = (
                f"The value propagated through {len(spread)} downstream step(s) "
                f"({spread[0]}\u2013{spread[-1]}) with no re-verification at any hop; each was "
                "internally consistent, so nothing downstream had reason to question it."
            )
        else:
            amplifier = (
                "No downstream propagation had occurred yet — the violation was caught at "
                "the step that produced it, so blast radius is limited to this step."
            )

        if gap == 0:
            concealer = (
                f"Nothing concealed it: {invariant.id} fired inline at the originating step."
            )
        else:
            path = "asynchronously" if violation.detected_by == "async" else "inline"
            concealer = (
                f"{invariant.id} runs {path} under this tier, so the fault was invisible for "
                f"{gap} step(s). No intermediate step re-validated the assumption, and the "
                "downstream outputs were self-consistent, which reads as correctness."
            )

        return RCA(trigger=trigger, amplifier=amplifier, concealer=concealer)


def summarize_localization(loc: Localization) -> dict[str, Any]:
    return {
        "last_good_step": loc.last_good_step,
        "method": loc.method,
        "quality": loc.quality,
        "evaluations": loc.evaluations,
        "wall_ms": round(loc.wall_ms, 3),
        "confidence": [loc.confidence_low, loc.confidence_high],
    }
