"""Command line entry point.

Subcommands are deliberately few and each maps to something a reviewer would
actually ask for: run one scenario and watch it, run the ladder and get the
numbers, list what exists, and check the spend before committing to a grid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .harness.experiment import LADDER, run_ladder, write_claims
from .harness.metrics import score_run
from .harness.runner import RUNS_ROOT, RunConfig, run_scenario
from .invariants import REGISTRY
from .llm import BACKENDS, METER, BudgetExhausted, LLMClient
from .policy import PolicyRegistry
from .scenarios import FAULTS, GOLDEN_SCENARIOS

console = Console()


def _cmd_list(_args) -> int:
    table = Table(title="Golden scenarios", header_style="bold")
    for col in ("id", "env", "workload", "faults", "steps", "expects"):
        table.add_column(col)
    for sc in GOLDEN_SCENARIOS.values():
        expects = []
        if sc.expects_block:
            expects.append("block")
        if sc.expects_escalation:
            expects.append("escalation")
        if sc.clean:
            expects.append("no intervention")
        table.add_row(
            sc.id,
            sc.env,
            sc.workload,
            ",".join(f.fault_id for f in sc.faults) or "—",
            str(sc.max_steps),
            ",".join(expects) or "—",
        )
    console.print(table)

    inv = Table(title=f"Invariants ({len(REGISTRY)})", header_style="bold")
    for col in ("id", "class", "monotone", "cost", "severity", "workloads"):
        inv.add_column(col)
    for i in REGISTRY.all():
        inv.add_row(
            i.id,
            i.invariant_class.value,
            "yes" if i.monotone else "no",
            i.inline_cost_class.value,
            i.severity.value,
            ",".join(sorted(i.applies_to)),
        )
    console.print(inv)

    faults = Table(title="Fault catalogue", header_style="bold")
    for col in ("id", "title", "envs", "held out"):
        faults.add_column(col)
    for f in FAULTS.values():
        faults.add_row(f.fault_id, f.title, ",".join(f.envs), "yes" if f.held_out else "")
    console.print(faults)
    return 0


def _cmd_tiers(_args) -> int:
    policy = PolicyRegistry()
    table = Table(title="Policy tiers", header_style="bold")
    for col in ("tier", "inline p95", "inline classes", "async classes", "lag", "irreversible"):
        table.add_column(col)
    for name, tier in policy.tiers.items():
        table.add_row(
            name,
            f"{tier.inline_budget_p95_ms:g} ms",
            ",".join(sorted(tier.inline_classes)),
            ",".join(sorted(tier.async_classes)) or "—",
            str(tier.async_lag_steps),
            tier.irreversible_policy,
        )
    console.print(table)

    assign = Table(title="Workload assignment", header_style="bold")
    for col in ("workload", "name", "tenant", "tier"):
        assign.add_column(col)
    for wid, wp in policy.workloads.items():
        assign.add_row(wid, wp.name, wp.tenant, wp.tier.name)
    console.print(assign)
    return 0


def _cmd_run(args) -> int:
    if args.scenario not in GOLDEN_SCENARIOS:
        console.print(f"[red]unknown scenario {args.scenario!r}[/red]")
        return 2
    sc = GOLDEN_SCENARIOS[args.scenario]
    console.print(Panel(sc.narrative.strip(), title=f"{sc.id} — {sc.title}"))

    cfg = RunConfig(
        scenario=sc,
        seed=args.seed,
        backend=args.backend,
        supervisor_on=not args.no_supervisor,
        recovery_on=not args.no_recovery,
        adjudicator_on=not args.no_adjudicator,
    )
    try:
        record = run_scenario(cfg)
    except BudgetExhausted as exc:
        console.print(f"[red]spend ceiling reached: {exc}[/red]")
        return 3

    path = record.save(RUNS_ROOT)
    scored = score_run(record, clean=sc.clean)

    table = Table(title=f"{record.run_id}", header_style="bold")
    table.add_column("metric")
    table.add_column("value")
    rows = [
        ("condition", record.condition),
        ("model", record.model),
        ("steps", str(record.steps)),
        ("stop reason", record.stop_reason),
        ("task success", "yes" if record.task_success else "no"),
        ("detail", record.success_detail),
        ("fault steps", str(record.ground_truth_steps) or "—"),
        ("incidents", str(len(record.incidents))),
        ("localization error", str(scored.localization_error) or "—"),
        ("Δdetect", str(scored.delta_detect) or "—"),
        ("recovered", str(scored.recovered)),
        ("escalated", str(scored.escalated)),
        ("harm", json.dumps(record.harm)),
        ("inline p95", f"{scored.inline_ms_p95:.3f} ms"),
        ("tokens", str(record.tokens)),
        ("spend", f"${record.usd:.5f}"),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)

    for inc in record.incidents:
        loc = inc.get("localization") or {}
        console.print(
            Panel(
                f"[bold]{inc['violation']['invariant_id']}[/bold]\n"
                f"{inc['violation']['detail']}\n\n"
                f"last good step: {loc.get('last_good_step')}  "
                f"({loc.get('method')}, {loc.get('quality')}, "
                f"{loc.get('evaluations')} evaluations)",
                title="incident",
                border_style="yellow",
            )
        )
    console.print(f"saved -> {path}")
    return 0


def _cmd_ladder(args) -> int:
    scenario_ids = args.scenarios or list(GOLDEN_SCENARIOS)
    rungs = args.rungs or [r.name for r in LADDER]
    total_cells = len(scenario_ids) * len(args.seeds) * len(args.backends) * len(rungs)
    console.print(
        f"[bold]{total_cells}[/bold] cells: {len(scenario_ids)} scenarios x "
        f"{len(args.seeds)} seeds x {len(args.backends)} backends x {len(rungs)} rungs"
    )

    def progress(i, n, cfg):
        console.print(f"[dim]({i}/{n})[/dim] {cfg.scenario.id} {cfg.condition} {cfg.backend}")

    try:
        exp = run_ladder(
            scenario_ids=scenario_ids,
            seeds=args.seeds,
            backends=args.backends,
            rungs=rungs,
            on_progress=progress,
        )
    except BudgetExhausted as exc:
        console.print(f"[red]spend ceiling reached mid-grid: {exc}[/red]")
        return 3

    judge = LLMClient("judge") if args.llm_baseline else None
    localization = exp.localization_vs_baselines(judge=judge)

    exp.save(localization=localization)
    claims = write_claims(exp, localization=localization)

    table = Table(title="By condition", header_style="bold")
    for col in ("condition", "n", "success %", "detections", "exact L %", "regret %", "p95 ms"):
        table.add_column(col)
    for cond, agg in exp.summary().items():
        table.add_row(
            cond,
            str(agg["n"]),
            f"{agg['task_success_pct']}",
            str(agg["detections"]),
            str(agg["localization"]["exact_step_pct"]),
            str(agg["intervention_regret_pct"]),
            f"{agg['inline_ms_p95']:.3f}",
        )
    console.print(table)

    if localization.get("incidents"):
        comp = Table(title="Localization vs baselines", header_style="bold")
        for col in ("method", "n", "exact %", "within 1 %", "mean err", "calls"):
            comp.add_column(col)
        ours = localization["ours"]
        comp.add_row(
            "ours (binary search)",
            str(ours["n"]),
            str(ours["exact_step_pct"]),
            str(ours["within_1_pct"]),
            str(ours["mean_abs_error"]),
            str(ours["mean_calls"]),
        )
        for name, block in localization["baselines"].items():
            if not block.get("n"):
                comp.add_row(name, "0", "—", "—", "—", "not run")
                continue
            comp.add_row(
                name,
                str(block["n"]),
                str(block["exact_step_pct"]),
                str(block["within_1_pct"]),
                str(block["mean_abs_error"]),
                str(block["mean_calls"]),
            )
        console.print(comp)

    console.print(Panel(json.dumps(exp.paired_supervisor_effect(), indent=2), title="paired test"))
    console.print(f"claims -> {claims}")
    console.print(f"spend  -> ${METER.summary().get('usd', 0.0):.4f}")
    return 0


def _cmd_spend(_args) -> int:
    table = Table(title="Backends", header_style="bold")
    for col in ("name", "model", "vendor", "$/Mtok in", "$/Mtok out"):
        table.add_column(col)
    for name, spec in BACKENDS.items():
        table.add_row(
            name,
            spec["model"],
            spec["vendor"],
            f"{spec['in_per_mtok']:.3f}",
            f"{spec['out_per_mtok']:.3f}",
        )
    console.print(table)
    console.print(Panel(json.dumps(METER.summary(), indent=2), title="spend so far"))
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("controlplane.dashboard.app:app", host=args.host, port=args.port, reload=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="controlplane", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="scenarios, invariants and faults").set_defaults(fn=_cmd_list)
    sub.add_parser("tiers", help="policy tiers and their budgets").set_defaults(fn=_cmd_tiers)
    sub.add_parser("spend", help="model pricing and spend so far").set_defaults(fn=_cmd_spend)

    run = sub.add_parser("run", help="run one scenario")
    run.add_argument("scenario")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--backend", default="primary", choices=sorted(BACKENDS))
    run.add_argument("--no-supervisor", action="store_true")
    run.add_argument("--no-recovery", action="store_true")
    run.add_argument("--no-adjudicator", action="store_true")
    run.set_defaults(fn=_cmd_run)

    ladder = sub.add_parser("ladder", help="run the ablation ladder and write CLAIMS.md")
    ladder.add_argument("--scenarios", nargs="*", default=None)
    ladder.add_argument("--seeds", nargs="*", type=int, default=[7])
    ladder.add_argument("--backends", nargs="*", default=["primary"])
    ladder.add_argument("--rungs", nargs="*", default=None, choices=[r.name for r in LADDER])
    ladder.add_argument(
        "--llm-baseline",
        action="store_true",
        help="also run the whole-trace LLM localizer (costs one model call per incident)",
    )
    ladder.set_defaults(fn=_cmd_ladder)

    serve = sub.add_parser("serve", help="run the dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(fn=_cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
