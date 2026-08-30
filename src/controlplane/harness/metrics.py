"""Metrics and the statistical protocol (PRD 7.4, 7.8).

The metrics that matter here are not "did it catch the bug". They are:

* **Δdetect** — how many steps elapsed between the fault and its detection. This
  is the number that separates prevention from archaeology.
* **Localization error** — |reported L − (fault step − 1)|. Exact-step and
  within-±1 rates are reported separately because a locator that is usually
  one step off is still useful, and pretending otherwise inflates the headline.
* **Recoverability@L** — did rolling back to the reported L actually produce a
  correct outcome. A locator that is right but unrecoverable has not helped.
* **Intervention regret and false alarms** — measured on clean runs, where every
  intervention is by definition a false positive. Reported whether or not it
  flatters us.

Every headline number carries a bootstrap 95% CI. No point estimate ships alone,
because with n≈30 the difference between 70% and 85% is frequently noise.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ScoredRun",
    "bootstrap_ci",
    "mcnemar",
    "score_run",
    "aggregate",
]


@dataclass
class ScoredRun:
    run_id: str
    scenario_id: str
    workload: str
    condition: str
    backend: str
    framework: str
    seed: int
    clean: bool
    task_success: bool
    steps: int
    tokens: int
    usd: float
    wall_ms: float
    inline_ms_p95: float
    detections: int
    fault_steps: list[int] = field(default_factory=list)
    delta_detect: list[int] = field(default_factory=list)
    localization_error: list[int] = field(default_factory=list)
    localization_exact: list[bool] = field(default_factory=list)
    localization_quality: list[str] = field(default_factory=list)
    localization_evals: list[int] = field(default_factory=list)
    localization_ms: list[float] = field(default_factory=list)
    recovered: int = 0
    escalated: int = 0
    false_alarms: int = 0
    # A real defect the agent introduced by itself, caught by a guard, but not
    # traceable to anything the harness injected. Not a false alarm — there is
    # genuinely something wrong — but there is no injected ground truth to score
    # localization against, so it is counted separately rather than folded into
    # either bucket.
    spontaneous: int = 0
    interventions: int = 0
    irreversible_blocked: int = 0
    real_harm: dict[str, Any] = field(default_factory=dict)
    pii_egress: int = 0
    chain_intact: bool = True
    replay_identical: bool = True

    @property
    def intervention_regret(self) -> float:
        """Fraction of interventions that were not justified by a real fault."""
        if not self.interventions:
            return 0.0
        return self.false_alarms / self.interventions

    @property
    def false_alarms_per_100_steps(self) -> float:
        return (self.false_alarms / self.steps * 100.0) if self.steps else 0.0


def score_run(record: Any, clean: bool) -> ScoredRun:
    """Score one RunRecord against its injected ground truth.

    Three buckets, kept apart on purpose:

    * **attributable** — a fault was injected at or before the detection step
      *and* the invariant that fired is one that fault can trip. Only these get
      a localization error, because only these have a known true origin.
    * **spontaneous** — a guard caught a real defect the agent produced on its
      own. Scoring these against the injected fault step would be measuring the
      wrong thing: on a 57-step run, a malformed argument at step 40 has nothing
      to do with a stale read injected at step 11, and the localizer is right to
      blame step 39.
    * **false alarm** — no fault at all preceded the alarm.

    Folding the middle bucket into either of the others is the easy mistake.
    Counting them as attributable understates localization; counting them as
    false alarms overstates the false-alarm rate. Both are wrong, in opposite
    directions, so they are reported separately.
    """
    fault_steps = sorted(record.ground_truth_steps)
    expected_by_step = _expected_invariants_by_step(record)
    scored = ScoredRun(
        run_id=record.run_id,
        scenario_id=record.scenario_id,
        workload=record.workload,
        condition=record.condition,
        backend=record.backend,
        framework=record.framework,
        seed=record.seed,
        clean=clean,
        task_success=record.task_success,
        steps=record.steps,
        tokens=record.tokens,
        usd=record.usd,
        wall_ms=record.wall_ms,
        inline_ms_p95=record.supervisor.get("metrics", {}).get("inline_ms_p95", 0.0),
        detections=len(record.incidents),
        fault_steps=fault_steps,
        real_harm=record.harm,
        chain_intact=record.supervisor.get("chain_intact", True),
        replay_identical=record.supervisor.get("replay_identical", True),
        irreversible_blocked=record.supervisor.get("metrics", {}).get("irreversible_blocked", 0),
    )

    for inc in record.incidents:
        scored.interventions += 1
        loc = inc.get("localization") or {}
        detected_at = inc["violation"]["detected_at_step"]
        inv_id = inc["violation"]["invariant_id"]

        # Attribute to the most recent fault at or before the alarm that could
        # actually have produced *this* invariant firing.
        candidates = [
            s
            for s in fault_steps
            if s <= detected_at and inv_id in expected_by_step.get(s, frozenset())
        ]
        if not candidates:
            if any(s <= detected_at for s in fault_steps):
                scored.spontaneous += 1
            else:
                scored.false_alarms += 1
            continue

        origin = max(candidates)
        scored.delta_detect.append(detected_at - origin)
        reported_L = loc.get("last_good_step", -1)
        expected_L = origin - 1
        err = abs(reported_L - expected_L)
        scored.localization_error.append(err)
        scored.localization_exact.append(err == 0)
        scored.localization_quality.append(loc.get("quality", "unknown"))
        scored.localization_evals.append(loc.get("evaluations", 0))
        scored.localization_ms.append(loc.get("wall_ms", 0.0))

        rec = inc.get("recovery") or {}
        if rec.get("succeeded"):
            scored.recovered += 1
        if rec.get("escalated"):
            scored.escalated += 1

    return scored


def _expected_invariants_by_step(record: Any) -> dict[int, frozenset[str]]:
    """Map each applied fault's step to the checks that fault can trip."""
    from ..scenarios import FAULTS

    out: dict[int, set[str]] = {}
    for applied in record.faults_applied or []:
        step = applied.get("step")
        spec = FAULTS.get(applied.get("fault_id", ""))
        if step is None or spec is None:
            continue
        out.setdefault(step, set()).update(spec.expected_invariants)
    return {k: frozenset(v) for k, v in out.items()}


