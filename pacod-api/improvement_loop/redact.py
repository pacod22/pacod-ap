"""Stage 1 — PII redaction (GDPR hard gate).

Conversations can carry customer data. Before ANY analysis model sees a
transcript, identifiers are replaced with stable per-conversation pseudonyms
(`EMAIL_1`, `IBAN_1`, `PHONE_1`, `CARD_1`, `AMOUNT_1`, …) so the analysis stays
coherent (the same id maps to the same token throughout a bundle) without exposing
the raw value. Deterministic regex + a per-call substitution map — no model is
involved in redaction itself, so nothing leaks at this step.

Patterns come in **packs**, chosen per profile (the review-loop setting
``loop.redaction_packs``):

  generic  email, any-country IBAN, payment-card numbers, international phone
           numbers, money amounts tied to a currency
  bg       Bulgarian identifiers: ЕГН (10-digit personal number), ЕИК/БУЛСТАТ
           (9- or 13-digit company number), Bulgarian phone numbers

The goal is best-effort de-identification, not perfect NER; tighten as needed.

**Amount masking is conditional** (`mask_amounts`). The review needs to fact-check
the actual numbers in an answer (a rate, a threshold, a computed figure), so the
text fed to the *analysis model* keeps amounts (`mask_amounts=False`). The text that
gets *persisted* into the loop's own tables (golden_tests, debug rows) keeps the
`AMOUNT_n` mask (`mask_amounts=True`, the default) — so customer figures are not
stored a second time in the review tier. True identifiers are always redacted
regardless of the flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_AMOUNT_LABEL = "AMOUNT"

# Order matters inside the combined list: longer/more-specific identifiers first,
# so e.g. an IBAN's digits are not first eaten by a numeric-id rule.
_PACKS: dict[str, list[tuple[str, re.Pattern]]] = {
    "generic": [
        ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
        # IBAN of any country: 2 letters + 2 check digits + 11..30 alphanumerics.
        ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", re.IGNORECASE)),
        # Payment-card numbers: 13..19 digits, optionally grouped by spaces/dashes.
        ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
        # International phone numbers (+ country code, ≥8 further digits).
        ("PHONE", re.compile(r"(?<![\w])\+\d{1,3}(?:[\s-]?\d){8,12}\b")),
    ],
    "bg": [
        # Bulgarian phone — mobile/landline with a leading 0, ≥9 digits.
        ("PHONE", re.compile(r"(?<![\w])0(?:[\s-]?\d){8,9}\b")),
        # ЕГН — exactly 10 digits (word boundaries so it doesn't match longer runs).
        ("ЕГН", re.compile(r"(?<!\d)\d{10}(?!\d)")),
        # ЕИК/БУЛСТАТ — 13 digits (branch) or 9 digits (base).
        ("ЕИК", re.compile(r"(?<!\d)(?:\d{13}|\d{9})(?!\d)")),
    ],
}

# Money amounts tied to a currency token. Only applied when mask_amounts=True
# (see module docstring) — kept for persisted rows, skipped for the text the
# analysis model fact-checks. Applied after every identifier pack.
_AMOUNT = re.compile(
    r"(?:[€$£]\s?\d[\d\s.,]*|\b\d[\d\s.,]*\s?(?:лв\.?|лева|bgn|€|eur(?:o)?|евро|usd|\$|gbp|£|chf)\b)",
    re.IGNORECASE,
)

PACK_NAMES = tuple(_PACKS)
DEFAULT_PACKS = ("generic",)


# Most specific first: an email or IBAN contains digit runs the other rules would
# claim, and an exact-length national id must win over the generic card rule.
_PRIORITY = ("EMAIL", "IBAN", "ЕГН", "ЕИК", "CARD", "PHONE")


def _patterns(packs: Iterable[str]) -> list[tuple[str, re.Pattern]]:
    chosen = [p for p in dict.fromkeys(packs) if p in _PACKS] or list(DEFAULT_PACKS)
    ordered: list[tuple[str, re.Pattern]] = []
    for pack in chosen:
        ordered.extend(_PACKS[pack])
    ordered.sort(key=lambda x: _PRIORITY.index(x[0]) if x[0] in _PRIORITY else len(_PRIORITY))
    return [*ordered, (_AMOUNT_LABEL, _AMOUNT)]


@dataclass
class RedactionResult:
    text: str
    mapping: dict[str, str]  # token -> original (kept in-process only; never persisted raw)
    counts: dict[str, int]


class Redactor:
    """Per-conversation redactor with stable pseudonyms across a bundle.

    Reuse one instance for all fields of a single conversation so the same
    identifier gets the same token in both the question and the answer.
    """

    def __init__(self, mask_amounts: bool = True, packs: Iterable[str] = DEFAULT_PACKS) -> None:
        self._token_for: dict[str, str] = {}  # original value -> token
        self._counters: dict[str, int] = {}  # label -> next index
        self.mapping: dict[str, str] = {}  # token -> original
        # When False, money amounts are left intact so the analysis model can
        # fact-check the figures; identifiers are still redacted.
        self._mask_amounts = mask_amounts
        self._patterns = _patterns(packs)

    def _assign(self, label: str, value: str) -> str:
        if value in self._token_for:
            return self._token_for[value]
        idx = self._counters.get(label, 0) + 1
        self._counters[label] = idx
        token = f"[{label}_{idx}]"
        self._token_for[value] = token
        self.mapping[token] = value
        return token

    def redact(self, text: str | None) -> str:
        if not text:
            return ""
        out = text
        for label, pattern in self._patterns:
            if label == _AMOUNT_LABEL and not self._mask_amounts:
                continue
            out = pattern.sub(lambda m, _l=label: self._assign(_l, m.group(0)), out)
        return out

    def counts(self) -> dict[str, int]:
        return dict(self._counters)


def redact_bundle_text(
    user_message: str,
    response: str,
    *,
    mask_amounts: bool = True,
    packs: Iterable[str] = DEFAULT_PACKS,
) -> tuple[str, str, dict[str, int]]:
    """Redact a question/answer pair with one shared pseudonym map.

    Returns (redacted_question, redacted_answer, counts). The raw→token mapping is
    intentionally not returned — re-identification is out of scope for the loop.

    `mask_amounts=False` keeps money figures intact (for the analysis-model copy);
    the default masks them (for the copy persisted into the loop tables).
    """
    r = Redactor(mask_amounts=mask_amounts, packs=packs)
    q = r.redact(user_message)
    a = r.redact(response)
    return q, a, r.counts()
