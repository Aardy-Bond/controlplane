"""PII detection, and the harder question: does the checker itself leak?

A supervisor that reads everything is a new place for personal data to
accumulate. FR-9 says the ledger stores offsets and hashes, never raw values.
The observer-purity tests below assert that on the actual persisted bytes,
because that is the artefact that gets shipped to an auditor or a dashboard.

Recall is not the only thing that matters here. Over-redaction is a real
failure too: redact POL-100001 into a pincode placeholder and the binding
invariants lose the identifier they exist to check.
"""

from __future__ import annotations

import json

import pytest

from controlplane.pii import detect, redact, redact_structure

from .factories import LedgerBuilder

# -- true positives --------------------------------------------------------

POSITIVES = [
    ("email", "write to ramesh.kumar@example.co.in about it"),
    ("phone_in", "call him on +91 9845012301 tomorrow"),
    ("phone_in", "his mobile is 9845012301"),
    ("aadhaar", "aadhaar 4123 5678 9012 on file"),
    ("pan", "PAN ABCDE1234F verified"),
    ("ifsc", "branch code HDFC0001234"),
    ("dob", "born 14/03/1981 in Pune"),
    ("passport_in", "passport M1234567 expires soon"),
    ("person_name", "the policyholder is Ramesh Kumar"),
    ("person_name", "Mr. Ramesh Kumar called"),
    ("address", "12/A, Vittal Mallya Road, Bengaluru 560001"),
]


@pytest.mark.parametrize("kind,text", POSITIVES, ids=[f"{k}:{t[:18]}" for k, t in POSITIVES])
def test_detects_expected_pii(kind, text):
    kinds = {s.kind for s in detect(text)}
    assert kind in kinds, f"expected {kind} in {text!r}, got {kinds}"


def test_card_number_requires_a_valid_checksum():
    assert "card" in {s.kind for s in detect("card 4539 1488 0343 6467")}
    # A 16-digit string that fails Luhn is far more likely a reference number.
    assert "card" not in {s.kind for s in detect("reference 1234 5678 9012 3456")}


# -- true negatives: business keys must survive ----------------------------

NEGATIVES = [
    "POL-100001",
    "CLM-100042",
    "policy POL-100001 amount 2400",
    "quote QTE-2026-0001 issued",
    "treaty TR-880012 renewed",
    "FY26Q1 loss ratio 0.62",
    "segment MOTOR-SOUTH exposure 184000000",
    "step 42 of 55 complete",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_business_keys_are_not_redacted(text):
    cleaned, spans = redact(text)
    assert cleaned == text, f"over-redacted {text!r} -> {cleaned!r} via {[s.kind for s in spans]}"


def test_policy_id_survives_redaction_inside_a_structure():
    # This is the exact regression that produced a false binding violation:
    # POL-100001 matched the pincode shape and became POL-[PINCODE].
    safe, _ = redact_structure({"policy_id": "POL-100001", "amount": 2400})
    assert safe["policy_id"] == "POL-100001"


def test_account_number_in_a_labelled_field_is_still_redacted():
    safe, spans = redact_structure({"account_number": "50100234567890"})
    assert safe["account_number"] != "50100234567890"
    assert spans


# -- structural redaction --------------------------------------------------


def test_redaction_preserves_shape_and_placeholder_types():
    text = "email ramesh@x.com and phone 9845012301"
    cleaned, spans = redact(text)
    assert "[EMAIL]" in cleaned and "[PHONE_IN]" in cleaned
    assert "ramesh@x.com" not in cleaned and "9845012301" not in cleaned
    assert len(spans) == 2


def test_nested_structures_are_walked():
    safe, spans = redact_structure(
        {"customer": {"name": "Ramesh Kumar", "contacts": [{"email": "r@x.com"}]}}
    )
    assert safe["customer"]["name"] == "[NAME]"
    assert safe["customer"]["contacts"][0]["email"] == "[EMAIL]"
    assert len(spans) == 2


def test_spans_are_non_overlapping_and_ordered():
    spans = detect("Mr. Ramesh Kumar, 12/A, Vittal Mallya Road, Bengaluru 560001, r@x.com")
    for a, b in zip(spans, spans[1:], strict=False):
        assert a.end <= b.start, "overlapping spans would corrupt offset-based redaction"


# -- observer purity: the ledger itself must not hold raw PII --------------

RAW_SECRETS = ["Ramesh Kumar", "ramesh@example.com", "9845012301", "ABCDE1234F"]


def dirty_ledger():
    b = LedgerBuilder()
    b.ledger.redact = True
    b.step(
        "send_sms",
        {"to": "+91 9845012301", "message": "Ramesh Kumar, your refund is issued"},
        {"name": "Ramesh Kumar", "email": "ramesh@example.com", "pan": "ABCDE1234F"},
        narrative="notified Ramesh Kumar on 9845012301",
    )
    return b.build()


@pytest.mark.parametrize("secret", RAW_SECRETS)
def test_no_raw_pii_survives_in_the_persisted_ledger(secret):
    blob = dirty_ledger().to_jsonl()
    assert secret not in blob, f"{secret!r} leaked into the ledger the checker persists"


def test_pii_spans_are_recorded_as_offsets_not_values():
    led = dirty_ledger()
    assert led.pii_spans_written, "redaction happened but was not accounted for"
    for span in led.pii_spans_written:
        assert {"step", "field", "start", "end", "kind", "tier"} >= set(span)
        assert "text" not in span, "span records must carry offsets, never the value"
        blob = json.dumps(span)
        for secret in RAW_SECRETS:
            assert secret not in blob


def test_redaction_does_not_disturb_the_identity_hashes():
    """Redaction changes stored text; it must not change what identity checks see.

    The binding invariants compare hashes of pre-redaction values. If redaction
    silently rewrote those, every identity check would fail open or closed for
    the wrong reason.
    """
    b = LedgerBuilder()
    b.ledger.redact = True
    b.step("send_sms", {"to": "+919845012301", "message": "hi"}, {})
    led = b.build()
    call = led[0].pending_call

    from controlplane.types import canonical_hash

    assert call.args["to"] != "+919845012301", "the stored value should be redacted"
    assert call.arg_hashes["to"] == canonical_hash("+919845012301"), (
        "the identity hash must still be over the original value"
    )
