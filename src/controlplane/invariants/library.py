"""Invariant library v0 (PRD 5.3).

Every check here is a pure function of a ledger prefix and declares its own
monotonicity. Read the ``monotone`` flags carefully: they are the contract that
LOCALIZE's binary search relies on, and each one is verified by a property test
in ``tests/test_monotonicity.py`` rather than taken on trust.

Naming: ``<class>.<check>``. Ids are stable and appear in incident records,
CLAIMS.md and the sabotage suite, so they should not be renamed casually.
"""

from __future__ import annotations

import re
from typing import Any

from ..pii import detect as detect_pii
from ..safe_expr import ExprError, evaluate
from ..types import (
    Checkpoint,
    CostClass,
    InvariantClass,
    Reversibility,
    Severity,
    SourceClass,
    Verdict,
    canonical_hash,
)
from .base import EvalContext, Invariant, register

TAINTED = {SourceClass.ERROR_TAGGED, SourceClass.UNLABELLED, SourceClass.DENIED}

# Argument names that carry entity identity and therefore require a binding.
ENTITY_ARGS = {
    "policy_id",
    "claim_id",
    "customer_id",
    "policyholder_id",
    "account_id",
    "employee_id",
    "quote_id",
}


def _calls(prefix: list[Checkpoint]):
    """Yield (checkpoint, pending_call) for every step that issued a call."""
    for cp in prefix:
        if cp.pending_call is not None:
            yield cp, cp.pending_call


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


@register
class BindingProvenanceTraced(Invariant):
    id = "binding.provenance_traced"
    invariant_class = InvariantClass.BINDING
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"A", "C"})
    description = (
        "Every entity identifier passed to a tool traces by provenance to a "
        "resolution event, and its value still matches the value that was resolved."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            for arg, value in call.args.items():
                if arg not in ENTITY_ARGS:
                    continue
                binding = cp.commitments.bindings.get(arg)
                if binding is None:
                    # The tool that resolves this entity is allowed to be the
                    # first to mention it; everything else must cite a binding.
                    if arg in ctx.manifest.get(call.tool).resolves:
                        continue
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} used {arg}={value!r} with no prior "
                        f"resolution event to bind it",
                        subject=arg,
                        step=cp.step,
                        tool=call.tool,
                    )
                # Compare hashes, not text: the stored value has been redacted
                # and the binding's has not.
                used_hash = call.arg_hashes.get(arg) or canonical_hash(str(value))
                if used_hash != binding.value_hash:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} used a {arg} that does not match the "
                        f"binding resolved at step {binding.resolved_at_step} "
                        f"({binding.value!r})",
                        subject=arg,
                        step=cp.step,
                        tool=call.tool,
                        bound_value=binding.value,
                        used_value=str(value),
                        resolved_at_step=binding.resolved_at_step,
                    )
        return Verdict.ok(self.id)


@register
class BindingSingleActive(Invariant):
    id = "binding.single_active"
    invariant_class = InvariantClass.BINDING
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"A", "C"})
    description = (
        "An entity's binding may change only at an explicit re-resolution event. "
        "A binding that silently mutates between steps is entity drift."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        seen: dict[str, str] = {}
        for cp in prefix:
            resolves = ctx.manifest.get(cp.pending_call.tool).resolves if cp.pending_call else []
            for entity, binding in cp.commitments.bindings.items():
                prior = seen.get(entity)
                if prior is not None and prior != binding.value:
                    if entity not in resolves and binding.confidence_source != "re_resolution":
                        return Verdict.fail(
                            self.id,
                            f"step {cp.step}: binding for {entity} changed from {prior!r} to "
                            f"{binding.value!r} without a re-resolution event",
                            subject=entity,
                            step=cp.step,
                            previous=prior,
                            current=binding.value,
                        )
                seen[entity] = binding.value
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@register
class SchemaArgsValid(Invariant):
    id = "schema.args_valid"
    invariant_class = InvariantClass.SCHEMA
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = "Tool arguments satisfy the declared schema: required keys present, types match."

    _PY_TYPES = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            if not spec.schema:
                continue
            for req in spec.required_args:
                if req not in call.args:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} missing required argument {req!r}",
                        subject=req,
                        step=cp.step,
                        tool=call.tool,
                    )
            for arg, value in call.args.items():
                declared = spec.properties.get(arg, {}).get("type")
                expected = self._PY_TYPES.get(declared) if declared else None
                if expected and not isinstance(value, expected):
                    # A redacted placeholder is not a type error.
                    if isinstance(value, str) and re.fullmatch(r"\[[A-Z_]+\]", value):
                        continue
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} argument {arg!r} declared {declared} "
                        f"but got {type(value).__name__}",
                        subject=arg,
                        step=cp.step,
                        tool=call.tool,
                    )
        return Verdict.ok(self.id)


