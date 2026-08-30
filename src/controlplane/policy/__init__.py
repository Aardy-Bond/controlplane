"""Policy layer: named, versioned tiers assigned per workload (PRD 5.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["PolicyTier", "WorkloadPolicy", "PolicyRegistry", "default_registry"]

_DEFAULT_PATH = Path(__file__).with_name("tiers.yaml")


@dataclass
class PolicyTier:
    name: str
    inline_budget_p95_ms: float
    inline_classes: set[str]
    async_classes: set[str]
    irreversible_policy: str
    llm_adjudication: str
    max_recovery_attempts: int
    on_supervisor_unavailable: str
    escalation_sla_s: int
    budget: dict[str, Any] = field(default_factory=dict)
    # How many steps the deep path lags the agent. This is the honest cost of
    # demoting a check to async: the agent keeps acting while the check runs.
    # 0 means the deep path keeps up; a high value means the only thing that
    # catches the fault is the pre-commit gate on an irreversible action.
    async_lag_steps: int = 1
    version: str = "v0"

    def wants_inline(self, invariant_class: str) -> bool:
        return invariant_class in self.inline_classes

    def wants_async(self, invariant_class: str) -> bool:
        return invariant_class in self.async_classes


@dataclass
class WorkloadPolicy:
    workload: str
    name: str
    tenant: str
    tier: PolicyTier


class PolicyRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or _DEFAULT_PATH)
        self.tiers: dict[str, PolicyTier] = {}
        self.workloads: dict[str, WorkloadPolicy] = {}
        self.reload()

    def reload(self) -> None:
        """Re-read config from disk. Config changes need no redeploy (NFR-8)."""
        raw = yaml.safe_load(self.path.read_text()) or {}
        self.tiers = {
            name: PolicyTier(
                name=name,
                inline_budget_p95_ms=float(body["inline_budget_p95_ms"]),
                inline_classes=set(body.get("inline_classes") or []),
                async_classes=set(body.get("async_classes") or []),
                irreversible_policy=body.get("irreversible_policy", "verify_before_commit"),
                llm_adjudication=body.get("llm_adjudication", "on_flag_only"),
                max_recovery_attempts=int(body.get("max_recovery_attempts", 2)),
                on_supervisor_unavailable=body.get("on_supervisor_unavailable", "fail_open"),
                escalation_sla_s=int(body.get("escalation_sla_s", 300)),
                budget=body.get("budget") or {},
                async_lag_steps=int(body.get("async_lag_steps", 1)),
            )
            for name, body in (raw.get("tiers") or {}).items()
        }
        self.workloads = {
            wid: WorkloadPolicy(
                workload=wid,
                name=body.get("name", wid),
                tenant=body.get("tenant", "default"),
                tier=self.tiers[body["tier"]],
            )
            for wid, body in (raw.get("workloads") or {}).items()
        }

    def for_workload(self, workload: str) -> WorkloadPolicy:
        return self.workloads[workload]


def default_registry() -> PolicyRegistry:
    return PolicyRegistry()
