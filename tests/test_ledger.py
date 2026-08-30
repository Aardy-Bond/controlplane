"""Ledger guarantees: tamper-evidence, replay, rollback views, tenant isolation.

These are the properties the rest of the system quietly assumes. Localization
is only trustworthy hours after the fact if the ledger replays exactly; the
audit trail is only worth showing a regulator if edits are detectable; and
FR-8 is only real if a cross-tenant read raises rather than returns.
"""

from __future__ import annotations

import json

import pytest

from controlplane.ledger import Ledger, LedgerStore, TenantIsolationError
from controlplane.types import ObservedState, SourceClass, canonical_hash

from .factories import LedgerBuilder


def sample(workload: str = "A", n: int = 8) -> Ledger:
    b = LedgerBuilder(workload=workload)
    b.bind("policy_id", "POL-100001")
    for i in range(n):
        b.step(
            "lookup_policy" if i % 2 == 0 else "read_data",
            {"policy_id": "POL-100001"} if i % 2 == 0 else {"key": f"k{i}", "amount": 10.0 * i},
            {"value": i, "unit": "INR"},
        )
    return b.build()


# -- tamper evidence -------------------------------------------------------


def test_chain_is_intact_on_an_untouched_ledger():
    ok, why = sample().verify_chain()
    assert ok, why


def test_editing_a_checkpoint_body_is_detected():
    led = sample()
    led._physical[3].narrative = "nothing to see here"
    ok, why = led.verify_chain()
    assert not ok
    assert "tampered" in why


def test_deleting_a_checkpoint_is_detected():
    led = sample()
    del led._physical[4]
    ok, why = led.verify_chain()
    assert not ok
    assert "chain break" in why


def test_reordering_checkpoints_is_detected():
    led = sample()
    led._physical[2], led._physical[5] = led._physical[5], led._physical[2]
    ok, _ = led.verify_chain()
    assert not ok


# -- replay ----------------------------------------------------------------


def test_mutating_a_live_object_after_append_cannot_rewrite_history():
    """The regression that broke every audit chain in the corpus.

    The supervisor passed its live Budget by shallow copy, so `tool_calls` was
    shared by reference and every later step mutated the dict inside every
    checkpoint already written. The chain broke, and — much worse — an
    invariant evaluating an early prefix saw counters from the future, which
    silently violates the prefix purity that binary search depends on.
    """
    b = LedgerBuilder()
    budget = b.budget
    commitments = b.commitments

    led = Ledger("mut", "meridian", "A", "test", redact=False)
    led.append(commitments=commitments, pending_call=None, budget=budget)
    snapshot = led[0].budget.tool_calls.copy()

    budget.tool_calls["issue_refund"] = 99
    budget.steps_used = 41
    commitments.constraints.append("added later")

    assert led[0].budget.tool_calls == snapshot, "a later mutation reached a stored checkpoint"
    assert led[0].budget.steps_used != 41
    assert "added later" not in led[0].commitments.constraints
    ok, why = led.verify_chain()
    assert ok, why


def test_replay_is_identical():
    assert sample(n=12).replay_identical()


def test_replay_is_identical_after_a_rollback():
    led = sample(n=10)
    led.rollback_to(4)
    assert len(led) == 5
    assert led.replay_identical()


def test_rollback_truncates_the_logical_view_but_not_history():
    led = sample(n=10)
    physical_before = len(led.physical_log)
    led.rollback_to(3)

    assert len(led) == 4, "logical view must be truncated to L inclusive"
    assert len(led.physical_log) == physical_before + 1, (
        "history must grow by exactly the rollback marker — nothing is ever removed"
    )
    assert led.physical_log[-1].rollback_to == 3


def test_appending_after_rollback_reuses_the_step_index_in_a_new_epoch():
    led = sample(n=6)
    before = led[4].epoch
    led.rollback_to(3)
    b = LedgerBuilder()
    led.append(
        commitments=b.commitments,
        pending_call=None,
        budget=b.budget,
        narrative="second attempt at step 4",
    )
    assert led[4].step == 4
    assert led[4].epoch == before + 1, "the retry must be distinguishable from the attempt"
    assert led.replay_identical()


def test_attach_result_revises_without_mutating_history():
    b = LedgerBuilder()
    b.step("read_data", {"key": "k"}, {"value": 1})
    led = b.build()
    physical_before = len(led.physical_log)

    led.attach_result(
        ObservedState(response_hash="h", source_class=SourceClass.OK, preview={"value": 2}),
        SourceClass.OK,
    )
    assert len(led.physical_log) == physical_before + 1
    assert len(led) == 1, "the logical view still has one step"
    assert led[0].result.preview["value"] == 2
    ok, why = led.verify_chain()
    assert ok, why


# -- persistence and tenant isolation --------------------------------------


def test_round_trip_through_disk_preserves_everything(tmp_path):
    led = sample(n=9)
    led.rollback_to(5)
    path = led.save(tmp_path)

    loaded = Ledger.load(path, tenant="meridian")
    assert canonical_hash([c.model_dump(mode="json") for c in loaded.physical_log]) == (
        canonical_hash([c.model_dump(mode="json") for c in led.physical_log])
    )
    assert len(loaded) == len(led)
    assert loaded.verify_chain()[0]


def test_loading_someone_elses_ledger_raises(tmp_path):
    path = sample().save(tmp_path)
    with pytest.raises(TenantIsolationError):
        Ledger.load(path, tenant="northwind")


def test_store_denies_cross_tenant_read_of_a_live_run(tmp_path):
    store = LedgerStore(tmp_path)
    store.open("run-1", "meridian", "A", "interactive")

    assert store.get("run-1", "meridian").run_id == "run-1"
    with pytest.raises(TenantIsolationError):
        store.get("run-1", "northwind")
    assert store.isolation_denials == 1


def test_store_does_not_reveal_that_another_tenants_run_exists(tmp_path):
    store = LedgerStore(tmp_path)
    store.persist(sample())
    store._live.clear()

    with pytest.raises(TenantIsolationError) as exc:
        store.get("test-run", "northwind")
    # The message for "exists elsewhere" and "does not exist" must be the same
    # shape, or the error itself becomes a cross-tenant existence oracle.
    assert "meridian" not in str(exc.value)

    with pytest.raises(TenantIsolationError) as missing:
        store.get("no-such-run", "northwind")
    assert str(exc.value).replace("test-run", "X") == str(missing.value).replace("no-such-run", "X")


def test_list_runs_is_scoped_to_the_tenant(tmp_path):
    store = LedgerStore(tmp_path)
    store.persist(sample())
    other = LedgerBuilder().build()
    other.tenant = "northwind"
    other.run_id = "other-run"
    store.persist(other)

    assert store.list_runs("meridian") == ["test-run"]
    assert store.list_runs("northwind") == ["other-run"]


def test_jsonl_is_one_valid_record_per_line():
    for line in sample().to_jsonl().splitlines():
        json.loads(line)
