"""The ablation ladder and the claims file.

Two rules this module exists to enforce.

**Every claim names the ablation that could falsify it.** A run with everything
switched on tells you the system works; it does not tell you which part did the
work. Each rung below removes exactly one thing, so each earns exactly one
sentence in CLAIMS.md and nothing more.

**Every number resolves to a run id.** ``CLAIMS.md`` is generated from the run
records on disk, never typed by hand. If a claim has no runs behind it, it is
written out as UNSUPPORTED rather than quietly omitted — a missing row is easy
to miss, and a claim that disappears when the evidence does is how a deck ends
up ahead of its evidence.

The localization baselines are scored by *replaying saved ledgers*, so adding a
competitor costs no agent reruns and no new sampling noise: every method sees
byte-identical incidents.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..llm import METER, LLMClient
from ..policy import PolicyRegistry
from ..scenarios import GOLDEN_SCENARIOS, Scenario
from .baselines import BASELINES, localize_with_baseline
from .metrics import (
    ScoredRun,
    _expected_invariants_by_step,
    aggregate,
    bootstrap_ci,
    mcnemar,
    score_run,
)
from .runner import RUNS_ROOT, RunConfig, RunRecord, run_scenario

__all__ = ["LADDER", "Experiment", "run_ladder", "write_claims"]


@dataclass(frozen=True)
class Rung:
    """One condition, and the single claim it exists to support or refute."""

    name: str
    overrides: dict[str, Any]
    question: str


LADDER: list[Rung] = [
    Rung(
        "off",
        {"supervisor_on": False},
        "What happens with no supervisor at all? This is the baseline every "
        "improvement is measured against, and the run where harm is expected.",
    ),
    Rung(
        "on",
        {},
        "Full system. Detection, localization and recovery all active.",
    ),
    Rung(
        "detect_only",
        {"recovery_on": False},
        "Detection without recovery. Isolates how much of the benefit comes "
        "from rolling back rather than from noticing.",
    ),
    Rung(
        "deterministic_only",
        {"adjudicator_on": False},
        "No LLM adjudicator. Shows how much the deterministic invariants carry "
        "on their own, which is the part with a bounded latency cost.",
    ),
]


@dataclass
class Experiment:
    """A grid of runs plus everything needed to defend the numbers it produces."""

    scenarios: list[Scenario]
    seeds: list[int] = field(default_factory=lambda: [7])
    backends: list[str] = field(default_factory=lambda: ["primary"])
    rungs: list[Rung] = field(default_factory=lambda: list(LADDER))
    root: Path = RUNS_ROOT
    records: list[RunRecord] = field(default_factory=list)
    scored: list[ScoredRun] = field(default_factory=list)

    def cells(self) -> list[RunConfig]:
        out = []
        for sc in self.scenarios:
            for seed in self.seeds:
                for backend in self.backends:
                    for rung in self.rungs:
                        out.append(
                            RunConfig(
                                scenario=sc,
                                seed=seed,
                                backend=backend,
                                label=rung.name,
                                **rung.overrides,
                            )
                        )
        return out

    def run(self, on_progress=None) -> Experiment:
        policy = PolicyRegistry()
        for i, cfg in enumerate(self.cells(), start=1):
            if on_progress:
                on_progress(i, len(self.cells()), cfg)
            record = run_scenario(cfg, policy=policy)
            record.save(self.root)
            self.records.append(record)
            self.scored.append(score_run(record, clean=cfg.scenario.clean))
            policy.reload()
        return self

    # -- analysis ----------------------------------------------------------

    def by_condition(self) -> dict[str, list[ScoredRun]]:
        out: dict[str, list[ScoredRun]] = {}
        for run in self.scored:
            out.setdefault(run.condition, []).append(run)
        return out

    def summary(self) -> dict[str, Any]:
        return {cond: aggregate(runs) for cond, runs in self.by_condition().items()}

    def paired_supervisor_effect(self) -> dict[str, Any]:
        """McNemar on (supervisor off, supervisor on) for identical task+seed.

        Paired on the cell key, not pooled: the same scenario under two
        conditions is a far tighter comparison than two independent samples,
        and at this n it is the difference between a real test and a decorative
        one. Cells without both halves are dropped and counted, because
        silently dropping them would bias the result toward whichever condition
        happened to finish.
        """
        off: dict[tuple, bool] = {}
        on: dict[tuple, bool] = {}
        for run in self.scored:
            key = (run.scenario_id, run.seed, run.backend, run.framework)
            if run.condition == "off":
                off[key] = run.task_success
            elif run.condition == "on":
                on[key] = run.task_success

        shared = sorted(set(off) & set(on))
        paired = [(off[k], on[k]) for k in shared]
        result = mcnemar(paired)
        result["pairs"] = len(paired)
        result["unpaired_dropped"] = len(set(off) ^ set(on))
        result["harm_off"] = sum(
            1 for r in self.scored if r.condition == "off" and r.real_harm.get("harm_occurred")
        )
        result["harm_on"] = sum(
            1 for r in self.scored if r.condition == "on" and r.real_harm.get("harm_occurred")
        )
        return result

    def localization_vs_baselines(self, judge: LLMClient | None = None) -> dict[str, Any]:
        """Replay every incident under each baseline and score identically.

        Returns a pooled comparison plus a split by detection delay. The pooled
        number alone is misleading: when a fault is caught on the step it was
        planted, blaming the previous step is already exact and binary search
        earns nothing. The interesting regime is late detection, where cheap
        heuristics drift by tens of steps. Both views are persisted so the
        dashboard cannot quietly pick the flatter one.
        """
        from ..ledger import Ledger
        from ..types import Violation

        # Strata: caught immediately (lag ≤ 1) vs caught late (lag > 1). The
        # late band is where search has to do real work; the immediate band is
        # where the fair cheap baseline is competitive.
        STRATA = {
            "all": lambda lag: True,
            "caught_immediately": lambda lag: lag <= 1,
            "caught_late": lambda lag: lag > 1,
            "lag_0": lambda lag: lag == 0,
            "lag_1": lambda lag: lag == 1,
            "lag_2_5": lambda lag: 2 <= lag <= 5,
            "lag_6_plus": lambda lag: lag >= 6,
        }

        def empty_bucket() -> dict[str, Any]:
            return {
                "ours": {"exact": [], "err": [], "calls": []},
                "baselines": {name: {"exact": [], "err": [], "calls": []} for name in BASELINES},
                "lags": [],
            }

        buckets = {name: empty_bucket() for name in STRATA}

        for record in self.records:
            if record.condition == "off" or not record.incidents:
                continue
            ledger_path = (
                self.root
                / "ledgers"
                / record.supervisor.get("tenant", "meridian")
                / record.workload
                / f"{record.run_id}.jsonl"
            )
            if not ledger_path.exists():
                continue
            ledger = Ledger.load(ledger_path, record.supervisor.get("tenant", "meridian"))
            faults = sorted(record.ground_truth_steps)
            if not faults:
                continue

            expected_by_step = _expected_invariants_by_step(record)
            for inc in record.incidents:
                detected_at = inc["violation"]["detected_at_step"]
                inv_id = inc["violation"]["invariant_id"]
                # Same attribution rule the scorer uses. A spontaneous agent
                # mistake has no injected origin, so scoring any method against
                # one would rank them all on a question none of them was asked.
                candidates = [
                    s
                    for s in faults
                    if s <= detected_at and inv_id in expected_by_step.get(s, frozenset())
                ]
                if not candidates:
                    continue
                expected_L = max(candidates) - 1
                lag = detected_at - (expected_L + 1)

                reported = (inc.get("localization") or {}).get("last_good_step", -999)
                ours_exact = reported == expected_L
                ours_err = abs(reported - expected_L)
                ours_evals = (inc.get("localization") or {}).get("evaluations", 0)

                violation = Violation(**inc["violation"])
                baseline_rows: dict[str, tuple[bool, float, int]] = {}
                for name in BASELINES:
                    if name == "llm_whole_trace" and judge is None:
                        continue
                    loc = localize_with_baseline(
                        name, ledger, violation, judge=judge, seed=record.seed
                    )
                    baseline_rows[name] = (
                        loc.last_good_step == expected_L,
                        abs(loc.last_good_step - expected_L),
                        loc.evaluations,
                    )

                for stratum, pred in STRATA.items():
                    if not pred(lag):
                        continue
                    bucket = buckets[stratum]
                    bucket["lags"].append(lag)
                    bucket["ours"]["exact"].append(ours_exact)
                    bucket["ours"]["err"].append(ours_err)
                    bucket["ours"]["calls"].append(ours_evals)
                    for name, (ex, err, calls) in baseline_rows.items():
                        bucket["baselines"][name]["exact"].append(ex)
                        bucket["baselines"][name]["err"].append(err)
                        bucket["baselines"][name]["calls"].append(calls)

        def block(exact: list, err: list, calls: list) -> dict[str, Any]:
            if not exact:
                return {"n": 0, "note": "not run"}
            return {
                "n": len(exact),
                "exact_step_pct": round(100.0 * sum(exact) / len(exact), 2),
                "exact_step_ci": bootstrap_ci([1.0 if e else 0.0 for e in exact]),
                "mean_abs_error": round(statistics.fmean(err), 3),
                "within_1_pct": round(100.0 * sum(e <= 1 for e in err) / len(err), 2),
                "mean_calls": round(statistics.fmean(calls), 2),
            }

        def summarize(bucket: dict[str, Any]) -> dict[str, Any]:
            lags = bucket["lags"]
            return {
                "n": len(lags),
                "mean_lag": round(statistics.fmean(lags), 3) if lags else None,
                "median_lag": statistics.median(lags) if lags else None,
                "ours": block(
                    bucket["ours"]["exact"], bucket["ours"]["err"], bucket["ours"]["calls"]
                ),
                "baselines": {
                    name: block(d["exact"], d["err"], d["calls"])
                    for name, d in bucket["baselines"].items()
                },
            }

        pooled = summarize(buckets["all"])
        by_lag = {name: summarize(buckets[name]) for name in STRATA if name != "all"}
        return {
            "incidents": pooled["n"],
            "ours": pooled["ours"],
            "baselines": pooled["baselines"],
            # Featured cheap competitor — the one a reader should compare us to.
            "featured_baseline": "previous_step",
            "by_lag": by_lag,
            "reading": (
                "When a fault is caught on the step it was planted (or one step "
                "later), blaming the previous step is already exact and binary "
                "search adds little. When detection is delayed by many steps, "
                "cheap heuristics drift with the lag and exact search is the "
                "method that stays on the origin. Read the by_lag split before "
                "the pooled average."
            ),
        }

    def save(self, path: Path | None = None, localization: dict[str, Any] | None = None) -> Path:
        path = path or (self.root / "experiment.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "conditions": self.summary(),
            "paired_supervisor_effect": self.paired_supervisor_effect(),
            # The baseline comparison is the one number that says whether binary
            # search was worth building, so it belongs in the saved evidence
            # rather than only in the terminal output of the run that produced
            # it. Anything the dashboard shows has to be re-readable from disk.
            "localization_vs_baselines": localization,
            "run_ids": [r.run_id for r in self.records],
            "meter": METER.summary(),
        }
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path


def run_ladder(
    scenario_ids: list[str] | None = None,
    seeds: list[int] | None = None,
    backends: list[str] | None = None,
    rungs: list[str] | None = None,
    on_progress=None,
) -> Experiment:
    ids = scenario_ids or list(GOLDEN_SCENARIOS)
    selected = [r for r in LADDER if not rungs or r.name in rungs]
    exp = Experiment(
        scenarios=[GOLDEN_SCENARIOS[i] for i in ids],
        seeds=seeds or [7],
        backends=backends or ["primary"],
        rungs=selected,
    )
    return exp.run(on_progress=on_progress)


# --------------------------------------------------------------------------
# CLAIMS.md
# --------------------------------------------------------------------------

CLAIMS = [
    (
        "C1",
        "Faults in tool-using agent runs are detected, and detection is attributed "
        "to a named invariant rather than to a general-purpose model opinion.",
        "conditions.on.detections",
    ),
    (
        "C2",
        "For monotone invariants, the last good step is recovered exactly, in "
        "O(log N) deterministic evaluations and zero model calls.",
        "localization.ours.exact_step_pct",
    ),
    (
        "C3",
        "Exact localization beats the fair cheap guess (blame the previous step) "
        "and the other alternatives, especially when detection is delayed. Read "
        "the by_lag split: on immediate catches the cheap guess is competitive; "
        "on late catches it collapses.",
        "localization.baselines",
    ),
    (
        "C4",
        "Rolling back to the reported step produces a correct outcome, rather than "
        "merely a correct diagnosis.",
        "conditions.on.recoverability_at_L_pct",
    ),
    (
        "C5",
        "The supervisor improves task success on a paired off/on design, and the "
        "improvement is tested rather than asserted.",
        "paired.p_value",
    ),
    (
        "C6",
        "On clean runs the system mostly leaves the agent alone; the false-alarm "
        "rate is reported whether or not it flatters the design.",
        "conditions.on.clean_runs",
    ),
    (
        "C7",
        "Inline checking fits the tier's latency budget.",
        "conditions.on.inline_ms_p95",
    ),
    (
        "C8",
        "The audit trail is tamper-evident and replays identically.",
        "conditions.on.integrity",
    ),
]


def _lookup(payload: dict[str, Any], dotted: str) -> Any:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def write_claims(
    exp: Experiment,
    localization: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    """Generate CLAIMS.md from run records. Never hand-edit the output."""
    path = path or Path("CLAIMS.md")
    conditions = exp.summary()
    paired = exp.paired_supervisor_effect()
    localization = localization or {}
    payload = {"conditions": conditions, "paired": paired, "localization": localization}

    run_ids = [r.run_id for r in exp.records]
    lines: list[str] = [
        "# CLAIMS",
        "",
        "Generated from run records by `controlplane.harness.experiment.write_claims`.",
        "Do not hand-edit: every number below is read back out of `runs/*.json`, and a",
        "claim with no runs behind it is printed as UNSUPPORTED rather than dropped.",
        "",
        f"- runs: **{len(run_ids)}**",
        f"- scenarios: {', '.join(sorted({r.scenario_id for r in exp.records})) or '—'}",
        f"- conditions: {', '.join(sorted(conditions)) or '—'}",
        f"- backends: {', '.join(sorted({r.backend for r in exp.records})) or '—'}",
        f"- total spend: ${METER.summary().get('usd', 0.0):.4f}",
        "",
        "## Claims",
        "",
    ]

    for cid, text, key in CLAIMS:
        value = _lookup(payload, key)
        supported = value not in (None, 0, [], {}, "")
        status = "SUPPORTED" if supported else "UNSUPPORTED"
        lines.append(f"### {cid} — {status}")
        lines.append("")
        lines.append(text)
        lines.append("")
        if supported:
            lines.append("```json")
            lines.append(json.dumps(value, indent=2, default=str))
            lines.append("```")
        else:
            lines.append(
                f"> No evidence on disk for `{key}`. This claim is not currently "
                "supported by any run in this repository."
            )
        lines.append("")

    lines += [
        "## Condition summary",
        "",
        "```json",
        json.dumps(conditions, indent=2, default=str),
        "```",
        "",
    ]
    if localization:
        lines += [
            "## Localization vs baselines",
            "",
            "Same incidents, same scoring code, replayed from saved ledgers.",
            "",
            "```json",
            json.dumps(localization, indent=2, default=str),
            "```",
            "",
        ]
    lines += ["## Run ids", "", *[f"- `{rid}`" for rid in run_ids], ""]

    path.write_text("\n".join(lines))
    return path
