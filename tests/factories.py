"""Synthetic ledger construction for tests — no network, no model calls."""

from __future__ import annotations

from typing import Any

from controlplane.invariants import EvalContext
from controlplane.ledger import Ledger
from controlplane.manifest import Precondition, ToolManifest, ToolSpec
from controlplane.types import (
    Binding,
    Budget,
    Commitments,
    ObservedState,
    PendingCall,
    Reversibility,
    SourceClass,
    canonical_hash,
)


def demo_manifest() -> ToolManifest:
    m = ToolManifest()
    m.add(
        ToolSpec(
            name="lookup_policy",
            schema={
                "type": "object",
                "properties": {"policy_id": {"type": "string", "format": "policy_id"}},
                "required": ["policy_id"],
            },
            reversibility=Reversibility.REVERSIBLE,
            resolves=["policy_id"],
        )
    )
    m.add(
        ToolSpec(
            name="read_data",
            schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "amount": {"type": "number"},
                    # A second numeric field, so a call can combine two figures
                    # that came from sources declaring different units.
                    "weight": {"type": "number"},
                },
            },
            reversibility=Reversibility.REVERSIBLE,
        )
    )
    m.add(
        ToolSpec(
            name="write_note",
            schema={"type": "object", "properties": {"body": {"type": "string"}}},
            reversibility=Reversibility.COMPENSABLE,
            compensator="delete_note",
        )
    )
    m.add(
        ToolSpec(
            name="issue_refund",
            schema={
                "type": "object",
                "properties": {
                    "policy_id": {"type": "string", "format": "policy_id"},
                    "amount": {"type": "number"},
                },
                "required": ["policy_id", "amount"],
            },
            reversibility=Reversibility.IRREVERSIBLE,
            preconditions=[
                Precondition("positive", "amount > 0", "amount must be positive"),
            ],
        )
    )
    m.add(
        ToolSpec(
            name="send_sms",
            schema={
                "type": "object",
                "properties": {"to": {"type": "string"}, "message": {"type": "string"}},
            },
            reversibility=Reversibility.IRREVERSIBLE,
            egress=True,
        )
    )
    return m


def ctx(workload: str = "A", **kw: Any) -> EvalContext:
    base = {
        "manifest": demo_manifest(),
        "workload": workload,
        "tier": "test",
        # Caps tight enough that the budget guard is actually reachable in a
        # short synthetic run — an untriggerable guard is an untested guard.
        "config": {
            "budget": {
                "max_steps": 12,
                "max_tokens": 100_000,
                "max_per_tool": {"issue_refund": 2, "send_sms": 2},
            }
        },
    }
    base.update(kw)
    return EvalContext(**base)


class LedgerBuilder:
    """Fluent builder for a checkpoint sequence."""

    def __init__(self, workload: str = "A", run_id: str = "test-run") -> None:
        self.ledger = Ledger(run_id, "meridian", workload, "test", redact=False)
        self.commitments = Commitments(goal_digest="g")
        self.budget = Budget()

    def bind(self, entity: str, value: str, at: int | None = None) -> LedgerBuilder:
        self.commitments.bindings[entity] = Binding(
            value=value,
            value_hash=canonical_hash(value),
            resolved_at_step=len(self.ledger) if at is None else at,
            confidence_source="tool_resolution",
        )
        return self

    def constrain(self, text: str) -> LedgerBuilder:
        self.commitments.constraints.append(text)
        return self

    def step(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        source_class: SourceClass = SourceClass.OK,
        provenance: dict[str, int] | None = None,
        narrative: str = "",
    ) -> LedgerBuilder:
        manifest = demo_manifest()
        args = args or {}
        self.budget.steps_used = len(self.ledger)
        self.budget.tool_calls[tool] = self.budget.tool_calls.get(tool, 0) + 1
        self.ledger.append(
            commitments=self.commitments.model_copy(deep=True),
            pending_call=PendingCall(
                tool=tool,
                args=args,
                reversibility=manifest.reversibility(tool),
                arg_provenance=provenance or {},
            ),
            budget=self.budget.model_copy(deep=True),
            narrative=narrative,
        )
        self.ledger.attach_result(
            ObservedState(
                response_hash=canonical_hash(result or {}),
                source_class=source_class,
                preview=result or {},
            ),
            source_class,
        )
        return self

    def build(self) -> Ledger:
        return self.ledger
