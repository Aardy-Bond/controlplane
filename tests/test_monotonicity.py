"""Monotonicity is a claim, so it gets tested (PRD 7.5, T-701).

Binary search over the ledger is only sound if a monotone invariant, once
violated on some prefix, stays violated on every longer one. Every invariant
*declares* its monotonicity, and a wrong declaration would silently corrupt
localization while every test that only checks "did it fire" still passes.

So this file does the boring thing properly: generate randomised ledgers, find
prefixes where each invariant fails, and assert it keeps failing afterwards.
"""

from __future__ import annotations

import random
import zlib

import pytest

from controlplane.invariants import REGISTRY
from controlplane.types import SourceClass

from .factories import LedgerBuilder, ctx

TOOLS = ["lookup_policy", "read_data", "write_note", "issue_refund", "send_sms"]
SOURCES = [
    SourceClass.OK,
    SourceClass.OK,
    SourceClass.OK,
    SourceClass.ERROR_TAGGED,
    SourceClass.DENIED,
    SourceClass.UNLABELLED,
]


def random_ledger(rng: random.Random, workload: str, n: int = 16):
    """Adversarial generator.

    It deliberately produces every violating shape in the library — wrong types,
    missing required args, shifted fields, drifting bindings, mixed units,
    protected attributes, taints. If a check never fires here, its monotonicity
    claim is untested, and the test says so rather than passing quietly.
    """
    b = LedgerBuilder(workload=workload)
    b.constrain("data_as_of == FY26Q1")
    b.constrain("egress_allow: deadbeefdeadbeef")

    for i in range(n):
        tool = rng.choice(TOOLS)
        args: dict = {}

        if tool in {"lookup_policy", "issue_refund"}:
            args["policy_id"] = rng.choice(
                # The last two are a shifted field and a type error respectively.
                ["POL-100001", "POL-100002", "2026-04-01", 12345]
            )
        if tool in {"issue_refund", "read_data"}:
            args["amount"] = rng.choice([100.0, -5.0, 2400.0, "not-a-number"])
        if tool == "read_data":
            args["key"] = f"k{i}"
            # A second numeric field, so a call can combine figures whose
            # sources declared different units.
            args["weight"] = rng.choice([1.0, 250.0, 184_000_000.0])
        if tool == "send_sms":
            args["to"] = rng.choice(["+919845012301", "+919999999999"])
            args["message"] = rng.choice(["hello", "call me on 9876543210"])
        if tool == "write_note":
            args["body"] = "note"
        # Sometimes drop a required argument entirely.
        if rng.random() < 0.12 and args:
            args.pop(rng.choice(list(args)))
        # Sometimes parameterise an action by a protected attribute.
        if rng.random() < 0.12:
            args[rng.choice(["gender", "religion", "marital_status"])] = "F"
        # Bind, and sometimes drift the binding without a resolution event.
        if tool == "lookup_policy" and rng.random() < 0.6:
            b.bind("policy_id", str(args.get("policy_id", "POL-100001")))
        elif rng.random() < 0.1:
            b.bind("policy_id", rng.choice(["POL-100001", "POL-100002"]), at=0)

        result = {
            "value": rng.random(),
            "as_of": rng.choice(["FY26Q1", "FY26Q1", "FY25Q4"]),
            "unit": rng.choice(["INR", "USD", "ratio"]),
            "rows": [1, 2, 3],
            "total_count": rng.choice([3, 3, 9]),
            "goal_distance": rng.random(),
            "documents": [{"doc_id": "D1", "scope": rng.choice(["sales:public", "hr:restricted"])}],
        }
        # Point numeric args at earlier steps so unit consistency can see a
        # genuine mix of source units.
        prov: dict[str, int] = {}
        if i >= 2:
            for name in args:
                if rng.random() < 0.6:
                    prov[name] = rng.randrange(0, i)

        b.step(
            tool,
            args,
            result,
            source_class=rng.choice(SOURCES),
            provenance=prov,
            narrative=rng.choice(["", "no results found for that query", "proceeding"]),
        )
    return b.build()


MONOTONE = [i for i in REGISTRY.all() if i.monotone]
NON_MONOTONE = [i for i in REGISTRY.all() if not i.monotone]


