"""Append-only checkpoint ledger (PRD 5.2).

Three properties everything else depends on:

1. **Prefix-evaluable.** Invariants are pure functions of ``ledger[:n]``. That
   is what makes binary search over the ledger meaningful.
2. **Replayable.** A run reconstructs exactly from its checkpoints with no
   dependence on live tool state (FR-7), so localization works hours later.
3. **Tamper-evident.** Each checkpoint commits to its predecessor's hash, so a
   silently edited audit trail is detectable rather than merely unlikely.

Tenant isolation (FR-8) is enforced at the store, not by convention: a read
requires the tenant key, and there is no API that returns another tenant's runs.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .pii import redact_structure
from .types import (
    Budget,
    Checkpoint,
    Commitments,
    ObservedState,
    PendingCall,
    SourceClass,
    canonical_hash,
)

__all__ = ["Ledger", "LedgerStore", "TenantIsolationError"]


class TenantIsolationError(PermissionError):
    """Raised on any attempt to read a run belonging to another tenant."""


class Ledger:
    """The checkpoint sequence for a single run.

    Two views over the same data:

    * ``physical_log`` — every checkpoint ever written, including attempts that
      were later rolled back, plus rollback markers. This is the audit trail and
      it is genuinely append-only; nothing is ever removed.
    * the **logical view** (``checkpoints``, ``prefix``, ``len``) — the sequence
      the agent is currently living in. A rollback truncates this view; it does
      not delete history.

    Invariants and binary search run over the logical view, because "what is
    true of the run right now" is the question they answer. Compliance reads the
    physical log, because "what did this system attempt" is the question it asks.
    """

    def __init__(
        self,
        run_id: str,
        tenant: str,
        workload: str,
        tier: str,
        redact: bool = True,
    ) -> None:
        self.run_id = run_id
        self.tenant = tenant
        self.workload = workload
        self.tier = tier
        self.redact = redact
        self._physical: list[Checkpoint] = []
        self._checkpoints: list[Checkpoint] = []  # logical view
        self._epoch = 0
        self._pii_spans_written: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # -- write ------------------------------------------------------------

    def append(
        self,
        *,
        commitments: Commitments,
        pending_call: PendingCall | None,
        observed_state: dict[str, ObservedState] | None = None,
        result: ObservedState | None = None,
        source_class: SourceClass = SourceClass.UNLABELLED,
        budget: Budget | None = None,
        narrative: str = "",
        parent_span: str = "",
        blocked: bool = False,
    ) -> Checkpoint:
        """Write one checkpoint. PII is redacted here, at write time (FR-9)."""
        with self._lock:
            step = len(self._checkpoints)

            if self.redact and pending_call is not None:
                safe_args, spans = redact_structure(pending_call.args)
                if spans:
                    self._record_spans(step, "args", spans)
                    # Hashes are computed over the *original* values and carried
                    # across, so identity checks still compare real identity
                    # while the stored text carries none of it.
                    pending_call = PendingCall(
                        tool=pending_call.tool,
                        args=safe_args,
                        args_hash=pending_call.args_hash,
                        reversibility=pending_call.reversibility,
                        arg_provenance=pending_call.arg_provenance,
                        arg_hashes=pending_call.arg_hashes,
                    )

            if self.redact and result is not None and result.preview:
                safe_preview, spans = redact_structure(result.preview)
                if spans:
                    self._record_spans(step, "result", spans)
                    result = ObservedState(
                        response_hash=result.response_hash,
                        schema_version=result.schema_version,
                        source_class=result.source_class,
                        preview=safe_preview,
                    )

            if self.redact and narrative:
                from .pii import redact as redact_text

                narrative, spans = redact_text(narrative)
                if spans:
                    self._record_spans(step, "narrative", spans)

            # Deep-copied on the way in, without exception. A caller that hands
            # over a live object it goes on mutating would silently rewrite
            # history: the stored checkpoint changes, its hash stops matching,
            # and — far worse than a broken chain — invariants evaluating an
            # early prefix start seeing state from the future, which is exactly
            # the assumption binary search rests on. Too important to leave to
            # every call site remembering.
            cp = Checkpoint(
                run_id=self.run_id,
                tenant=self.tenant,
                workload=self.workload,
                tier=self.tier,
                step=step,
                epoch=self._epoch,
                parent_span=parent_span,
                commitments=commitments.model_copy(deep=True),
                observed_state={
                    k: v.model_copy(deep=True) for k, v in (observed_state or {}).items()
                },
                pending_call=pending_call.model_copy(deep=True) if pending_call else None,
                result=result.model_copy(deep=True) if result else None,
                source_class=source_class,
                budget=(budget or Budget(steps_used=step)).model_copy(deep=True),
                narrative=narrative,
                blocked=blocked,
                prev_hash=self._physical[-1].self_hash if self._physical else "genesis",
            )
            cp.self_hash = cp.compute_hash()
            self._physical.append(cp)
            self._checkpoints.append(cp)
            return cp

    def rollback_to(self, step: int) -> Checkpoint:
        """Truncate the logical view to ``step``; history is retained.

        Returns the marker checkpoint appended to the physical log, so the audit
        trail records that a rollback happened and where it went.
        """
        with self._lock:
            self._epoch += 1
            marker = Checkpoint(
                run_id=self.run_id,
                tenant=self.tenant,
                workload=self.workload,
                tier=self.tier,
                step=step,
                epoch=self._epoch,
                narrative=f"rollback to step {step}",
                rollback_to=step,
                prev_hash=self._physical[-1].self_hash if self._physical else "genesis",
            )
            marker.self_hash = marker.compute_hash()
            self._physical.append(marker)
            self._checkpoints = self._checkpoints[: step + 1]
            return marker

    def attach_result(
        self,
        result: ObservedState,
        source_class: SourceClass,
        budget: Budget | None = None,
        narrative: str | None = None,
    ) -> Checkpoint:
        """Attach a tool result to the step that issued the call.

        Nothing is mutated: a corrected copy is appended to the physical log and
        replaces the step in the logical view. The original attempt stays in the
        audit trail, which is the behaviour an auditor expects from an
        append-only store.
        """
        with self._lock:
            if self.redact and result.preview:
                safe_preview, spans = redact_structure(result.preview)
                if spans:
                    self._record_spans(len(self._checkpoints) - 1, "result", spans)
                    result = ObservedState(
                        response_hash=result.response_hash,
                        schema_version=result.schema_version,
                        source_class=result.source_class,
                        preview=safe_preview,
                    )
            if self.redact and narrative:
                from .pii import redact as redact_text

                narrative, spans = redact_text(narrative)
                if spans:
                    self._record_spans(len(self._checkpoints) - 1, "narrative", spans)

            updates: dict[str, Any] = {
                "result": result.model_dump(),
                "source_class": source_class.value,
            }
            if budget is not None:
                updates["budget"] = budget.model_dump()
            if narrative is not None:
                updates["narrative"] = narrative
            return self._revise_last(updates)

    def mark_blocked(self) -> Checkpoint:
        """Record that the most recent proposed call was refused pre-commit."""
        with self._lock:
            return self._revise_last({"blocked": True})

    def _revise_last(self, updates: dict[str, Any]) -> Checkpoint:
        body = self._checkpoints[-1].model_dump()
        body.update(updates)
        body["self_hash"] = ""
        body["prev_hash"] = self._physical[-1].self_hash
        revised = Checkpoint(**body)
        revised.self_hash = revised.compute_hash()
        self._physical.append(revised)
        self._checkpoints[-1] = revised
        return revised

    def _record_spans(self, step: int, field: str, spans: list) -> None:
        for s in spans:
            self._pii_spans_written.append({"step": step, "field": field, **s.as_dict()})

    # -- read -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._checkpoints)

    def __getitem__(self, idx: int) -> Checkpoint:
        return self._checkpoints[idx]

    def __iter__(self):
        return iter(self._checkpoints)

    @property
    def checkpoints(self) -> list[Checkpoint]:
        return list(self._checkpoints)

    def prefix(self, n: int) -> list[Checkpoint]:
        """Checkpoints 0..n inclusive — the unit an invariant is evaluated on."""
        return self._checkpoints[: n + 1]

    @property
    def last_step(self) -> int:
        return len(self._checkpoints) - 1

    @property
    def physical_log(self) -> list[Checkpoint]:
        """Everything ever written, including rolled-back attempts and markers."""
        return list(self._physical)

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def pii_spans_written(self) -> list[dict[str, Any]]:
        return list(self._pii_spans_written)

    # -- integrity --------------------------------------------------------

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute the hash chain over the physical log. Detects any edit."""
        prev = "genesis"
        for i, cp in enumerate(self._physical):
            if cp.prev_hash != prev:
                return False, f"chain break at physical record {i} (step {cp.step})"
            if cp.compute_hash() != cp.self_hash:
                return False, f"content tampered at physical record {i} (step {cp.step})"
            prev = cp.self_hash
        return True, "intact"

    def digest(self) -> str:
        return self._physical[-1].self_hash if self._physical else "empty"

    # -- persistence ------------------------------------------------------

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(cp.model_dump(mode="json")) for cp in self._physical)

    @staticmethod
    def rebuild_logical(physical: list[Checkpoint]) -> list[Checkpoint]:
        """Replay the physical log to reconstruct the live sequence (FR-7)."""
        logical: list[Checkpoint] = []
        for cp in physical:
            if cp.rollback_to is not None:
                logical = logical[: cp.rollback_to + 1]
            elif logical and logical[-1].step == cp.step and logical[-1].epoch == cp.epoch:
                logical[-1] = cp
            else:
                logical.append(cp)
        return logical

    def save(self, root: Path) -> Path:
        path = root / self.tenant / self.workload / f"{self.run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl())
        return path

    @classmethod
    def load(cls, path: Path, tenant: str) -> Ledger:
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        if not lines:
            raise ValueError(f"empty ledger at {path}")
        first = Checkpoint.model_validate_json(lines[0])
        if first.tenant != tenant:
            raise TenantIsolationError(
                f"tenant {tenant!r} may not read a ledger owned by {first.tenant!r}"
            )
        led = cls(first.run_id, first.tenant, first.workload, first.tier, redact=False)
        led._physical = [Checkpoint.model_validate_json(ln) for ln in lines]
        led._checkpoints = cls.rebuild_logical(led._physical)
        led._epoch = led._physical[-1].epoch
        return led

    def replay_identical(self) -> bool:
        """Round-trip through serialisation and confirm both the physical log
        and the reconstructed logical view come back identical. This is the
        Phase-1 exit criterion, and it is what makes localization hours later
        as accurate as localization inline."""
        rebuilt = [Checkpoint.model_validate_json(ln) for ln in self.to_jsonl().splitlines()]
        physical_ok = canonical_hash(
            [c.model_dump(mode="json") for c in rebuilt]
        ) == canonical_hash([c.model_dump(mode="json") for c in self._physical])
        logical_ok = canonical_hash(
            [c.model_dump(mode="json") for c in self.rebuild_logical(rebuilt)]
        ) == canonical_hash([c.model_dump(mode="json") for c in self._checkpoints])
        chain_ok, _ = self.verify_chain()
        return physical_ok and logical_ok and chain_ok


