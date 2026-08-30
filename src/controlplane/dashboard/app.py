"""Dashboard API.

Reads only what is already on disk — run records and ledgers — and computes
nothing it cannot show the provenance of. That constraint is deliberate: if the
dashboard could derive its own numbers, the demo and the evidence would be able
to disagree, and the demo would win.

Every endpoint takes a tenant and refuses to cross it, so the isolation
property holds at the presentation layer too rather than only in the store.
"""

from __future__ import annotations

import ast
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..invariants import REGISTRY
from ..ledger import Ledger, TenantIsolationError
from ..policy import PolicyRegistry
from ..scenarios import FAULTS, GOLDEN_SCENARIOS

# Four levels up from this file is the repository root. Resolving from the
# module rather than the process CWD is what lets the dashboard run under a
# serverless host, which sets a working directory of its own choosing.
_REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_ROOT = Path(os.getenv("CONTROLPLANE_RUNS") or _REPO_ROOT / "runs")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="ControlPlane", docs_url="/api/docs")


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


class _AsRecord:
    """Attribute view over a run dict, so the scorer's helpers work unchanged."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.__dict__.update(data)


def _run_files() -> list[Path]:
    # experiment.json and any other aggregate the harness writes live in the
    # same directory. Match on shape rather than filename, so a new aggregate
    # cannot break the feed by being added later.
    return sorted(RUNS_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _records(tenant: str) -> list[dict[str, Any]]:
    out = []
    for path in _run_files():
        rec = _load(path)
        if rec is None or "run_id" not in rec:
            continue
        if rec.get("supervisor", {}).get("tenant", "meridian") != tenant:
            continue
        out.append(rec)
    return out


def _record(run_id: str, tenant: str) -> dict[str, Any]:
    rec = _load(RUNS_ROOT / f"{run_id}.json")
    if rec is None or "run_id" not in rec:
        raise HTTPException(404, f"no run {run_id!r}")
    if rec.get("supervisor", {}).get("tenant", "meridian") != tenant:
        # Same response as "does not exist": a distinguishable error is an
        # existence oracle for another tenant's run ids.
        raise HTTPException(404, f"no run {run_id!r}")
    return rec


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@app.get("/api/runs")
def list_runs(tenant: str = Query("meridian")) -> list[dict[str, Any]]:
    """Summary row per run — enough for the feed, nothing heavier."""
    rows = []
    for rec in _records(tenant):
        harm = rec.get("harm") or {}
        rows.append(
            {
                "run_id": rec["run_id"],
                "scenario_id": rec["scenario_id"],
                "workload": rec["workload"],
                "condition": rec["condition"],
                "backend": rec["backend"],
                "model": rec["model"],
                "seed": rec["seed"],
                "steps": rec["steps"],
                "task_success": rec["task_success"],
                "success_detail": rec["success_detail"],
                "stop_reason": rec["stop_reason"],
                "incidents": len(rec.get("incidents") or []),
                "harm_occurred": bool(harm.get("harm_occurred")),
                "fault_steps": rec.get("ground_truth_steps") or [],
                "inline_ms_p95": (rec.get("supervisor", {}).get("metrics") or {}).get(
                    "inline_ms_p95", 0.0
                ),
                "usd": rec.get("usd", 0.0),
                "tokens": rec.get("tokens", 0),
                "ts": rec.get("ts", ""),
            }
        )
    return rows


@app.get("/api/incidents")
def list_incidents(tenant: str = Query("meridian")) -> list[dict[str, Any]]:
    """The live feed. One row per incident, newest run first."""
    from ..harness.metrics import _expected_invariants_by_step

    feed = []
    for rec in _records(tenant):
        faults = sorted(rec.get("ground_truth_steps") or [])
        expected_by_step = _expected_invariants_by_step(_AsRecord(rec))
        for inc in rec.get("incidents") or []:
            violation = inc["violation"]
            loc = inc.get("localization") or {}
            detected_at = violation["detected_at_step"]
            inv_id = violation["invariant_id"]
            origins = [
                s
                for s in faults
                if s <= detected_at and inv_id in expected_by_step.get(s, frozenset())
            ]
            expected_L = (max(origins) - 1) if origins else None
            reported_L = loc.get("last_good_step")
            spontaneous = expected_L is None and any(s <= detected_at for s in faults)

            feed.append(
                {
                    "incident_id": inc["incident_id"],
                    "run_id": rec["run_id"],
                    "scenario_id": rec["scenario_id"],
                    "workload": rec["workload"],
                    "condition": rec["condition"],
                    "invariant_id": violation["invariant_id"],
                    "invariant_class": violation["invariant_class"],
                    "severity": violation["severity"],
                    "detected_at_step": detected_at,
                    "detected_by": violation["detected_by"],
                    "detail": violation["detail"],
                    "last_good_step": reported_L,
                    "expected_last_good_step": expected_L,
                    # Scored only where an injected fault can explain this
                    # particular check firing. Everything else is either a real
                    # defect the agent produced itself, or a false alarm, and
                    # the two are shown apart.
                    "localization_error": (
                        None
                        if expected_L is None or reported_L is None
                        else abs(reported_L - expected_L)
                    ),
                    "spontaneous": spontaneous,
                    "false_alarm": expected_L is None and not spontaneous,
                    "delta_detect": None if expected_L is None else detected_at - (expected_L + 1),
                    "method": loc.get("method"),
                    "quality": loc.get("quality"),
                    "evaluations": loc.get("evaluations"),
                    "wall_ms": loc.get("wall_ms"),
                    "rca": loc.get("rca") or {},
                    "recovery": inc.get("recovery") or {},
                    "ts": rec.get("ts", ""),
                }
            )
    return feed


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str, tenant: str = Query("meridian")) -> dict[str, Any]:
    return _record(run_id, tenant)


@app.get("/api/runs/{run_id}/timeline")
def run_timeline(run_id: str, tenant: str = Query("meridian")) -> dict[str, Any]:
    """Step-by-step view, annotated with faults, blocks and rollbacks.

    Built from the ledger's *physical* log where available, so rolled-back
    attempts stay visible. A timeline that quietly hid the abandoned branch
    would misrepresent what the agent actually did.
    """
    rec = _record(run_id, tenant)
    path = RUNS_ROOT / "ledgers" / tenant / rec["workload"] / f"{run_id}.jsonl"

    faults = {f.get("step"): f for f in (rec.get("faults_applied") or [])}
    incidents_by_step: dict[int, list[dict]] = {}
    for inc in rec.get("incidents") or []:
        incidents_by_step.setdefault(inc["violation"]["detected_at_step"], []).append(inc)

    steps: list[dict[str, Any]] = []
    if path.exists():
        try:
            ledger = Ledger.load(path, tenant)
        except TenantIsolationError:
            raise HTTPException(404, f"no run {run_id!r}") from None
        live = {(cp.step, cp.epoch) for cp in ledger.checkpoints}
        for cp in ledger.physical_log:
            call = cp.pending_call
            steps.append(
                {
                    "step": cp.step,
                    "epoch": cp.epoch,
                    "tool": call.tool if call else None,
                    "args": call.args if call else {},
                    "reversibility": call.reversibility.value if call else None,
                    "source_class": (
                        cp.result.source_class.value if cp.result else cp.source_class.value
                    ),
                    "result": cp.result.preview if cp.result else None,
                    "narrative": cp.narrative,
                    "blocked": cp.blocked,
                    "rollback_to": cp.rollback_to,
                    "bindings": {k: v.value for k, v in cp.commitments.bindings.items()},
                    "budget": cp.budget.model_dump(),
                    "superseded": cp.rollback_to is None and (cp.step, cp.epoch) not in live,
                    "fault": faults.get(cp.step),
                    "incidents": incidents_by_step.get(cp.step, []),
                    "self_hash": cp.self_hash[:12],
                }
            )
        integrity = {
            "chain_intact": ledger.verify_chain()[0],
            "chain_detail": ledger.verify_chain()[1],
            "replay_identical": ledger.replay_identical(),
            "physical_records": len(ledger.physical_log),
            "logical_steps": len(ledger),
            "epoch": ledger.epoch,
            "pii_spans_redacted": len(ledger.pii_spans_written),
        }
    else:
        # Fall back to the transcript so a run whose ledger was pruned still
        # renders, clearly marked as the weaker source.
        for entry in rec.get("transcript") or []:
            steps.append(
                {
                    "step": entry.get("step"),
                    "epoch": 0,
                    "tool": entry.get("tool"),
                    "args": entry.get("args") or {},
                    "source_class": "unlabelled",
                    "result": entry.get("result"),
                    "narrative": entry.get("narrative", ""),
                    "blocked": False,
                    "rollback_to": None,
                    "bindings": {},
                    "superseded": False,
                    "fault": faults.get(entry.get("step")),
                    "incidents": incidents_by_step.get(entry.get("step"), []),
                }
            )
        integrity = {"chain_intact": None, "note": "ledger not on disk; showing transcript"}

    return {
        "run_id": run_id,
        "scenario_id": rec["scenario_id"],
        "scenario": _scenario_blurb(rec["scenario_id"]),
        "workload": rec["workload"],
        "condition": rec["condition"],
        "model": rec["model"],
        "task_success": rec["task_success"],
        "success_detail": rec["success_detail"],
        "harm": rec.get("harm") or {},
        "fault_steps": rec.get("ground_truth_steps") or [],
        "supervisor": rec.get("supervisor", {}).get("metrics", {}),
        "integrity": integrity,
        "steps": steps,
    }


@app.get("/api/runs/{run_id}/localization/{incident_id}")
def localization_detail(
    run_id: str, incident_id: str, tenant: str = Query("meridian")
) -> dict[str, Any]:
    """Replay the binary search so the probe sequence is visible, not asserted.

    The point of the exact path is that it is checkable. Showing which prefixes
    were evaluated, and what each returned, is the difference between a claim
    and a demonstration.
    """
    rec = _record(run_id, tenant)
    incident = next(
        (i for i in rec.get("incidents") or [] if i["incident_id"] == incident_id), None
    )
    if incident is None:
        raise HTTPException(404, f"no incident {incident_id!r}")

    violation = incident["violation"]
    loc = incident.get("localization") or {}
    probes: list[dict[str, Any]] = []

    path = RUNS_ROOT / "ledgers" / tenant / rec["workload"] / f"{run_id}.jsonl"
    inv_id = violation["invariant_id"]
    if path.exists() and inv_id in REGISTRY:
        invariant = REGISTRY.get(inv_id)
        ledger = Ledger.load(path, tenant)
        ctx = _replay_context(rec)
        if invariant.monotone:
            lo, hi = -1, min(violation["detected_at_step"], ledger.last_step)
            probes.append(
                {
                    "prefix": hi,
                    "holds": invariant.evaluate(ledger.prefix(hi), ctx).holds,
                    "role": "confirm right endpoint fails",
                }
            )
            while hi - lo > 1:
                mid = (lo + hi) // 2
                holds = invariant.evaluate(ledger.prefix(mid), ctx).holds
                probes.append(
                    {
                        "prefix": mid,
                        "holds": holds,
                        "role": "search left half" if not holds else "search right half",
                        "lo": lo,
                        "hi": hi,
                    }
                )
                if holds:
                    lo = mid
                else:
                    hi = mid

    faults = sorted(rec.get("ground_truth_steps") or [])
    origins = [s for s in faults if s <= violation["detected_at_step"]]
    return {
        "incident_id": incident_id,
        "run_id": run_id,
        "violation": violation,
        "localization": loc,
        "recovery": incident.get("recovery") or {},
        "monotone": REGISTRY.get(inv_id).monotone if inv_id in REGISTRY else None,
        "probes": probes,
        "expected_last_good_step": (max(origins) - 1) if origins else None,
        "steps_searched": min(violation["detected_at_step"] + 1, rec["steps"]),
        "linear_scan_would_cost": min(violation["detected_at_step"] + 1, rec["steps"]),
    }


def _replay_context(rec: dict[str, Any]):
    """Rebuild the evaluation context a run used, for replay in the UI."""
    from ..envs import ENVIRONMENTS
    from ..invariants import EvalContext

    env_cls = ENVIRONMENTS[rec["workload"]]
    env = env_cls()
    env.reset(rec.get("seed", 7))
    policy = PolicyRegistry().for_workload(rec["workload"])
    return EvalContext(
        manifest=env.manifest,
        workload=rec["workload"],
        tier=policy.tier.name,
        caller=getattr(env, "caller_department", ""),
        entitlements=getattr(env, "entitlements", set()),
        world_view=env.world_view(),
        config={"budget": policy.tier.budget},
    )


@app.get("/api/guards")
def guard_liveness() -> dict[str, Any]:
    """Attestation: which guards are active, and which are proven load-bearing.

    'Proven' means the sabotage suite has a case showing the fault goes through
    when that guard is removed. A guard with no such case is listed as
    unvalidated rather than assumed healthy — an inactive check and a check
    with nothing to catch look identical from the outside.
    """
    validated = _sabotage_validated()
    policy = PolicyRegistry()
    rows = []
    for inv in REGISTRY.all():
        tiers = {}
        for wid, wp in policy.workloads.items():
            if not inv.applies(wid):
                continue
            cls = inv.invariant_class.value
            tiers[wid] = (
                "inline"
                if wp.tier.wants_inline(cls)
                else ("async" if wp.tier.wants_async(cls) else "off")
            )
        rows.append(
            {
                **inv.spec(),
                "sabotage_validated": inv.id in validated,
                "placement": tiers,
            }
        )
    return {
        "active": len(rows),
        "sabotage_validated": sum(1 for r in rows if r["sabotage_validated"]),
        "unvalidated": sorted(r["id"] for r in rows if not r["sabotage_validated"]),
        "guards": rows,
    }


@lru_cache(maxsize=1)
def _sabotage_validated() -> frozenset[str]:
    """Read the guards the sabotage suite actually covers, from the suite itself.

    Parsed from the test module rather than duplicated here, so the attestation
    cannot drift from the tests it attests to. Read as source with `ast` rather
    than imported, because importing pulls in pytest and the fault fixtures,
    and this has to work on a deployment that installs neither. A missing or
    unreadable suite yields an empty set, which reports every guard as
    unvalidated — the direction an attestation should fail in.
    """
    path = _REPO_ROOT / "tests" / "test_sabotage.py"
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return frozenset()

    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(isinstance(t, ast.Name) and t.id == "CASES" for t in targets):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        found = set()
        for case in node.value.elts:
            # Each case is (title, fixture, guard_id, overlaps); the guard id
            # is the third element and is always a literal string.
            if isinstance(case, (ast.Tuple, ast.List)) and len(case.elts) >= 3:
                guard = case.elts[2]
                if isinstance(guard, ast.Constant) and isinstance(guard.value, str):
                    found.add(guard.value)
        return frozenset(found)
    return frozenset()


_TIER_BLURBS = {
    "interactive-external": (
        "Customer-facing work with a tight clock. Most checks run before the "
        "action; a few deeper ones trail in the background."
    ),
    "interactive-internal": (
        "Internal tools where entitlement boundaries matter. Slightly more "
        "time for instant checks than the external tier."
    ),
    "batch-analytical": (
        "Long-running analytical work. The clock is loose enough that nearly "
        "every check can run before the next step."
    ),
}


def _policy_payload() -> dict[str, Any]:
    policy = PolicyRegistry()
    return {
        "tiers": [
            {
                "name": t.name,
                "id": t.name,
                "description": _TIER_BLURBS.get(t.name, ""),
                "inline_budget_p95_ms": t.inline_budget_p95_ms,
                "inline_classes": sorted(t.inline_classes),
                "async_classes": sorted(t.async_classes),
                "async_lag_steps": t.async_lag_steps,
                "irreversible_policy": t.irreversible_policy,
                "on_supervisor_unavailable": t.on_supervisor_unavailable,
                "active_invariants": sorted(t.inline_classes | t.async_classes),
            }
            for t in policy.tiers.values()
        ],
        "workloads": [
            {
                "id": w.workload,
                "workload": w.workload,
                "name": w.name,
                "tenant": w.tenant,
                "tier": w.tier.name,
            }
            for w in policy.workloads.values()
        ],
    }


@app.get("/api/policy")
def policy_view() -> dict[str, Any]:
    """Risk profiles and which workload uses which profile."""
    return _policy_payload()


@app.get("/api/catalogue")
def catalogue() -> dict[str, Any]:
    policy = _policy_payload()
    return {
        "scenarios": [
            {
                "id": sc.id,
                "title": sc.title,
                "env": sc.env,
                "workload": sc.workload,
                "narrative": sc.narrative.strip(),
                "faults": [f.fault_id for f in sc.faults],
                "clean": sc.clean,
                "expects_block": sc.expects_block,
                "expects_escalation": sc.expects_escalation,
                "max_steps": sc.max_steps,
            }
            for sc in GOLDEN_SCENARIOS.values()
        ],
        "faults": [
            {
                "id": f.fault_id,
                "title": f.title,
                "envs": list(f.envs),
                "held_out": f.held_out,
            }
            for f in FAULTS.values()
        ],
        "tiers": policy["tiers"],
        "workloads": policy["workloads"],
    }


@app.get("/api/experiment")
def experiment() -> JSONResponse:
    path = RUNS_ROOT / "experiment.json"
    if not path.exists():
        return JSONResponse({"note": "no experiment on disk; run `controlplane ladder`"}, 404)
    return JSONResponse(json.loads(path.read_text()))


def _scenario_blurb(scenario_id: str) -> dict[str, Any]:
    sc = GOLDEN_SCENARIOS.get(scenario_id)
    if sc is None:
        return {}
    return {"title": sc.title, "narrative": sc.narrative.strip()}


@app.get("/")
def index() -> FileResponse:
    # HTML must not be cached — otherwise a deploy that only changes JS/CSS
    # can leave visitors on a stale shell that still points at old asset URLs.
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-store"},
    )


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