@register
class SchemaNoPositionalShift(Invariant):
    id = "schema.no_positional_shift"
    invariant_class = InvariantClass.SCHEMA
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "Values are bound to fields by key, never by position. Detected by its "
        "signature: a value that matches another declared field's format better "
        "than its own, which is what an omitted output line produces."
    )

    _FORMATS = {
        "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "quarter": re.compile(r"^FY\d{2}Q[1-4]$"),
        "policy_id": re.compile(r"^POL-\d{6}$"),
        "claim_id": re.compile(r"^CLM-\d{6}$"),
        "currency": re.compile(r"^(INR|USD|SGD|EUR)$"),
    }

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            for arg, value in call.args.items():
                if not isinstance(value, str):
                    continue
                declared_fmt = spec.properties.get(arg, {}).get("format")
                if not declared_fmt or declared_fmt not in self._FORMATS:
                    continue
                if self._FORMATS[declared_fmt].match(value):
                    continue
                misplaced = [name for name, pat in self._FORMATS.items() if pat.match(value)]
                if misplaced:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} argument {arg!r} expects "
                        f"{declared_fmt} but holds a {misplaced[0]} value {value!r} — "
                        "field assignments appear shifted by position",
                        subject=arg,
                        step=cp.step,
                        tool=call.tool,
                        expected_format=declared_fmt,
                        actual_format=misplaced[0],
                    )
        return Verdict.ok(self.id)


@register
class SchemaNoSilentTruncation(Invariant):
    id = "schema.no_silent_truncation"
    implicates = "result"
    invariant_class = InvariantClass.SCHEMA
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "A result that declares a total count must return that many records. A "
        "page of 20 out of 340 read as the whole set is a silent truncation."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp in prefix:
            res = cp.result
            if res is None or not isinstance(res.preview, dict):
                continue
            declared = res.preview.get("total_count")
            rows = res.preview.get("rows")
            if declared is None or not isinstance(rows, list):
                continue
            if len(rows) < declared and not res.preview.get("truncation_acknowledged"):
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: result declares total_count={declared} but returned "
                    f"{len(rows)} rows with no truncation marker",
                    subject="rows",
                    step=cp.step,
                    declared=declared,
                    returned=len(rows),
                )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Precondition
# ---------------------------------------------------------------------------


@register
class PreconditionDeclared(Invariant):
    id = "precondition.declared"
    invariant_class = InvariantClass.PRECONDITION
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"A", "C"})
    description = "Every precondition declared in the tool manifest holds at call time."

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            if not spec.preconditions:
                continue
            env: dict[str, Any] = {
                **call.args,
                "state": ctx.world_view,
                "observed": {k: v.preview for k, v in cp.observed_state.items()},
                "step": cp.step,
            }
            for pre in spec.preconditions:
                reason = ""
                try:
                    ok = bool(evaluate(pre.expr, env))
                except ExprError as exc:
                    # An unevaluable precondition is not a pass. Missing or
                    # malformed inputs mean unverified, and unverified on a
                    # guarded tool means deny. Report it as unevaluable rather
                    # than as a violation — they call for different fixes.
                    ok, reason = False, str(exc)
                if not ok:
                    detail = f"could not be evaluated ({reason})" if reason else f"— {pre.message}"
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} precondition {pre.name!r} "
                        f"({pre.expr}) {detail}",
                        subject=pre.name,
                        step=cp.step,
                        tool=call.tool,
                        expr=pre.expr,
                        unevaluable=bool(reason),
                    )
        return Verdict.ok(self.id)


