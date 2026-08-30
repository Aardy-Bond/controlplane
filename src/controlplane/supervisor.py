"""The supervisor: one process interposed between an agent and its tools.

Flow per step:

    agent proposes call
        -> INTERCEPT: build a *candidate* checkpoint (not yet committed)
        -> ASSERT (inline, budgeted): evaluate the tier's inline classes over
           prefix + [candidate]
        -> allow / block
        -> tool executes
        -> commit the checkpoint with its result
        -> ASSERT (post): result-dependent checks
        -> violation? LOCALIZE -> RECOVER -> resume
        -> queue the tier's async classes for the deep path

Three things here are load-bearing:

**Prevention is a function of the latency budget, not of intent.** An invariant
whose cost class exceeds the tier's inline budget is demoted to async
automatically, and the demotion is logged rather than hidden (FR-14). Workload A
therefore prevents less than workload C by construction — which is exactly the
asymmetry the product exists to manage.

**Irreversible calls ignore the budget** (FR-25). A tier's latency SLO is a
promise about reads. It is not a licence to send money unverified.

**Inline and async feed the identical localize/recover engine** (FR-21). Late
detection is then a difference in blast radius, not a difference in capability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .invariants.base import REGISTRY, EvalContext, Invariant, InvariantRegistry, timed_eval
from .ledger import Ledger, LedgerStore
from .llm import LLMClient
from .localize import LocalizationEngine
from .manifest import ToolManifest
from .otel import SpanRecorder
from .policy import PolicyRegistry, WorkloadPolicy
from .recover import RecoveryEngine, RecoveryOutcome, WorldAdapter
from .types import (
    Binding,
    Budget,
    Commitments,
    Incident,
    ObservedState,
    PendingCall,
    Reversibility,
    Severity,
    SourceClass,
    Violation,
    canonical_hash,
)

__all__ = ["Decision", "Supervisor", "RunSupervisor", "SupervisorHealth"]


@dataclass
class SupervisorHealth:
    """Degradation switchboard for GS-6 / T-503.

    A supervisor that cannot be turned off in a controlled way will be turned
    off in an uncontrolled way the first time it misbehaves in production.
    """

    interceptor_attached: bool = True
    judge_available: bool = True
    judge_latency_ms: float = 0.0
    judge_rate_limited: bool = False

    @property
    def degraded(self) -> bool:
        return not (
            self.interceptor_attached and self.judge_available and not self.judge_rate_limited
        )


@dataclass
class Decision:
    allow: bool
    step: int
    violations: list[Violation] = field(default_factory=list)
    inline_ms: float = 0.0
    checks_run: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)
    reason: str = ""
    pre_commit_verified: bool = False

    @property
    def blocked(self) -> bool:
        return not self.allow


class Supervisor:
    """Process-wide control plane. One deployment serves every workload (FR-6.x)."""

    def __init__(
        self,
        manifest: ToolManifest,
        policy: PolicyRegistry | None = None,
        registry: InvariantRegistry | None = None,
        store: LedgerStore | None = None,
        judge: LLMClient | None = None,
        runs_root: Path | None = None,
    ) -> None:
        self.manifest = manifest
        self.policy = policy or PolicyRegistry()
        self.registry = registry or REGISTRY
        self.store = store or LedgerStore(Path(runs_root or "runs/ledgers"))
        self.judge = judge
        self.localizer = LocalizationEngine(judge=judge)
        self.health = SupervisorHealth()
        self.incidents: list[Incident] = []
        self.sabotage_validated: dict[str, str] = {}
        # Shared judge quota, arbitrated by priority (FR-32).
        self.judge_quota = JudgeQuota()

    def start_run(
        self,
        workload: str,
        *,
        goal: str,
        caller: str = "",
        entitlements: set[str] | None = None,
        world: WorldAdapter | None = None,
        world_view: dict[str, Any] | None = None,
        run_id: str | None = None,
        enabled: bool = True,
    ) -> RunSupervisor:
        wp: WorkloadPolicy = self.policy.for_workload(workload)
        rid = run_id or f"{workload.lower()}-{uuid.uuid4().hex[:10]}"
        ledger = self.store.open(rid, wp.tenant, workload, wp.tier.name)
        return RunSupervisor(
            parent=self,
            policy=wp,
            ledger=ledger,
            goal=goal,
            caller=caller,
            entitlements=entitlements or set(),
            world=world,
            world_view=world_view or {},
            enabled=enabled,
        )

    # -- attestation (FR-35) ----------------------------------------------

    def guard_liveness(self, workload: str | None = None) -> dict[str, Any]:
        invs = self.registry.for_workload(workload) if workload else self.registry.all()
        validated = [i for i in invs if i.id in self.sabotage_validated]
        return {
            "workload": workload or "all",
            "active": len(invs),
            "sabotage_validated": len(validated),
            "ratio": f"{len(validated)} / {len(invs)}",
            "unvalidated": sorted(i.id for i in invs if i.id not in self.sabotage_validated),
            "last_validated": self.sabotage_validated,
        }


@dataclass
class JudgeQuota:
    """Priority arbitration for the shared judge model (FR-32).

    Irreversible pre-commit verification pre-empts batch adjudication, always.
    Without this, workload C's 60-step batch can starve workload A's refund
    check, and the cheapest workload to degrade would be the one holding money.
    """

    capacity: int = 4
    in_flight: int = 0
    granted: int = 0
    preempted: int = 0
    queued_ms: float = 0.0

    def priority(self, reversibility: Reversibility, tier: str, queue_age_s: float) -> float:
        base = {"irreversible": 100.0, "compensable": 40.0, "reversible": 10.0}[reversibility.value]
        tier_weight = {"interactive-external": 30.0, "interactive-internal": 15.0}.get(tier, 5.0)
        return base + tier_weight + min(queue_age_s, 30.0)

    def acquire(self, reversibility: Reversibility, tier: str, queue_age_s: float = 0.0) -> bool:
        prio = self.priority(reversibility, tier, queue_age_s)
        if self.in_flight < self.capacity:
            self.in_flight += 1
            self.granted += 1
            return True
        # Irreversible pre-commit verification always pre-empts.
        if prio >= 100.0:
            self.preempted += 1
            self.granted += 1
            return True
        return False

    def release(self) -> None:
        self.in_flight = max(0, self.in_flight - 1)


class RunSupervisor:
    """Per-run interception. Not thread-safe by design: one run, one sequence."""

    def __init__(
        self,
        parent: Supervisor,
        policy: WorkloadPolicy,
        ledger: Ledger,
        goal: str,
        caller: str,
        entitlements: set[str],
        world: WorldAdapter | None,
        world_view: dict[str, Any],
        enabled: bool = True,
    ) -> None:
        self.parent = parent
        self.policy = policy
        self.tier = policy.tier
        self.ledger = ledger
        self.goal = goal
        self.world = world
        self.enabled = enabled
        self.spans = SpanRecorder(ledger.run_id)

        self.ctx = EvalContext(
            manifest=parent.manifest,
            workload=policy.workload,
            tier=self.tier.name,
            goal=goal,
            caller=caller,
            entitlements=entitlements,
            world_view=world_view,
            config={"budget": self.tier.budget},
        )
        self.recovery = RecoveryEngine(parent.manifest, self.tier.max_recovery_attempts)

        self.commitments = Commitments(goal_digest=canonical_hash(goal))
        self.budget = Budget()
        self.async_queue: list[tuple[int, str]] = []
        self.recovery_attempts = 0
        self.incidents: list[Incident] = []
        self.world_hash_by_step: dict[int, str] = {}
        self._last_async_step = 0
        self.metrics = RunMetrics()
        self._split_cache: dict[str, tuple[list[Invariant], list[Invariant]]] = {}

    # -- invariant selection ----------------------------------------------

    def _split_invariants(self) -> tuple[list[Invariant], list[Invariant]]:
        """Partition the library into inline and async for this tier (FR-14).

        Demotion is automatic and logged. An invariant whose nominal cost
        exceeds the tier's blocking budget cannot be run inline no matter what
        the config says it wants, so the config does not get to pretend.
        """
        key = f"{self.policy.workload}:{self.tier.name}"
        if key in self._split_cache:
            return self._split_cache[key]

        inline: list[Invariant] = []
        deferred: list[Invariant] = []
        for inv in self.parent.registry.for_workload(self.policy.workload):
            wants_inline = self.tier.wants_inline(inv.invariant_class.value)
            affordable = inv.inline_cost_class.nominal_ms <= self.tier.inline_budget_p95_ms
            if wants_inline and affordable:
                inline.append(inv)
            else:
                deferred.append(inv)
                if wants_inline and not affordable:
                    self.metrics.demotions.append(
                        f"{inv.id} demoted to async: nominal "
                        f"{inv.inline_cost_class.nominal_ms}ms exceeds tier budget "
                        f"{self.tier.inline_budget_p95_ms}ms"
                    )
        self._split_cache[key] = (inline, deferred)
        return inline, deferred

    # -- interception ------------------------------------------------------

    def intercept(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        arg_provenance: dict[str, int] | None = None,
        narrative: str = "",
        bindings_update: dict[str, Any] | None = None,
        constraints_add: list[str] | None = None,
    ) -> Decision:
        """Evaluate a proposed call before it executes.

        The candidate checkpoint is written to the ledger either way. A blocked
        attempt is part of the audit trail, and localization needs it in the
        sequence to search over.
        """
        spec = self.parent.manifest.get(tool)
        if bindings_update:
            self._apply_bindings(bindings_update)
        if constraints_add:
            for c in constraints_add:
                if c not in self.commitments.constraints:
                    self.commitments.constraints.append(c)

        call = PendingCall(
            tool=tool,
            args=args,
            reversibility=spec.reversibility,
            arg_provenance=arg_provenance or {},
        )
        self.budget.steps_used = len(self.ledger)
        self.budget.tool_calls[tool] = self.budget.tool_calls.get(tool, 0) + 1

        span = self.spans.start_tool_span(tool, len(self.ledger))

        if not self.enabled or not self.parent.health.interceptor_attached:
            # Observe-only: record, never block (FR-5).
            self.ledger.append(
                commitments=self.commitments.model_copy(deep=True),
                pending_call=call,
                budget=self.budget.model_copy(deep=True),
                narrative=narrative,
                parent_span=span,
            )
            return Decision(allow=True, step=self.ledger.last_step, reason="observe_only")

        cp = self.ledger.append(
            commitments=self.commitments.model_copy(deep=True),
            pending_call=call,
            budget=self.budget.model_copy(deep=True),
            narrative=narrative,
            parent_span=span,
        )

        inline, deferred = self._split_invariants()
        irreversible = spec.reversibility is Reversibility.IRREVERSIBLE
        pre_commit = irreversible and self.tier.irreversible_policy == "verify_before_commit"

        # An irreversible call is verified at full depth regardless of budget.
        to_run = list(inline)
        if pre_commit:
            for inv in deferred:
                if inv.inline_cost_class.value != "llm" or self.parent.health.judge_available:
                    to_run.append(inv)

        decision = self._assert(to_run, cp.step, "inline", budget_exempt=pre_commit)
        decision.pre_commit_verified = pre_commit
        decision.demoted = [i.id for i in deferred]

        if decision.blocked:
            # The refusal is itself an auditable event: what was attempted, and
            # what stopped it.
            self.ledger.mark_blocked()
            self.metrics.blocked_calls += 1
            if irreversible:
                self.metrics.irreversible_blocked += 1
        else:
            self.async_queue.append((cp.step, tool))

        return decision

    def commit(
        self,
        result: dict[str, Any],
        source_class: SourceClass = SourceClass.OK,
        tokens: int = 0,
        usd: float = 0.0,
        wall_ms: float = 0.0,
    ) -> Decision:
        """Attach a tool result and run the result-dependent checks."""
        self.budget.tokens += tokens
        self.budget.usd += usd
        self.budget.wall_ms += wall_ms

        observed = ObservedState(
            response_hash=canonical_hash(result),
            source_class=source_class,
            preview=result,
        )
        self.ledger.attach_result(observed, source_class, budget=self.budget.model_copy(deep=True))
        step = self.ledger.last_step
        self.spans.end_tool_span(step, source_class.value)

        if self.world is not None:
            self.world_hash_by_step[step] = self.world.state_hash()

        if not self.enabled or not self.parent.health.interceptor_attached:
            return Decision(allow=True, step=step, reason="observe_only")

        inline, _ = self._split_invariants()
        return self._assert(inline, step, "inline")

    # -- assertion ---------------------------------------------------------

    def _assert(
        self,
        invariants: list[Invariant],
        step: int,
        path: str,
        budget_exempt: bool = False,
    ) -> Decision:
        prefix = self.ledger.prefix(step)
        violations: list[Violation] = []
        ran: list[str] = []
        spent = 0.0
        budget_ms = self.tier.inline_budget_p95_ms

        for inv in invariants:
            if not budget_exempt and path == "inline" and spent >= budget_ms:
                self.metrics.budget_cutoffs += 1
                self.metrics.demotions.append(
                    f"{inv.id} skipped inline at step {step}: tier budget "
                    f"{budget_ms}ms already spent"
                )
                continue
            verdict, ms = timed_eval(inv, prefix, self.ctx)
            spent += ms
            ran.append(inv.id)
            self.metrics.record_check(inv.id, ms, path)
            if not verdict.holds:
                violations.append(
                    Violation(
                        invariant_id=inv.id,
                        invariant_class=inv.invariant_class,
                        severity=inv.severity,
                        detected_at_step=step,
                        detected_by=path,
                        detail=verdict.detail,
                        subject=verdict.subject,
                        evidence=verdict.evidence,
                    )
                )

        if path == "inline":
            self.metrics.inline_ms_samples.append(spent)

        blocking = [v for v in violations if v.severity is Severity.BLOCK]
        return Decision(
            allow=not blocking,
            step=step,
            violations=violations,
            inline_ms=spent,
            checks_run=ran,
            reason=blocking[0].detail if blocking else "",
        )

    def drain_async(self, force: bool = False) -> list[Violation]:
        """Run the deep path. No budget; this is where the expensive checks live.

        The deep path *lags*. That lag is not an implementation compromise, it
        is the thing being modelled: a check demoted off the critical path finds
        the fault several steps after the agent has already acted on it. For a
        tight-latency workload this is where most detection actually happens,
        which is precisely why the localizer has to be exact rather than
        approximately helpful.
        """
        # An observe-only run must be genuinely silent. Leaving the deep path
        # running would let the "supervisor off" arm of the ablation report
        # detections, which would contaminate the baseline every other number
        # is measured against.
        if not self.enabled or not self.parent.health.interceptor_attached:
            return []

        _, deferred = self._split_invariants()
        if not deferred or not len(self.ledger):
            return []

        lag = self.tier.async_lag_steps
        step = self.ledger.last_step
        if not force and lag > 0 and (step - self._last_async_step) < lag:
            self.metrics.async_backlog = step - self._last_async_step
            return []
        self._last_async_step = step

        decision = self._assert(deferred, step, "async", budget_exempt=True)
        self.metrics.async_detections += len(decision.violations)
        return decision.violations

    # -- the loop that closes ---------------------------------------------

    def handle_violation(self, violation: Violation) -> Incident:
        """LOCALIZE then RECOVER. Identical for inline and async detections."""
        invariant = self.parent.registry.get(violation.invariant_id)
        localization = self.parent.localizer.localize(self.ledger, invariant, self.ctx, violation)
        world_at_L = self.world_hash_by_step.get(localization.last_good_step)

        outcome: RecoveryOutcome = self.recovery.recover(
            self.ledger,
            localization,
            violation,
            self.world,
            self.recovery_attempts,
            world_hash_at_L=world_at_L,
            implicates=invariant.implicates,
        )
        self.recovery_attempts += 1

        if outcome.succeeded:
            self.ledger.rollback_to(localization.last_good_step)
            self._restore_state_to(localization.last_good_step)
            self.metrics.recoveries += 1
        if outcome.escalation is not None:
            self.metrics.escalations += 1

        incident = Incident(
            incident_id=f"INC-{uuid.uuid4().hex[:8]}",
            run_id=self.ledger.run_id,
            tenant=self.ledger.tenant,
            workload=self.policy.workload,
            tier=self.tier.name,
            violation=violation,
            localization=localization,
            recovery=outcome.as_dict(),
            escalation=outcome.escalation,
        )
        self.incidents.append(incident)
        self.parent.incidents.append(incident)
        return incident

    def _restore_state_to(self, step: int) -> None:
        """Restore the supervisor's own live state to checkpoint L.

        Budget counters and entity bindings are part of what "step L" means. If
        per-tool call counts keep climbing across a rollback, the budget guard
        eventually fires on the recovery instead of on a runaway agent — the
        supervisor would be reporting its own retries as the agent's fault.
        """
        if step < 0 or step > self.ledger.last_step:
            self.budget = Budget()
            self.commitments = Commitments(goal_digest=self.commitments.goal_digest)
            self._last_async_step = 0
            return
        cp = self.ledger[step]
        self.budget = cp.budget.model_copy(deep=True)
        self.commitments = cp.commitments.model_copy(deep=True)

        # A checkpoint's commitments snapshot is taken when the call is
        # proposed, so it cannot contain the binding that call itself went on to
        # resolve. Replaying the successful resolutions in the prefix recovers
        # what the agent actually knew at L — without it, a rollback to the very
        # step that resolved an entity would land in a state where that entity
        # is unbound, and the binding guard would fire on its own recovery.
        for prior in self.ledger.prefix(step):
            call, res = prior.pending_call, prior.result
            if call is None or res is None or prior.blocked:
                continue
            if res.source_class is not SourceClass.OK:
                continue
            for entity in self.parent.manifest.get(call.tool).resolves:
                if entity in call.args:
                    self.commitments.bindings[entity] = Binding(
                        value=str(call.args[entity]),
                        value_hash=call.arg_hashes.get(entity, "")
                        or canonical_hash(str(call.args[entity])),
                        resolved_at_step=prior.step,
                        confidence_source="tool_resolution",
                    )
        self._last_async_step = min(self._last_async_step, step)

    # -- commitments -------------------------------------------------------

    def _apply_bindings(self, updates: dict[str, Any]) -> None:
        # The resolution happened at the step that just committed, not at the
        # next one about to be written.
        step = max(0, len(self.ledger) - 1)
        for entity, value in updates.items():
            self.commitments.bindings[entity] = Binding(
                value=str(value),
                value_hash=canonical_hash(str(value)),
                resolved_at_step=step,
                confidence_source="tool_resolution",
            )

    def declare_constraint(self, constraint: str) -> None:
        if constraint not in self.commitments.constraints:
            self.commitments.constraints.append(constraint)

    # -- reporting ---------------------------------------------------------

    def finish(self) -> dict[str, Any]:
        self.parent.store.persist(self.ledger)
        chain_ok, chain_detail = self.ledger.verify_chain()
        return {
            "run_id": self.ledger.run_id,
            "tenant": self.ledger.tenant,
            "workload": self.policy.workload,
            "tier": self.tier.name,
            "steps": len(self.ledger),
            "physical_records": len(self.ledger.physical_log),
            "epoch": self.ledger.epoch,
            "chain_intact": chain_ok,
            "chain_detail": chain_detail,
            "replay_identical": self.ledger.replay_identical(),
            "incidents": [i.model_dump(mode="json") for i in self.incidents],
            "pii_spans_redacted_at_write": len(self.ledger.pii_spans_written),
            "metrics": self.metrics.summary(),
            "budget": self.budget.model_dump(),
        }


@dataclass
class RunMetrics:
    check_ms: dict[str, list[float]] = field(default_factory=dict)
    check_counts: dict[str, int] = field(default_factory=dict)
    inline_ms_samples: list[float] = field(default_factory=list)
    demotions: list[str] = field(default_factory=list)
    blocked_calls: int = 0
    irreversible_blocked: int = 0
    async_detections: int = 0
    async_backlog: int = 0
    recoveries: int = 0
    escalations: int = 0
    budget_cutoffs: int = 0

    def record_check(self, inv_id: str, ms: float, path: str) -> None:
        self.check_ms.setdefault(inv_id, []).append(ms)
        key = f"{inv_id}:{path}"
        self.check_counts[key] = self.check_counts.get(key, 0) + 1

    def summary(self) -> dict[str, Any]:
        import statistics

        samples = sorted(self.inline_ms_samples)
        p95 = samples[int(0.95 * (len(samples) - 1))] if samples else 0.0
        return {
            "inline_ms_p95": round(p95, 4),
            "inline_ms_mean": round(statistics.fmean(samples), 4) if samples else 0.0,
            "checks_evaluated": sum(self.check_counts.values()),
            "blocked_calls": self.blocked_calls,
            "irreversible_blocked": self.irreversible_blocked,
            "async_detections": self.async_detections,
            "recoveries": self.recoveries,
            "escalations": self.escalations,
            "budget_cutoffs": self.budget_cutoffs,
            "demotions": self.demotions,
        }
