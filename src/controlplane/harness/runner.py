"""Scenario runner — executes one scenario end-to-end and records evidence.

Every run emits a manifest: scenario, seed, model, backend, framework, ablation
condition, pinned versions, and the resulting metrics. That manifest is what
`CLAIMS.md` points at, so that every number in the deck resolves to a run id
rather than to a memory of a demo that worked once.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..agent import GraphAgent, ReActAgent
from ..envs import ENVIRONMENTS
from ..envs.base import FaultInjector
from ..invariants import REGISTRY
from ..ledger import LedgerStore
from ..llm import METER, LLMClient
from ..policy import PolicyRegistry
from ..scenarios import Scenario
from ..supervisor import Supervisor

__all__ = ["RunConfig", "RunRecord", "run_scenario"]

RUNS_ROOT = Path("runs")


@dataclass
class RunConfig:
    """One cell of the experiment grid."""

    scenario: Scenario
    seed: int = 7
    backend: str = "primary"
    framework: str = "native-react"
    # Ablation switches (PRD 7.6). Each exists to earn exactly one claim.
    supervisor_on: bool = True
    recovery_on: bool = True
    adjudicator_on: bool = True
    localizer: str = "ours"  # ours | oracle | random
    label: str = ""

    @property
    def condition(self) -> str:
        if not self.supervisor_on:
            return "off"
        bits = ["on"]
        if not self.recovery_on:
            bits.append("detect_only")
        if not self.adjudicator_on:
            bits.append("deterministic_only")
        if self.localizer != "ours":
            bits.append(f"loc_{self.localizer}")
        return "+".join(bits)


@dataclass
class RunRecord:
    run_id: str
    scenario_id: str
    workload: str
    condition: str
    backend: str
    model: str
    framework: str
    seed: int
    task_success: bool
    success_detail: str
    steps: int
    stop_reason: str
    wall_ms: float
    tokens: int
    usd: float
    llm_calls: int
    ground_truth_steps: list[int] = field(default_factory=list)
    faults_applied: list[dict[str, Any]] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    supervisor: dict[str, Any] = field(default_factory=dict)
    harm: dict[str, Any] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    spans: int = 0
    ts: str = ""

    def save(self, root: Path = RUNS_ROOT) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.run_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2, default=str))
        return path


def run_scenario(
    cfg: RunConfig,
    store: LedgerStore | None = None,
    policy: PolicyRegistry | None = None,
) -> RunRecord:
    sc = cfg.scenario
    env_cls = ENVIRONMENTS[sc.env]
    env = env_cls(caller_department=sc.caller_department) if sc.env == "B" else env_cls()
    env.reset(cfg.seed)

    # A fresh registry per run: scenarios mutate tier config to simulate
    # tighter budgets, and that must not leak into the next run.
    policy = policy or PolicyRegistry()
    tier = policy.for_workload(sc.workload).tier
    if sc.force_async_classes:
        tier.inline_classes = tier.inline_classes - set(sc.force_async_classes)
        tier.async_classes = tier.async_classes | set(sc.force_async_classes)
    if sc.async_lag_override is not None:
        tier.async_lag_steps = sc.async_lag_override

    judge = LLMClient("judge") if (cfg.adjudicator_on and cfg.supervisor_on) else None
    sup = Supervisor(
        manifest=env.manifest,
        policy=policy,
        registry=REGISTRY,
        store=store or LedgerStore(RUNS_ROOT / "ledgers"),
        judge=judge,
        runs_root=RUNS_ROOT / "ledgers",
    )

    run_id = f"{sc.id}-{cfg.condition}-{cfg.backend}-s{cfg.seed}-{uuid.uuid4().hex[:6]}"
    run_sup = sup.start_run(
        sc.workload,
        goal=env.goal(),
        caller=getattr(env, "caller_department", ""),
        entitlements=getattr(env, "entitlements", set()),
        world=env,
        world_view=env.world_view(),
        run_id=run_id,
        enabled=cfg.supervisor_on,
    )
    _declare_run_constraints(run_sup, env, sc)

    injector = FaultInjector(sc.faults)
    llm = LLMClient(cfg.backend)
    agent_cls = GraphAgent if cfg.framework == "phase-graph" else ReActAgent
    agent = agent_cls(
        env=env,
        supervisor=run_sup,
        llm=llm,
        injector=injector,
        max_steps=sc.max_steps,
        recover=cfg.recovery_on and cfg.supervisor_on,
        seed=cfg.seed,
    )

    started = time.perf_counter()
    result = agent.run()
    wall_ms = (time.perf_counter() - started) * 1000

    ok, detail = env.verify_success()
    summary = run_sup.finish()

    return RunRecord(
        run_id=run_id,
        scenario_id=sc.id,
        workload=sc.workload,
        condition=cfg.condition,
        backend=cfg.backend,
        model=llm.model,
        framework=agent.framework,
        seed=cfg.seed,
        task_success=ok,
        success_detail=detail,
        steps=result.steps,
        stop_reason=result.stop_reason,
        wall_ms=wall_ms,
        tokens=result.tokens,
        usd=result.usd,
        llm_calls=result.llm_calls,
        ground_truth_steps=injector.ground_truth_steps,
        faults_applied=injector.applied,
        incidents=summary["incidents"],
        supervisor=summary,
        harm=env.harm_report(),
        transcript=result.transcript,
        spans=len(run_sup.spans.spans),
        ts=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _declare_run_constraints(run_sup, env, sc: Scenario) -> None:
    """Constraints the run commits to up front. Invariants read these."""
    if sc.workload == "C":
        from ..envs.env_c_underwriting import CURRENT_QUARTER

        run_sup.declare_constraint(f"data_as_of == {CURRENT_QUARTER}")
        run_sup.declare_constraint("all figures exposure-weighted before pricing")
    if sc.workload == "A":
        from ..types import canonical_hash

        # Declared as hashes: the allowlist is itself a list of phone numbers,
        # and putting personal data into an audit record to guard against
        # leaking personal data would be an odd way to run a privacy control.
        for pol in env.state["policies"].values():
            run_sup.declare_constraint(
                f"egress_allow: {canonical_hash(str(pol['contacts']['phone']))}"
            )
        run_sup.declare_constraint("no financial action before entity resolution")


def meter_summary() -> dict[str, Any]:
    return METER.summary()