@register
class PreconditionUnitConsistency(Invariant):
    id = "precondition.unit_consistency"
    invariant_class = InvariantClass.PRECONDITION
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"C", "A"})
    description = (
        "A numeric value carries the unit of its source. Combining figures that "
        "declare different currencies or scales is a violation, not a rounding issue."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        units_by_step: dict[int, str] = {}
        for cp in prefix:
            if cp.result and isinstance(cp.result.preview, dict):
                unit = cp.result.preview.get("unit") or cp.result.preview.get("currency")
                if unit:
                    units_by_step[cp.step] = str(unit)

        for cp, call in _calls(prefix):
            contributing = {
                units_by_step[src]
                for arg, src in call.arg_provenance.items()
                if src in units_by_step and isinstance(call.args.get(arg), int | float)
            }
            if len(contributing) > 1:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: {call.tool} combines figures in incompatible units "
                    f"{sorted(contributing)}",
                    subject="unit",
                    step=cp.step,
                    tool=call.tool,
                    units=sorted(contributing),
                )
            declared = call.args.get("unit") or call.args.get("currency")
            if declared and contributing and str(declared) not in contributing:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: {call.tool} declares unit {declared!r} but its inputs "
                    f"are in {sorted(contributing)}",
                    subject="unit",
                    step=cp.step,
                    tool=call.tool,
                )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@register
class ProvenanceNoTaintedSource(Invariant):
    id = "provenance.no_tainted_source"
    implicates = "result"
    invariant_class = InvariantClass.PROVENANCE
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "No outbound field derives from a source classed error_tagged, unlabelled "
        "or denied. This is what stops an HTTP 502 body being read as data."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        source_class_by_step = {
            cp.step: (cp.result.source_class if cp.result else cp.source_class) for cp in prefix
        }
        for cp, call in _calls(prefix):
            for arg, src_step in call.arg_provenance.items():
                sc = source_class_by_step.get(src_step, SourceClass.UNLABELLED)
                if sc in TAINTED:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: {call.tool} argument {arg!r} derives from step "
                        f"{src_step} whose result is classed {sc.value}",
                        subject=arg,
                        step=cp.step,
                        tool=call.tool,
                        source_step=src_step,
                        source_class=sc.value,
                    )
        return Verdict.ok(self.id)


@register
class ProvenanceDeniedIsNotAbsence(Invariant):
    id = "provenance.denied_is_not_absence"
    implicates = "result"
    invariant_class = InvariantClass.PROVENANCE
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "A denied result may not be read as 'nothing found'. An instrument that "
        "cannot tell absence from prohibition manufactures false reassurance."
    )

    _ABSENCE = re.compile(
        r"\b(no (?:results?|records?|restrictions?|matches?|entries|data)"
        r"|none found|nothing found|not found|empty|no such)\b",
        re.IGNORECASE,
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        denied_steps = {
            cp.step
            for cp in prefix
            if (cp.result.source_class if cp.result else cp.source_class) == SourceClass.DENIED
        }
        if not denied_steps:
            return Verdict.ok(self.id)
        for cp in prefix:
            if cp.step in denied_steps:
                continue
            cited = (
                set((cp.pending_call.arg_provenance or {}).values()) if cp.pending_call else set()
            )
            cites_denied = denied_steps & cited
            if cp.narrative and self._ABSENCE.search(cp.narrative):
                # Narrative claims absence while a denial is unresolved upstream.
                unresolved = sorted(s for s in denied_steps if s < cp.step)
                if unresolved:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: concluded absence while step "
                        f"{unresolved[-1]} returned access-denied, not empty",
                        subject="absence_claim",
                        step=cp.step,
                        denied_steps=unresolved,
                    )
            if cites_denied:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: argument derived from denied result at step "
                    f"{sorted(cites_denied)[0]}",
                    subject="denied_derivation",
                    step=cp.step,
                    denied_steps=sorted(cites_denied),
                )
        return Verdict.ok(self.id)


