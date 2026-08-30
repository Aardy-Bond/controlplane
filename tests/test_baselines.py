"""Tests for localization baselines and lag stratification."""

from __future__ import annotations

from controlplane.harness.baselines import BASELINES, localize_with_baseline
from controlplane.ledger import Ledger
from controlplane.types import (
    Budget,
    Commitments,
    InvariantClass,
    ObservedState,
    PendingCall,
    Reversibility,
    Severity,
    SourceClass,
    Violation,
)


def _ledger_with_calls(tools: list[tuple[str, Reversibility]]) -> Ledger:
    led = Ledger(run_id="t", tenant="meridian", workload="A", tier="interactive-support")
    for i, (tool, rev) in enumerate(tools):
        led.append(
            commitments=Commitments(),
            pending_call=PendingCall(tool=tool, args={}, reversibility=rev),
            result=ObservedState(
                response_hash="x",
                preview={"ok": True},
                source_class=SourceClass.OK,
            ),
            source_class=SourceClass.OK,
            budget=Budget(steps_used=i),
            narrative=f"call {tool}",
        )
    return led


def _violation(step: int) -> Violation:
    return Violation(
        invariant_id="binding.provenance_traced",
        invariant_class=InvariantClass.BINDING,
        severity=Severity.BLOCK,
        detected_at_step=step,
        detected_by="inline",
        detail="test",
    )


def test_previous_step_is_alarm_minus_one():
    led = _ledger_with_calls(
        [("lookup", Reversibility.REVERSIBLE), ("refund", Reversibility.IRREVERSIBLE)]
    )
    loc = localize_with_baseline("previous_step", led, _violation(1))
    assert loc.last_good_step == 0


def test_detected_at_sanity_floor_blames_alarm_itself():
    led = _ledger_with_calls([("lookup", Reversibility.REVERSIBLE)])
    loc = localize_with_baseline("detected_at", led, _violation(0))
    assert loc.last_good_step == 0  # alarm step itself — deliberately off by +1 vs true L


def test_last_write_skips_reversible_reads():
    led = _ledger_with_calls(
        [
            ("lookup", Reversibility.REVERSIBLE),
            ("update", Reversibility.COMPENSABLE),
            ("lookup2", Reversibility.REVERSIBLE),
        ]
    )
    loc = localize_with_baseline("last_write", led, _violation(2))
    # Most recent write was step 1 (update); last-good guess is 0.
    assert loc.last_good_step == 0


def test_baselines_do_not_clamp_to_post_rollback_view():
    """After a rollback the logical view ends before the alarm; baselines must not."""
    led = _ledger_with_calls(
        [
            ("a", Reversibility.REVERSIBLE),
            ("b", Reversibility.REVERSIBLE),
            ("c", Reversibility.IRREVERSIBLE),
            ("d", Reversibility.REVERSIBLE),
        ]
    )
    # Roll back to step 1 — logical last_step becomes 1, alarm was at 3.
    led.rollback_to(1)
    assert led.last_step == 1
    v = _violation(3)
    prev = localize_with_baseline("previous_step", led, v)
    assert prev.last_good_step == 2  # not clamped to 0
    write = localize_with_baseline("last_write", led, v)
    assert write.last_good_step == 1  # irreversible call at step 2 → L guess 1


def test_baseline_registry_order_features_fair_guess_first():
    names = list(BASELINES)
    assert names[0] == "previous_step"
    assert "detected_at" in names
    assert "llm_whole_trace" in names
