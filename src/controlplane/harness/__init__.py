"""Evaluation harness: run scenarios, compute metrics, produce evidence.

Lazily re-exported for the same reason as the top-level package: `metrics` is
pure arithmetic over saved records and the dashboard uses it on every incident
request, but importing it through this module used to drag in the runner, the
experiment ladder and the OpenRouter client behind them. Scoring evidence that
is already on disk should not require the machinery that produced it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "BASELINES": "baselines",
    "localize_with_baseline": "baselines",
    "LADDER": "experiment",
    "Experiment": "experiment",
    "run_ladder": "experiment",
    "write_claims": "experiment",
    "ScoredRun": "metrics",
    "aggregate": "metrics",
    "mcnemar": "metrics",
    "score_run": "metrics",
    "RunConfig": "runner",
    "RunRecord": "runner",
    "run_scenario": "runner",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:
    from .baselines import BASELINES, localize_with_baseline
    from .experiment import LADDER, Experiment, run_ladder, write_claims
    from .metrics import ScoredRun, aggregate, mcnemar, score_run
    from .runner import RunConfig, RunRecord, run_scenario

__all__ = [
    "BASELINES",
    "LADDER",
    "Experiment",
    "RunConfig",
    "RunRecord",
    "ScoredRun",
    "aggregate",
    "localize_with_baseline",
    "mcnemar",
    "run_ladder",
    "run_scenario",
    "score_run",
    "write_claims",
]