@register
class ProvenanceUnresolvedTaintedSource(Invariant):
    id = "provenance.unresolved_tainted_source"
    implicates = "result"
    invariant_class = InvariantClass.PROVENANCE
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "No irreversible or outbound action while a read in this run failed or was "
        "denied and was never successfully retried. Value-level provenance only "
        "catches a bad value that was actually passed along; this catches the more "
        "common case where the agent could not read something, carried on anyway, "
        "and told someone the result."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        # "Unresolved" is judged strictly as of the committing step, from what
        # had happened by then. Succeeding at the read afterwards does not
        # un-send the message. Judging with hindsight would also make this check
        # non-monotone — a later retry would make a failed prefix pass again —
        # and a non-monotone check silently returns wrong answers from binary
        # search. The property test in tests/test_monotonicity.py catches
        # exactly this, which is why it is written the way it is.
        resolved: set[tuple[str, str]] = set()
        outstanding: list[tuple[int, str, str, str]] = []  # step, tool, class, args_hash

        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            committing = spec.egress or spec.reversibility is not Reversibility.REVERSIBLE
            if committing and outstanding:
                step_bad, tool_bad, cls_bad, _ = outstanding[0]
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: {call.tool} commits an outbound or irreversible "
                    f"action while step {step_bad} ({tool_bad}) returned {cls_bad} and had "
                    f"not been successfully retried",
                    subject="unresolved_source",
                    step=cp.step,
                    tool=call.tool,
                    source_step=step_bad,
                    source_class=cls_bad,
                )

            src = cp.result.source_class if cp.result else cp.source_class
            key = (call.tool, call.args_hash)
            if src is SourceClass.OK:
                resolved.add(key)
                outstanding = [o for o in outstanding if (o[1], o[3]) != key]
            elif src in {SourceClass.ERROR_TAGGED, SourceClass.DENIED}:
                outstanding.append((cp.step, call.tool, src.value, call.args_hash))

        return Verdict.ok(self.id)


@register
class ProvenanceDataVintage(Invariant):
    id = "provenance.data_vintage"
    implicates = "result"
    invariant_class = InvariantClass.PROVENANCE
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"C", "A"})
    description = (
        "Every observed source satisfies the run's declared data-vintage "
        "constraint. Catches a stale snapshot at the step that read it, not "
        "forty steps later when the number it fed looks wrong."
    )

    _CONSTRAINT = re.compile(r"data_as_of\s*==?\s*([A-Za-z0-9]+)")

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        required: str | None = None
        for cp in prefix:
            for c in cp.commitments.constraints:
                m = self._CONSTRAINT.search(c)
                if m:
                    required = m.group(1)
        if not required:
            return Verdict.ok(self.id)

        for cp in prefix:
            res = cp.result
            if res is None or not isinstance(res.preview, dict):
                continue
            as_of = res.preview.get("as_of") or res.preview.get("as_of_quarter")
            if as_of and str(as_of) != required:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: source returned data as of {as_of} but the run "
                    f"is constrained to {required}",
                    subject="as_of",
                    step=cp.step,
                    required=required,
                    observed=str(as_of),
                )
        return Verdict.ok(self.id)


@register
class EntitlementRetrievalScope(Invariant):
    id = "entitlement.retrieval_scope"
    invariant_class = InvariantClass.ENTITLEMENT
    monotone = True
    inline_cost_class = CostClass.MILLIS
    severity = Severity.BLOCK
    applies_to = frozenset({"B"})
    description = "Every retrieved document lies inside the caller's entitlement set."

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp in prefix:
            res = cp.result
            if res is None or not isinstance(res.preview, dict):
                continue
            for doc in res.preview.get("documents") or []:
                scope = doc.get("scope") if isinstance(doc, dict) else None
                if scope and scope not in ctx.entitlements:
                    return Verdict.fail(
                        self.id,
                        f"step {cp.step}: retrieved document {doc.get('doc_id')!r} in scope "
                        f"{scope!r}, outside caller {ctx.caller!r} entitlements "
                        f"{sorted(ctx.entitlements)}",
                        subject="entitlement",
                        step=cp.step,
                        doc_id=doc.get("doc_id"),
                        scope=scope,
                    )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@register
