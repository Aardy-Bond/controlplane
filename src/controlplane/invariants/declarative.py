"""YAML-declared invariants (FR-13).

Adding an invariant must not require a supervisor code change, otherwise the
policy layer ages exactly as badly as the hard-coded rules it was meant to
replace. A declarative invariant is a ``when``/``require`` pair evaluated by the
restricted expression evaluator over one checkpoint at a time.

The author declares ``monotone`` themselves, and the sabotage suite checks the
claim — a mis-declared monotone flag would silently corrupt localization, so it
is validated rather than trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..safe_expr import ExprError, evaluate
from ..types import Checkpoint, CostClass, InvariantClass, Severity, Verdict
from .base import EvalContext, Invariant

__all__ = ["DeclarativeInvariant", "load_declarative_invariants"]


class DeclarativeInvariant(Invariant):
    def __init__(self, body: dict[str, Any]) -> None:
        self.id = body["id"]
        self.invariant_class = InvariantClass(body.get("class", "precondition"))
        self.monotone = bool(body.get("monotone", True))
        self.inline_cost_class = CostClass(body.get("cost", "us"))
        self.severity = Severity(body.get("severity", "block"))
        applies = body.get("applies_to") or ["*"]
        self.applies_to = frozenset(applies)
        self.description = body.get("description", "")
        self.scope = body.get("scope", "call")
        self.when = body.get("when", "True")
        self.require = body["require"]
        self.message = body.get("message", "")
        self.source = "declarative"

    def _env(self, cp: Checkpoint, ctx: EvalContext) -> dict[str, Any] | None:
        if self.scope == "call":
            if cp.pending_call is None:
                return None
            return {
                **cp.pending_call.args,
                "tool": cp.pending_call.tool,
                "args": cp.pending_call.args,
                "reversibility": cp.pending_call.reversibility.value,
                "step": cp.step,
                "bindings": {k: v.value for k, v in cp.commitments.bindings.items()},
                "constraints": cp.commitments.constraints,
                "state": ctx.world_view,
                "entitlements": sorted(ctx.entitlements),
                "caller": ctx.caller,
            }
        if self.scope == "result":
            if cp.result is None:
                return None
            return {
                "result": cp.result.preview,
                "source_class": cp.result.source_class.value,
                "tool": cp.pending_call.tool if cp.pending_call else "",
                "step": cp.step,
                "state": ctx.world_view,
            }
        return {
            "step": cp.step,
            "budget": cp.budget.model_dump(),
            "constraints": cp.commitments.constraints,
            "state": ctx.world_view,
        }

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp in prefix:
            env = self._env(cp, ctx)
            if env is None:
                continue
            try:
                if not bool(evaluate(self.when, env)):
                    continue
                ok = bool(evaluate(self.require, env))
            except ExprError:
                # Unevaluable means unverified, and unverified is not a pass for
                # a guard the operator explicitly asked for.
                ok = False
            if not ok:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: {self.message or self.require} (require: {self.require})",
                    subject=self.id.split(".")[-1],
                    step=cp.step,
                )
        return Verdict.ok(self.id)


def load_declarative_invariants(path: Path, registry) -> list[DeclarativeInvariant]:
    """Load and register invariants from YAML. Hot-reloadable (NFR-8)."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    loaded: list[DeclarativeInvariant] = []
    for body in raw.get("invariants") or []:
        inv = DeclarativeInvariant(body)
        registry.add(inv)
        loaded.append(inv)
    return loaded
