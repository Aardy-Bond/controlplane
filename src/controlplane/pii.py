"""PII detection, span-level.

Two tiers, matching the cost split the policy tiers rely on:

* **tier-1** — deterministic regex/checksum rules. Microseconds. Safe to run
  inline in every tier including the 150ms interactive budget.
* **tier-2** — contextual pass for entities that have no lexical signature
  (person names, postal addresses). Milliseconds.

Tier-2 is a *feature-based contextual detector*, not a neural encoder. It is
deliberately structured behind the same span-returning interface a real encoder
would implement, so swapping in a NER model changes this file and nothing else.
Reported numbers must say which tier produced them; the harness does.

Spans are returned as character offsets so the ledger can store offsets and
hashes rather than raw values (FR-9), and so span-level F1 can be measured with
strict matching (T-601).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["PIISpan", "detect", "redact", "redact_structure"]


@dataclass(frozen=True)
class PIISpan:
    start: int
    end: int
    kind: str
    tier: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "kind": self.kind, "tier": self.tier}


# --------------------------------------------------------------------------
# Tier 1 — deterministic
# --------------------------------------------------------------------------

# Numeric identifiers must not be preceded or followed by identifier characters
# or a hyphen. Without this, POL-100001 reads as a pincode and a policy number
# gets redacted out of the very field an invariant needs it in.
_NB = r"(?<![\w-])"
_NA = r"(?![\w-])"

_TIER1_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    ("aadhaar", re.compile(rf"{_NB}[2-9]\d{{3}}[\s-]?\d{{4}}[\s-]?\d{{4}}{_NA}")),
    ("pan", re.compile(rf"{_NB}[A-Z]{{5}}\d{{4}}[A-Z]{_NA}")),
    ("ifsc", re.compile(rf"{_NB}[A-Z]{{4}}0[A-Z0-9]{{6}}{_NA}")),
    ("phone_in", re.compile(rf"(?:\+91[\s-]?)?{_NB}[6-9]\d{{9}}{_NA}")),
    ("card", re.compile(rf"{_NB}(?:\d{{4}}[\s-]?){{3}}\d{{4}}{_NA}")),
    ("bank_account", re.compile(rf"{_NB}\d{{11,16}}{_NA}")),
    ("dob", re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}\b")),
    ("passport_in", re.compile(rf"{_NB}[A-PR-WY][1-9]\d{{6}}{_NA}")),
]

# Identifiers that look numeric but are business keys, not personal data.
_NOT_PII_CONTEXT = re.compile(
    r"(?:policy|claim|quote|ticket|invoice|order|treaty|contract)[\s_-]*(?:id|no|number|#)?[\s:=]*$",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()][::-1]
    if len(nums) < 12:
        return False
    total = 0
    for i, n in enumerate(nums):
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _tier1(text: str) -> list[PIISpan]:
    spans: list[PIISpan] = []
    for kind, pattern in _TIER1_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(0)
            # Suppress business keys that happen to match a numeric shape.
            if kind in {"bank_account", "card", "phone_in", "aadhaar"}:
                if _NOT_PII_CONTEXT.search(text[max(0, m.start() - 24) : m.start()]):
                    continue
            if kind == "card" and not _luhn_ok(raw):
                continue
            spans.append(PIISpan(m.start(), m.end(), kind, 1, raw))
    return spans


# --------------------------------------------------------------------------
# Tier 2 — contextual
# --------------------------------------------------------------------------

_TITLES = r"(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sri)\.?"
_NAME_CUE = re.compile(
    rf"(?:{_TITLES}\s+|"
    r"(?:policyholder|customer|claimant|insured|beneficiary|nominee|employee|"
    r"patient|applicant|holder|name)\s*(?:is|:|=)?\s+)"
    r"([A-Z][a-z]{1,15}(?:\s+[A-Z][a-z]{1,15}){0,2})"
)
_BARE_NAME = re.compile(r"\b([A-Z][a-z]{2,15}\s+[A-Z][a-z]{2,15})\b")
_ADDRESS = re.compile(
    r"\b\d{1,4}[/-]?[A-Z]?,?\s+(?:[A-Z][a-z]+\s+){1,4}"
    r"(?:Road|Rd|Street|St|Marg|Nagar|Colony|Layout|Sector|Lane|Avenue|Cross)\b"
    r"(?:,?\s*[A-Z][a-z]+)?(?:,?\s*\d{6})?",
)
_PINCODE = re.compile(rf"{_NB}[1-9]\d{{5}}{_NA}")

# Capitalised tokens that are organisations, products or places, not people.
_NAME_STOPLIST = {
    "Policy",
    "Claim",
    "Meridian",
    "Insurance",
    "Health",
    "Motor",
    "Life",
    "Term",
    "Plan",
    "Premium",
    "Refund",
    "Quarter",
    "North",
    "South",
    "East",
    "West",
    "Zone",
    "Region",
    "Portfolio",
    "Loss",
    "Ratio",
    "Rate",
    "Model",
    "Report",
    "Memo",
    "Section",
    "Table",
    "Data",
    "Source",
    "System",
    "Error",
    "Access",
    "Denied",
    "Not",
    "Found",
    "Sales",
    "Engineering",
    "Finance",
    "Legal",
    "Operations",
    "Underwriting",
}


def _looks_like_person(candidate: str) -> bool:
    parts = candidate.split()
    return bool(parts) and not any(p in _NAME_STOPLIST for p in parts)


def _tier2(text: str) -> list[PIISpan]:
    spans: list[PIISpan] = []
    for m in _NAME_CUE.finditer(text):
        cand = m.group(1)
        if _looks_like_person(cand):
            spans.append(PIISpan(m.start(1), m.end(1), "person_name", 2, cand))
    for m in _BARE_NAME.finditer(text):
        cand = m.group(1)
        if _looks_like_person(cand):
            spans.append(PIISpan(m.start(1), m.end(1), "person_name", 2, cand))
    for m in _ADDRESS.finditer(text):
        spans.append(PIISpan(m.start(), m.end(), "address", 2, m.group(0)))
    for m in _PINCODE.finditer(text):
        spans.append(PIISpan(m.start(), m.end(), "pincode", 2, m.group(0)))
    return spans


def _dedupe(spans: list[PIISpan]) -> list[PIISpan]:
    """Drop spans fully contained in a longer span, preferring the lower tier."""
    ordered = sorted(spans, key=lambda s: (s.start, -(s.end - s.start), s.tier))
    kept: list[PIISpan] = []
    for span in ordered:
        if any(k.start <= span.start and span.end <= k.end for k in kept):
            continue
        kept.append(span)
    return kept


def detect(text: str, max_tier: int = 2) -> list[PIISpan]:
    """Return PII spans in ``text``, sorted by position."""
    if not text:
        return []
    spans = _tier1(text)
    if max_tier >= 2:
        spans += _tier2(text)
    return _dedupe(spans)


def redact(text: str, max_tier: int = 2) -> tuple[str, list[PIISpan]]:
    """Replace detected spans with typed placeholders, preserving structure."""
    spans = detect(text, max_tier)
    if not spans:
        return text, []
    out, cursor = [], 0
    for span in spans:
        out.append(text[cursor : span.start])
        out.append(f"[{span.kind.upper()}]")
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out), spans


# Structured fields whose values are personal data regardless of their content.
_SENSITIVE_KEYS = {
    "name",
    "full_name",
    "customer_name",
    "policyholder",
    "policyholder_name",
    "holder_name",
    "email",
    "phone",
    "mobile",
    "address",
    "dob",
    "date_of_birth",
    "aadhaar",
    "pan",
    "account_number",
    "salary",
    "compensation",
    "ctc",
}


def redact_structure(value: Any, max_tier: int = 2) -> tuple[Any, list[PIISpan]]:
    """Recursively redact a JSON-like structure for ledger write (FR-9)."""
    found: list[PIISpan] = []

    def walk(node: Any, key_hint: str = "") -> Any:
        if isinstance(node, dict):
            return {k: walk(v, str(k).lower()) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key_hint) for v in node]
        if isinstance(node, str):
            if key_hint in _SENSITIVE_KEYS:
                found.append(PIISpan(0, len(node), key_hint, 1, node))
                return f"[{key_hint.upper()}]"
            cleaned, spans = redact(node, max_tier)
            found.extend(spans)
            return cleaned
        if key_hint in _SENSITIVE_KEYS and isinstance(node, int | float):
            found.append(PIISpan(0, 0, key_hint, 1, str(node)))
            return f"[{key_hint.upper()}]"
        return node

    return walk(value), found