class ProgressNoRepeat(Invariant):
    id = "progress.no_repeat"
    invariant_class = InvariantClass.PROGRESS
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.WARN
    description = (
        "No identical (tool, canonical args) call repeats within a sliding window. "
        "This is the retry storm detector."
    )
    window = 6
    threshold = 3

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        window = int(ctx.config.get("repeat_window", self.window))
        threshold = int(ctx.config.get("repeat_threshold", self.threshold))
        history: list[tuple[int, str]] = [
            (cp.step, f"{call.tool}:{call.args_hash}") for cp, call in _calls(prefix)
        ]
        for i, (step, key) in enumerate(history):
            recent = [k for _, k in history[max(0, i - window + 1) : i + 1]]
            if recent.count(key) >= threshold:
                return Verdict.fail(
                    self.id,
                    f"step {step}: identical call {key.split(':')[0]} repeated "
                    f"{recent.count(key)} times within {window} steps",
                    subject="repeat",
                    step=step,
                    repeats=recent.count(key),
                )
        return Verdict.ok(self.id)


@register
class ProgressGoalDistance(Invariant):
    id = "progress.goal_distance"
    invariant_class = InvariantClass.PROGRESS
    monotone = False
    inline_cost_class = CostClass.MILLIS
    severity = Severity.WARN
    applies_to = frozenset({"C"})
    description = (
        "Distance to the goal does not increase over a K-step window. Explicitly "
        "non-monotone: a plan may legitimately regress before it improves, so a "
        "violation here is localized by the fallback path and marked estimated."
    )
    k = 8

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        k = int(ctx.config.get("goal_window", self.k))
        scores = [
            (cp.step, float(cp.result.preview.get("goal_distance")))
            for cp in prefix
            if cp.result
            and isinstance(cp.result.preview, dict)
            and isinstance(cp.result.preview.get("goal_distance"), int | float)
        ]
        if len(scores) <= k:
            return Verdict.ok(self.id)
        for i in range(k, len(scores)):
            step, now = scores[i]
            _, before = scores[i - k]
            if now > before:
                return Verdict.fail(
                    self.id,
                    f"step {step}: goal distance rose from {before:.2f} to {now:.2f} "
                    f"over {k} steps",
                    subject="goal_distance",
                    step=step,
                    before=before,
                    after=now,
                )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@register
class BudgetCaps(Invariant):
    id = "budget.caps"
    invariant_class = InvariantClass.BUDGET
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = "Step, token, wall-clock and per-tool call caps are respected."

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        caps = ctx.config.get("budget", {})
        if not caps or not prefix:
            return Verdict.ok(self.id)
        last = prefix[-1]
        for field_name, cap in (
            ("steps_used", caps.get("max_steps")),
            ("tokens", caps.get("max_tokens")),
            ("wall_ms", caps.get("max_wall_ms")),
            ("usd", caps.get("max_usd")),
        ):
            if cap is not None and getattr(last.budget, field_name) > cap:
                return Verdict.fail(
                    self.id,
                    f"step {last.step}: {field_name}={getattr(last.budget, field_name)} "
                    f"exceeds cap {cap}",
                    subject=field_name,
                    step=last.step,
                )
        per_tool = caps.get("max_per_tool") or {}
        for tool, cap in per_tool.items():
            used = last.budget.tool_calls.get(tool, 0)
            if used > cap:
                return Verdict.fail(
                    self.id,
                    f"step {last.step}: tool {tool} called {used} times, cap {cap}",
                    subject=tool,
                    step=last.step,
                )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class _PIIBase(Invariant):
    max_tier = 1

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            if not ctx.manifest.get(call.tool).egress:
                continue
            payload = " ".join(str(v) for v in call.args.values() if isinstance(v, str))
            spans = [s for s in detect_pii(payload, self.max_tier) if s.tier <= self.max_tier]
            if spans:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: outbound payload to {call.tool} contains "
                    f"{', '.join(sorted({s.kind for s in spans}))}",
                    subject="pii",
                    step=cp.step,
                    tool=call.tool,
                    spans=[s.as_dict() for s in spans],
                )
        return Verdict.ok(self.id)


@register
class SafetyPIITier1(_PIIBase):
    id = "safety.pii_tier1"
    invariant_class = InvariantClass.SAFETY
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = "No tier-1 PII (identifiers with a deterministic signature) leaves the boundary."
    max_tier = 1


