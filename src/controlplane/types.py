"""Core value types for ControlPlane.

Everything downstream — invariants, localization, recovery — is a pure function
of the checkpoint sequence defined here. No model internals appear anywhere
(FR-3): a checkpoint holds call text, result text, and hashes of external state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def canonical_hash(value: Any) -> str:
    """Stable content hash. Used for state equality, plan identity, and the
    ledger's tamper-evident chain, so it must not depend on dict ordering."""
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class SourceClass(str, Enum):
    """Provenance quality of an observed tool result.

    ``denied`` is deliberately distinct from an empty ``ok`` result. An
    instrument that cannot tell "nothing is there" from "I was not allowed to
    look" manufactures false reassurance (PRD 5.2).
    """

    OK = "ok"
    ERROR_TAGGED = "error_tagged"
    UNLABELLED = "unlabelled"
    DENIED = "denied"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    COMPENSABLE = "compensable"
    IRREVERSIBLE = "irreversible"


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class CostClass(str, Enum):
    """How expensive an invariant is to evaluate. Drives inline/async demotion."""

    MICROS = "us"
    MILLIS = "ms"
    LLM = "llm"

    @property
    def nominal_ms(self) -> float:
        return {"us": 0.05, "ms": 8.0, "llm": 900.0}[self.value]


class InvariantClass(str, Enum):
    BINDING = "binding"
    SCHEMA = "schema"
    PRECONDITION = "precondition"
    PROVENANCE = "provenance"
    ENTITLEMENT = "entitlement"
    PROGRESS = "progress"
    BUDGET = "budget"
    SAFETY = "safety"
    SEMANTIC = "semantic"


class Binding(BaseModel):
    """An entity the agent has committed to, e.g. which policy it is acting on."""

    value: str
    value_hash: str
    resolved_at_step: int
    confidence_source: str


class Commitments(BaseModel):
    bindings: dict[str, Binding] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    goal_digest: str = ""


class ObservedState(BaseModel):
    response_hash: str
    schema_version: str = "v1"
    source_class: SourceClass = SourceClass.UNLABELLED
    # Kept for replay and for invariants that must read result content. PII is
    # redacted before this ever reaches the ledger (FR-9).
    preview: dict[str, Any] = Field(default_factory=dict)


class PendingCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    args_hash: str = ""
    reversibility: Reversibility = Reversibility.IRREVERSIBLE
    # arg name -> step index the value originated from. This is the provenance
    # graph the non-monotone localizer walks.
    arg_provenance: dict[str, int] = Field(default_factory=dict)
    # Per-argument hash of the *pre-redaction* value. Identity comparisons —
    # "is this the entity we resolved?" — must run on these, never on the
    # stored text, which has had personal data stripped out of it (FR-9).
    arg_hashes: dict[str, str] = Field(default_factory=dict)

    def model_post_init(self, _ctx: Any) -> None:
        if not self.args_hash:
            self.args_hash = canonical_hash(self.args)
        if not self.arg_hashes:
            self.arg_hashes = {k: canonical_hash(str(v)) for k, v in self.args.items()}

    @property
    def plan_hash(self) -> str:
        return canonical_hash({"tool": self.tool, "args": self.args})


class Budget(BaseModel):
    steps_used: int = 0
    tokens: int = 0
    wall_ms: float = 0.0
    usd: float = 0.0
    tool_calls: dict[str, int] = Field(default_factory=dict)


class Checkpoint(BaseModel):
    """One append-only record per step boundary (FR-6).

    ``step`` is the *logical* index used by invariants and binary search.
    ``epoch`` increments on every rollback, so after recovery a new checkpoint
    can reuse a logical step index that an earlier, abandoned attempt also used.
    The physical log keeps both; the logical view keeps only the live one.
    """

    run_id: str
    tenant: str
    workload: str
    tier: str
    step: int
    epoch: int = 0
    ts: str = Field(default_factory=utcnow)
    parent_span: str = ""

    commitments: Commitments = Field(default_factory=Commitments)
    observed_state: dict[str, ObservedState] = Field(default_factory=dict)
    pending_call: PendingCall | None = None
    result: ObservedState | None = None
    source_class: SourceClass = SourceClass.UNLABELLED
    budget: Budget = Field(default_factory=Budget)

    # Free-form agent-visible text (thought / final answer), redacted.
    narrative: str = ""

    # The call was refused pre-commit; recorded so the audit trail shows what
    # was attempted, and so localization can search over the attempt.
    blocked: bool = False
    # Set on the marker checkpoint written when a rollback occurs.
    rollback_to: int | None = None

    # Tamper-evident chain. Each checkpoint commits to its predecessor.
    prev_hash: str = ""
    self_hash: str = ""

    def compute_hash(self) -> str:
        body = self.model_dump(exclude={"self_hash"}, mode="json")
        return canonical_hash(body)


class Verdict(BaseModel):
    """Result of evaluating one invariant against one ledger prefix."""

    holds: bool
    invariant_id: str
    detail: str = ""
    # Field or entity the violation attaches to; seeds the provenance walk.
    subject: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls, invariant_id: str) -> Verdict:
        return cls(holds=True, invariant_id=invariant_id)

    @classmethod
    def fail(
        cls,
        invariant_id: str,
        detail: str,
        subject: str = "",
        **evidence: Any,
    ) -> Verdict:
        return cls(
            holds=False,
            invariant_id=invariant_id,
            detail=detail,
            subject=subject,
            evidence=evidence,
        )


class Violation(BaseModel):
    invariant_id: str
    invariant_class: InvariantClass
    severity: Severity
    detected_at_step: int
    detected_by: str  # "inline" | "async" | "human"
    detail: str
    subject: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class RCA(BaseModel):
    """Three-layer root cause (FR-20). A single "root cause" hides the fact that
    a fault needs a spark, something to spread it, and an absence to hide it."""

    trigger: str
    amplifier: str
    concealer: str


class Localization(BaseModel):
    last_good_step: int
    method: str  # "binary_search" | "provenance_fallback"
    quality: str  # "exact" | "estimated"
    evaluations: int
    wall_ms: float
    confidence_low: int
    confidence_high: int
    rca: RCA | None = None
    candidates: list[int] = Field(default_factory=list)


class EscalationReason(str, Enum):
    IRREVERSIBLE_FAILED_VERIFICATION = "irreversible_failed_verification"
    RECOVERY_BUDGET_EXHAUSTED = "recovery_budget_exhausted"
    NO_COMPENSATOR = "no_compensator"


class Escalation(BaseModel):
    reason: EscalationReason
    localized_step: int
    rca: RCA | None
    proposed_plan: str
    violation: Violation


class Incident(BaseModel):
    """What a human or the dashboard actually consumes."""

    incident_id: str
    run_id: str
    tenant: str
    workload: str
    tier: str
    violation: Violation
    localization: Localization | None = None
    recovery: dict[str, Any] = Field(default_factory=dict)
    escalation: Escalation | None = None
    ts: str = Field(default_factory=utcnow)