def bootstrap_ci(
    values: list[float], iterations: int = 4000, alpha: float = 0.05, seed: int = 11
) -> tuple[float, float]:
    """Percentile bootstrap CI. Returns (lo, hi); degenerate input returns the point."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        means.append(statistics.fmean(rng.choices(values, k=n)))
    means.sort()
    lo = means[int((alpha / 2) * iterations)]
    hi = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return (round(lo, 4), round(hi, 4))


def mcnemar(paired: list[tuple[bool, bool]]) -> dict[str, Any]:
    """Exact McNemar on paired success/failure flips (off, on).

    Paired design because the same task and seed run under both conditions has
    far less variance than two independent samples, which buys real power at
    n≈30 instead of pretending to have it.
    """
    b = sum(1 for off, on in paired if off and not on)  # supervisor hurt
    c = sum(1 for off, on in paired if not off and on)  # supervisor helped
    n = b + c
    if n == 0:
        return {"b_hurt": 0, "c_helped": 0, "p_value": 1.0, "significant": False}
    # Exact binomial two-sided p under H0: p = 0.5
    from math import comb

    tail = sum(comb(n, k) for k in range(0, min(b, c) + 1)) / (2**n)
    p = min(1.0, 2 * tail)
    return {
        "b_hurt": b,
        "c_helped": c,
        "p_value": round(p, 5),
        "significant": p < 0.05,
        "direction": "helped" if c > b else ("hurt" if b > c else "neutral"),
    }


def _flat(runs: list[ScoredRun], attr: str) -> list[float]:
    out: list[float] = []
    for r in runs:
        out.extend(float(v) for v in getattr(r, attr))
    return out


def _pct(values: list[bool]) -> float:
    return round(100.0 * sum(values) / len(values), 2) if values else 0.0


def aggregate(runs: list[ScoredRun]) -> dict[str, Any]:
    """Roll a set of runs into the numbers the acceptance gates are stated in."""
    if not runs:
        return {"n": 0}

    errs = _flat(runs, "localization_error")
    exact = [bool(v) for r in runs for v in r.localization_exact]
    within1 = [e <= 1 for e in errs]
    dd = _flat(runs, "delta_detect")
    inline = [r.inline_ms_p95 for r in runs]
    success = [r.task_success for r in runs]
    clean_runs = [r for r in runs if r.clean]

    total_interventions = sum(r.interventions for r in runs)
    total_false = sum(r.false_alarms for r in runs)
    total_spontaneous = sum(r.spontaneous for r in runs)
    total_steps = sum(r.steps for r in runs)
    detections = sum(r.detections for r in runs)
    recovered = sum(r.recovered for r in runs)

    out: dict[str, Any] = {
        "n": len(runs),
        "task_success_pct": _pct(success),
        "task_success_ci": bootstrap_ci([1.0 if s else 0.0 for s in success]),
        "detections": detections,
        "attributable_detections": len(errs),
        "spontaneous_detections": total_spontaneous,
        "localization": {
            "n": len(errs),
            "exact_step_pct": _pct(exact),
            "exact_step_ci": bootstrap_ci([1.0 if e else 0.0 for e in exact]),
            "within_1_pct": _pct(within1),
            "within_1_ci": bootstrap_ci([1.0 if w else 0.0 for w in within1]),
            "mean_abs_error": round(statistics.fmean(errs), 3) if errs else None,
            "mean_evaluations": round(statistics.fmean(_flat(runs, "localization_evals")), 2)
            if errs
            else None,
            "mean_wall_ms": round(statistics.fmean(_flat(runs, "localization_ms")), 4)
            if errs
            else None,
            "max_wall_ms": round(max(_flat(runs, "localization_ms")), 4) if errs else None,
            "quality_mix": _counts([q for r in runs for q in r.localization_quality]),
        },
        "delta_detect": {
            "median": round(statistics.median(dd), 2) if dd else None,
            "p90": round(_percentile(dd, 90), 2) if dd else None,
            "max": round(max(dd), 2) if dd else None,
            "ci": bootstrap_ci(dd),
        },
        "recoverability_at_L_pct": round(100.0 * recovered / detections, 2) if detections else None,
        "escalations": sum(r.escalated for r in runs),
        "intervention_regret_pct": round(100.0 * total_false / total_interventions, 2)
        if total_interventions
        else 0.0,
        "false_alarms_per_100_steps": round(100.0 * total_false / total_steps, 3)
        if total_steps
        else 0.0,
        "inline_ms_p95": round(_percentile(inline, 95), 4) if inline else 0.0,
        "cost": {
            "mean_tokens": round(statistics.fmean([r.tokens for r in runs]), 1),
            "mean_usd": round(statistics.fmean([r.usd for r in runs]), 6),
            "total_usd": round(sum(r.usd for r in runs), 6),
        },
        "integrity": {
            "chain_intact_all": all(r.chain_intact for r in runs),
            "replay_identical_all": all(r.replay_identical for r in runs),
        },
    }

    if clean_runs:
        cf = sum(r.false_alarms for r in clean_runs)
        cs = sum(r.steps for r in clean_runs)
        out["clean_runs"] = {
            "n": len(clean_runs),
            "false_alarms": cf,
            "false_alarms_per_100_steps": round(100.0 * cf / cs, 3) if cs else 0.0,
            "interventions": sum(r.interventions for r in clean_runs),
            "task_success_pct": _pct([r.task_success for r in clean_runs]),
        }
    return out


def _counts(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[idx]