@register
class SafetyPIITier2(_PIIBase):
    id = "safety.pii_tier2"
    invariant_class = InvariantClass.SAFETY
    monotone = True
    inline_cost_class = CostClass.MILLIS
    severity = Severity.BLOCK
    description = (
        "No tier-2 PII (contextual entities: person names, addresses) leaves the "
        "boundary. Costlier, so tight-latency tiers may demote it to async."
    )
    max_tier = 2


@register
class SafetyEgressAllowlist(Invariant):
    id = "safety.egress_allowlist"
    invariant_class = InvariantClass.SAFETY
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    applies_to = frozenset({"A", "B"})
    description = (
        "An egress destination must be on the allowlist for the bound entity — "
        "the contact on the policy, not a contact the model recalled."
    )

    _DEST_ARGS = ("to", "recipient", "email", "phone", "destination", "address")

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            if not spec.egress:
                continue
            dest_arg = next((a for a in self._DEST_ARGS if a in call.args), None)
            if dest_arg is None:
                continue
            # The allowlist is declared as hashes and the destination is
            # compared as a hash. A phone number is personal data; it is
            # redacted out of the stored args, so comparing text here would
            # reject every legitimate send.
            allowed = {
                c.split("egress_allow:", 1)[1].strip()
                for c in cp.commitments.constraints
                if c.startswith("egress_allow:")
            }
            if not allowed:
                continue
            dest_hash = call.arg_hashes.get(dest_arg) or canonical_hash(str(call.args[dest_arg]))
            if dest_hash not in allowed:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: {call.tool} targets a destination that is not on the "
                    f"allowlist for the bound entity",
                    subject="egress",
                    step=cp.step,
                    tool=call.tool,
                    destination=str(call.args[dest_arg]),
                    allowed_count=len(allowed),
                )
        return Verdict.ok(self.id)


@register
class SafetyNoProtectedAttributeInAction(Invariant):
    id = "safety.no_protected_attribute_in_action"
    invariant_class = InvariantClass.SAFETY
    monotone = True
    inline_cost_class = CostClass.MICROS
    severity = Severity.BLOCK
    description = (
        "A protected attribute may not appear in the arguments of an action. This "
        "is counterfactual *action* invariance, checkable within a single run; the "
        "paired-run version lives in the T-602 harness."
    )

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            spec = ctx.manifest.get(call.tool)
            if spec.reversibility.value == "reversible" and not spec.egress:
                continue
            hits = sorted(set(call.args) & ctx.protected_attributes)
            if hits:
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: action {call.tool} is parameterised by protected "
                    f"attribute(s) {hits}",
                    subject=hits[0],
                    step=cp.step,
                    tool=call.tool,
                    attributes=hits,
                )
        return Verdict.ok(self.id)


# ---------------------------------------------------------------------------
# Semantic (P1) — the only class that may consult a model, and only on a flag
# ---------------------------------------------------------------------------


@register
class SemanticRecommendationConsistent(Invariant):
    id = "semantic.recommendation_consistent"
    invariant_class = InvariantClass.SEMANTIC
    monotone = False
    inline_cost_class = CostClass.LLM
    severity = Severity.WARN
    applies_to = frozenset({"C"})
    description = (
        "A recommendation is arithmetically consistent with the figures it cites. "
        "Checked deterministically first; the adjudicator is only consulted on a "
        "conflict this check has already flagged (FR-15)."
    )

    _NUM = re.compile(r"-?\d+(?:\.\d+)?")

    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        for cp, call in _calls(prefix):
            cited = call.args.get("cited_figures")
            stated = call.args.get("recommended_rate")
            if not isinstance(cited, dict) or not isinstance(stated, int | float):
                continue
            base = cited.get("base_rate")
            loading = cited.get("loading_pct")
            if not isinstance(base, int | float) or not isinstance(loading, int | float):
                continue
            expected = base * (1 + loading / 100.0)
            if abs(expected - stated) > max(0.01, abs(expected) * 0.02):
                return Verdict.fail(
                    self.id,
                    f"step {cp.step}: recommended rate {stated} is inconsistent with cited "
                    f"base {base} and loading {loading}% (implies {expected:.4f})",
                    subject="recommended_rate",
                    step=cp.step,
                    tool=call.tool,
                    expected=round(expected, 4),
                    stated=stated,
                )
        return Verdict.ok(self.id)
