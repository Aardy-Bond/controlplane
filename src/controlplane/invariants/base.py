"""Invariant protocol and registry (PRD 5.3).

An invariant is a **pure function of a ledger prefix** (FR-12). That constraint
is not stylistic — it is what makes LOCALIZE possible. If `holds(prefix(n))`
can be evaluated for any n without touching live tool state, then for a
monotone invariant the ledger becomes a sorted array and finding the last good
step is a binary search rather than an LLM reading the whole trace.

Each invariant declares:

* ``monotone`` — once violated, violated for every longer prefix. Only monotone
  invariants get the exact O(log N) path; the rest fall back and are labelled
  ``estimated``. Never assumed, always declared, and checked by a test.
* ``inline_cost_class`` — drives automatic demotion to async when a tier's
  blocking budget cannot afford it (FR-14).
* ``severity`` — whether a violation blocks, warns, or is recorded.
* ``applies_to`` — which workloads. One library, three workloads.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..manifest import ToolManifest
from ..types import Checkpoint, CostClass, InvariantClass, Severity, Verdict

__all__ = ["EvalContext", "Invariant", "InvariantRegistry", "REGISTRY", "register", "timed_eval"]


@dataclass
class EvalContext:
    """Everything an invariant may read besides the prefix itself.

    Deliberately narrow. No live tool state, no model internals — otherwise
    replay-based localization would not be sound.
    """

    manifest: ToolManifest
    workload: str = ""
    tier: str = ""
    goal: str = ""
    # Caller identity and entitlements, for workload B.
    caller: str = ""
    entitlements: set[str] = field(default_factory=set)
    # Static world snapshot used only by declarative preconditions that need it.
    world_view: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    # Attributes that must not influence which actions are taken (G4).
    protected_attributes: set[str] = field(
        default_factory=lambda: {"gender", "religion", "caste", "ethnicity", "marital_status"}
    )


class Invariant(ABC):
    id: str = ""
    invariant_class: InvariantClass = InvariantClass.SCHEMA
    monotone: bool = True
    inline_cost_class: CostClass = CostClass.MICROS
    severity: Severity = Severity.BLOCK
    applies_to: frozenset[str] = frozenset({"*"})
    description: str = ""
    # What the violation blames: the call's *arguments*, or the *result* that
    # came back. Recovery needs this and gets it wrong without it. When the
    # arguments were wrong, retrying the same call unchanged repeats the fault,
    # so the plan is forbidden. When the result was wrong — a stale snapshot, a
    # 502 body, a truncated page — the same call is exactly what should be
    # retried, and forbidding it strands the agent with no way to get the data
    # it needs.
    implicates: str = "arguments"

    def applies(self, workload: str) -> bool:
        return "*" in self.applies_to or workload in self.applies_to

    @abstractmethod
    def evaluate(self, prefix: list[Checkpoint], ctx: EvalContext) -> Verdict:
        """Return whether the invariant holds over ``prefix``.

        Implementations must read nothing outside ``prefix`` and ``ctx``.
        """

    def spec(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.invariant_class.value,
            "monotone": self.monotone,
            "inline_cost_class": self.inline_cost_class.value,
            "severity": self.severity.value,
            "applies_to": sorted(self.applies_to),
            "implicates": self.implicates,
            "description": self.description,
        }


class InvariantRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Invariant] = {}

    def add(self, inv: Invariant) -> Invariant:
        if not inv.id:
            raise ValueError(f"{type(inv).__name__} has no id")
        self._items[inv.id] = inv
        return inv

    def get(self, inv_id: str) -> Invariant:
        return self._items[inv_id]

    def all(self) -> list[Invariant]:
        return list(self._items.values())

    def for_workload(self, workload: str, classes: set[str] | None = None) -> list[Invariant]:
        out = [i for i in self._items.values() if i.applies(workload)]
        if classes is not None:
            out = [i for i in out if i.invariant_class.value in classes]
        return out

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, inv_id: str) -> bool:
        return inv_id in self._items


REGISTRY = InvariantRegistry()


def register(cls: type[Invariant]) -> type[Invariant]:
    REGISTRY.add(cls())
    return cls


def timed_eval(inv: Invariant, prefix: list[Checkpoint], ctx: EvalContext) -> tuple[Verdict, float]:
    started = time.perf_counter()
    try:
        verdict = inv.evaluate(prefix, ctx)
    except Exception as exc:  # noqa: BLE001
        # A crashing invariant must not take the agent down, but it also must
        # not silently pass. An unevaluable guard is reported as a violation
        # of its own liveness.
        verdict = Verdict.fail(
            inv.id, f"invariant raised {type(exc).__name__}: {exc}", subject="__invariant__"
        )
    return verdict, (time.perf_counter() - started) * 1000