class LedgerStore:
    """Multi-tenant ledger store. There is no cross-tenant read path (FR-8)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, Ledger] = {}
        self.isolation_denials = 0

    def open(self, run_id: str, tenant: str, workload: str, tier: str) -> Ledger:
        led = Ledger(run_id, tenant, workload, tier)
        self._live[run_id] = led
        return led

    def get(self, run_id: str, tenant: str) -> Ledger:
        led = self._live.get(run_id)
        if led is not None:
            if led.tenant != tenant:
                self.isolation_denials += 1
                raise TenantIsolationError(
                    f"run {run_id!r} is owned by tenant {led.tenant!r}, not {tenant!r}"
                )
            return led
        matches = list(self.root.glob(f"{tenant}/*/{run_id}.jsonl"))
        if not matches:
            # Do not disclose whether the run exists under a different tenant.
            self.isolation_denials += 1
            raise TenantIsolationError(f"no run {run_id!r} readable by tenant {tenant!r}")
        return Ledger.load(matches[0], tenant)

    def list_runs(self, tenant: str, workload: str | None = None) -> list[str]:
        pattern = f"{tenant}/{workload or '*'}/*.jsonl"
        return sorted(p.stem for p in self.root.glob(pattern))

    def persist(self, ledger: Ledger) -> Path:
        return ledger.save(self.root)
