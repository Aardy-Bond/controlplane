"""RECOVER — restore to L, undo what was committed after it, re-plan (PRD 5.5).

Localization without recovery is just a better error message. The interesting
and dangerous part is that between the fault at L+1 and its detection at N, the
agent may have already changed the world. Rolling the *conversation* back to L
while leaving those effects in place produces a state that exists in no
consistent history.

So recovery is three ordered obligations:

1. **Compensate** every side effect committed after L, in reverse order, and
   assert each compensator succeeded. A compensator that silently fails is
   worse than no rollback at all.
2. **Restore** agent state to checkpoint L.
3. **Re-plan** with a corrective note naming the violated constraint and the
   plan hash that must never be retried.

An irreversible effect after L cannot be discharged by step 1. That is not a
recovery failure to be retried — it is an escalation, immediately.

FR-30: the supervisor is read-only with respect to world state except inside
``authorized_mutation``. The guard is a runtime assertion, not a comment.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from .ledger import Ledger
from .manifest import ToolManifest
from .types import (
    Escalation,
    EscalationReason,
    Localization,
    Reversibility,
    SourceClass,
    Violation,
    canonical_hash,
)

__all__ = [
    "CompensationRecord",
    "ObserverPurityError",
    "RecoveryOutcome",
    "RecoveryEngine",
    "WorldAdapter",
    "authorized_mutation",
]


class ObserverPurityError(RuntimeError):
    """Raised when the supervisor attempts a mutation off the authorised path."""


_AUTHORIZED = threading.local()


def _is_authorized() -> bool:
    return getattr(_AUTHORIZED, "on", False)


@contextlib.contextmanager
def authorized_mutation():
    """The only window in which the supervisor may change world state."""
    prev = getattr(_AUTHORIZED, "on", False)
    _AUTHORIZED.on = True
    try:
        yield
    finally:
        _AUTHORIZED.on = prev


class WorldAdapter(Protocol):
    """What an environment must expose for rollback to be verifiable."""

    def state_hash(self) -> str:
        """Content hash of all mutable world state."""

    def compensate(self, tool: str, args: dict[str, Any], result: dict[str, Any]) -> bool:
        """Undo the effect of one committed call. Returns success."""


@dataclass
class CompensationRecord:
    step: int
    tool: str
    reversibility: str
    attempted: bool
    succeeded: bool
    detail: str = ""


@dataclass
class RecoveryOutcome:
    attempted: bool
    succeeded: bool
    restored_to_step: int
    compensations: list[CompensationRecord] = field(default_factory=list)
    corrective_note: str = ""
    forbidden_plan_hashes: list[str] = field(default_factory=list)
    escalation: Escalation | None = None
    world_hash_before: str = ""
    world_hash_after: str = ""
    world_matches_L: bool | None = None
    attempts_used: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "restored_to_step": self.restored_to_step,
            "compensations": [c.__dict__ for c in self.compensations],
            "corrective_note": self.corrective_note,
            "forbidden_plan_hashes": self.forbidden_plan_hashes,
            "world_hash_before": self.world_hash_before,
            "world_hash_after": self.world_hash_after,
            "world_matches_L": self.world_matches_L,
            "attempts_used": self.attempts_used,
            "escalated": self.escalation is not None,
            "escalation_reason": (self.escalation.reason.value if self.escalation else None),
            "detail": self.detail,
        }


class RecoveryEngine:
    def __init__(self, manifest: ToolManifest, max_attempts: int = 2) -> None:
        self.manifest = manifest
        self.max_attempts = max_attempts
        self._forbidden: dict[str, set[str]] = {}
        self._forbidden_readable: dict[str, list[str]] = {}

    def forbidden_for(self, run_id: str) -> set[str]:
        return self._forbidden.setdefault(run_id, set())

    def recover(
        self,
        ledger: Ledger,
        localization: Localization,
        violation: Violation,
        world: WorldAdapter | None,
        attempts_used: int,
        world_hash_at_L: str | None = None,
        implicates: str = "arguments",
    ) -> RecoveryOutcome:
        L = localization.last_good_step
        outcome = RecoveryOutcome(
            attempted=True,
            succeeded=False,
            restored_to_step=L,
            attempts_used=attempts_used,
        )
        outcome.world_hash_before = world.state_hash() if world else ""

        if L < 0:
            outcome.detail = "no good state exists: the first step already violated the invariant"
            outcome.escalation = self._escalate(
                EscalationReason.NO_COMPENSATOR, L, localization, violation, outcome
            )
            return outcome

        if attempts_used >= self.max_attempts:
            outcome.detail = (
                f"recovery budget exhausted after {attempts_used} attempt(s); "
                f"tier allows {self.max_attempts}"
            )
            outcome.escalation = self._escalate(
                EscalationReason.RECOVERY_BUDGET_EXHAUSTED, L, localization, violation, outcome
            )
            return outcome

        # 1. Compensate committed effects after L, newest first.
        blocked_by_irreversible = False
        for step in range(ledger.last_step, L, -1):
            cp = ledger[step]
            call = cp.pending_call
            if call is None or cp.result is None:
                continue
            rev = self.manifest.reversibility(call.tool)
            if rev is Reversibility.REVERSIBLE:
                outcome.compensations.append(
                    CompensationRecord(step, call.tool, rev.value, False, True, "read-only")
                )
                continue
            if rev is Reversibility.IRREVERSIBLE:
                blocked_by_irreversible = True
                outcome.compensations.append(
                    CompensationRecord(
                        step,
                        call.tool,
                        rev.value,
                        False,
                        False,
                        "irreversible effect already committed; cannot be undone",
                    )
                )
                continue
            ok = False
            detail = "no world adapter"
            if world is not None:
                with authorized_mutation():
                    ok = bool(world.compensate(call.tool, call.args, cp.result.preview))
                detail = "compensator ran" if ok else "compensator reported failure"
            outcome.compensations.append(
                CompensationRecord(step, call.tool, rev.value, True, ok, detail)
            )
            if not ok:
                outcome.detail = f"compensator for {call.tool} at step {step} failed"
                outcome.escalation = self._escalate(
                    EscalationReason.NO_COMPENSATOR, L, localization, violation, outcome
                )
                outcome.world_hash_after = world.state_hash() if world else ""
                return outcome

        outcome.world_hash_after = world.state_hash() if world else ""
        if world_hash_at_L is not None:
            outcome.world_matches_L = outcome.world_hash_after == world_hash_at_L

        if blocked_by_irreversible:
            outcome.detail = (
                "an irreversible action was committed after the last good step; the "
                "conversation can be rolled back but the world cannot"
            )
            outcome.escalation = self._escalate(
                EscalationReason.NO_COMPENSATOR, L, localization, violation, outcome
            )
            return outcome

        # 2 & 3. Restore and re-plan.
        forbidden = self.forbidden_for(ledger.run_id)
        readable = self._forbidden_readable.setdefault(ledger.run_id, [])
        # Only forbid the plan when the *arguments* were what went wrong. If the
        # result was the problem, the same call is what has to be retried, and
        # banning it would leave the agent unable to obtain the data the task
        # depends on — recovery would reliably turn a survivable fault into a
        # stalled run.
        if implicates == "arguments":
            for step in range(L + 1, ledger.last_step + 1):
                call = ledger[step].pending_call
                if call is not None and call.plan_hash not in forbidden:
                    forbidden.add(call.plan_hash)
                    readable.append(f"{call.tool}({_brief(call.args, 120)})")
        outcome.forbidden_plan_hashes = sorted(forbidden)
        outcome.corrective_note = self.corrective_note(
            violation,
            localization,
            readable,
            self.verified_facts(ledger, L),
            implicates=implicates,
            retry_call=(
                f"{ledger[L + 1].pending_call.tool}({_brief(ledger[L + 1].pending_call.args, 120)})"
                if implicates == "result"
                and L + 1 <= ledger.last_step
                and ledger[L + 1].pending_call is not None
                else ""
            ),
        )
        outcome.succeeded = True
        outcome.detail = f"restored to step {L}; {len(outcome.compensations)} effect(s) reconciled"
        return outcome

    def corrective_note(
        self,
        violation: Violation,
        localization: Localization,
        forbidden_calls: list[str],
        verified_facts: list[str] | None = None,
        implicates: str = "arguments",
        retry_call: str = "",
    ) -> str:
        """What the agent is told on resume.

        Restoring to checkpoint L means restoring what the agent *knew* at L,
        not merely truncating its transcript. Handing back the verified facts
        from the good prefix is the difference between recovery and starting
        over, and on a fifty-step run that difference is most of the cost.
        """
        note = [
            f"SUPERVISOR CORRECTION. Your run was rolled back to step "
            f"{localization.last_good_step}.",
            f"Violated invariant: {violation.invariant_id} ({violation.invariant_class.value}).",
            f"What went wrong: {violation.detail}",
            f"Constraint you must now satisfy: {violation.invariant_id} must hold at every "
            f"subsequent step.",
        ]
        if verified_facts:
            note.append(
                "\nVERIFIED FACTS from the good prefix — these are already established, "
                "do not re-query them:\n" + "\n".join(f"  - {f}" for f in verified_facts)
            )
        if forbidden_calls:
            shown = forbidden_calls[-6:]
            note.append(
                "\nDo NOT repeat these exact calls — they were rejected:\n"
                + "\n".join(f"  x {c}" for c in shown)
            )

        if implicates == "result":
            # The call was fine; what came back was not. Say so plainly, and
            # say what to do next, because "a check failed" with no instruction
            # is how a recoverable run becomes a stalled one.
            note.append(
                "\nThe ARGUMENTS you used were correct. The problem was the DATA that came "
                "back. Retry the same call to obtain a fresh, valid result, then continue "
                "the procedure from that point."
            )
            if retry_call:
                note.append(f"Retry exactly this call now:\n  > {retry_call}")
        else:
            note.append(
                "\nRe-derive the affected value from a verified source before using it, then "
                "continue the procedure from that point. A rejected call was rejected because "
                "of its arguments, so change what made it wrong rather than retrying it "
                "unchanged. If you cannot verify the value, say so and stop rather than "
                "proceeding."
            )
        return "\n".join(note)

    @staticmethod
    def verified_facts(ledger: Ledger, upto_step: int, limit: int = 60) -> list[str]:
        """One line per successfully verified step in the good prefix."""
        out: list[str] = []
        for cp in ledger.prefix(upto_step):
            call, res = cp.pending_call, cp.result
            if call is None or res is None or cp.blocked:
                continue
            if res.source_class is not SourceClass.OK:
                continue
            summary = {
                k: v
                for k, v in (res.preview or {}).items()
                if k
                in {
                    "value",
                    "name",
                    "segment",
                    "metric",
                    "policy_id",
                    "holder_name",
                    "status",
                    "amount",
                    "recommended_rate",
                    "base_rate",
                    "loading_pct",
                    "phone",
                }
            }
            out.append(f"step {cp.step}: {call.tool}({_brief(call.args)}) -> {_brief(summary)}")
        return out[-limit:]

    def _escalate(
        self,
        reason: EscalationReason,
        L: int,
        localization: Localization,
        violation: Violation,
        outcome: RecoveryOutcome,
    ) -> Escalation:
        """Escalations carry the localised step and RCA, never a raw trace (FR-29)."""
        proposed = (
            f"Roll back to step {L} and re-derive {violation.subject or 'the affected value'} "
            f"from a verified source. "
        )
        if reason is EscalationReason.NO_COMPENSATOR:
            irrev = [c for c in outcome.compensations if c.reversibility == "irreversible"]
            if irrev:
                proposed += (
                    "Human action required for the irreversible effect(s): "
                    + ", ".join(f"{c.tool} at step {c.step}" for c in irrev)
                    + "."
                )
        return Escalation(
            reason=reason,
            localized_step=L,
            rca=localization.rca,
            proposed_plan=proposed,
            violation=violation,
        )


def world_state_hash(state: Any) -> str:
    return canonical_hash(state)


def _brief(value: Any, limit: int = 90) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