@pytest.mark.parametrize("inv", MONOTONE, ids=lambda i: i.id)
def test_declared_monotone_invariants_are_monotone(inv):
    """Once false, always false — across 40 randomised ledgers per invariant."""
    context = ctx(workload="A" if inv.applies("A") else next(iter(inv.applies_to)))
    context.entitlements = {"sales:public"}
    violations_observed = 0

    for seed in range(40):
        # crc32, not hash(): Python randomises string hashing per process, so
        # seeding from it would make this suite pass or fail run to run. A
        # property test that changes its mind between runs is worse than none,
        # because you learn to rerun it instead of reading it.
        rng = random.Random(seed * 31 + zlib.crc32(inv.id.encode()) % 997)
        workload = next(iter(inv.applies_to - {"*"}), "A")
        context.workload = workload
        ledger = random_ledger(rng, workload)

        first_fail = None
        for n in range(len(ledger)):
            holds = inv.evaluate(ledger.prefix(n), context).holds
            if not holds and first_fail is None:
                first_fail = n
            if first_fail is not None and holds:
                pytest.fail(
                    f"{inv.id} declares monotone=True but recovered: failed at prefix "
                    f"{first_fail}, held again at prefix {n} (seed {seed}). Binary search "
                    f"would return a wrong last-good step for this invariant."
                )
        if first_fail is not None:
            violations_observed += 1

    assert violations_observed > 0, (
        f"{inv.id} never fired across 40 randomised ledgers, so its monotonicity is "
        "untested. Either the generator does not exercise it or the check is dead."
    )


@pytest.mark.parametrize("inv", NON_MONOTONE, ids=lambda i: i.id)
def test_non_monotone_invariants_are_routed_to_the_fallback(inv):
    """Non-monotone invariants must not claim the exact path.

    They are allowed to be non-monotone; they are not allowed to be silently
    treated as if binary search applied to them.
    """
    assert inv.monotone is False
    from controlplane.localize import LocalizationEngine
    from controlplane.types import Violation

    ledger = random_ledger(random.Random(3), next(iter(inv.applies_to - {"*"}), "C"))
    engine = LocalizationEngine(judge=None)
    v = Violation(
        invariant_id=inv.id,
        invariant_class=inv.invariant_class,
        severity=inv.severity,
        detected_at_step=len(ledger) - 1,
        detected_by="async",
        detail="synthetic",
    )
    loc = engine.localize(ledger, inv, ctx(workload="C"), v)
    assert loc.method.startswith("provenance_fallback")
    assert loc.quality == "estimated"


def test_binary_search_matches_linear_scan():
    """The optimisation must agree with the obvious implementation."""
    from controlplane.localize import LocalizationEngine
    from controlplane.types import Violation

    inv = REGISTRY.get("provenance.data_vintage")
    context = ctx(workload="C")
    engine = LocalizationEngine(judge=None)

    for seed in range(25):
        rng = random.Random(seed)
        ledger = random_ledger(rng, "C", n=20)
        linear = None
        for n in range(len(ledger)):
            if not inv.evaluate(ledger.prefix(n), context).holds:
                linear = n - 1
                break
        if linear is None:
            continue
        v = Violation(
            invariant_id=inv.id,
            invariant_class=inv.invariant_class,
            severity=inv.severity,
            detected_at_step=len(ledger) - 1,
            detected_by="inline",
            detail="synthetic",
        )
        loc = engine._binary_search(ledger, inv, context, v)
        assert loc.last_good_step == linear, f"seed {seed}: binary {loc.last_good_step} != {linear}"
        assert loc.quality == "exact"


def test_binary_search_is_logarithmic():
    """Evaluation count must scale with log(N), not N — the whole point."""
    from controlplane.localize import LocalizationEngine
    from controlplane.types import Violation

    inv = REGISTRY.get("provenance.data_vintage")
    engine = LocalizationEngine(judge=None)
    b = LedgerBuilder(workload="C").constrain("data_as_of == FY26Q1")
    for i in range(120):
        b.step("read_data", {"key": f"k{i}"}, {"as_of": "FY25Q4" if i == 77 else "FY26Q1"})
    ledger = b.build()

    v = Violation(
        invariant_id=inv.id,
        invariant_class=inv.invariant_class,
        severity=inv.severity,
        detected_at_step=len(ledger) - 1,
        detected_by="inline",
        detail="synthetic",
    )
    loc = engine._binary_search(ledger, inv, ctx(workload="C"), v)
    assert loc.last_good_step == 76
    assert loc.evaluations <= LocalizationEngine.expected_evaluations(len(ledger))
    assert loc.evaluations < 12, f"{loc.evaluations} evaluations for a 120-step ledger is not log N"
