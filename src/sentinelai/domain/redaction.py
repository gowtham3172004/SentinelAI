"""Redaction of secrets and personal data from telemetry text (NFR-08, design/01 section 5).

Applied at ingest, before anything is stored, and again before any model call. Each rule's
named group ``s`` is the sensitive part. It's replaced with a typed marker
(``[REDACTED:email]``) so investigators can still see *that* something was there.
``redact_text`` is idempotent: redacting already-redacted text changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTION_VERSION = "1"
MAX_ATTRIBUTE_DEPTH = 4


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(
        r"(?P<s>-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)", re.S)),
    ("jwt", re.compile(r"(?P<s>\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})")),
    ("url_credentials", re.compile(r"://(?P<s>[^/\s:@]+:[^/\s@]+)@")),
    ("bearer", re.compile(r"(?i)\bbearer\s+(?P<s>(?!\[REDACTED)[A-Za-z0-9._~+/=-]{8,})")),
    ("api_key", re.compile(
        r"(?P<s>\b(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{20,}"
        r"|AIza[0-9A-Za-z_-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|csk-[a-z0-9]{20,})\b)")),
    ("secret_assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api_key|apikey|access_key)[\"']?\s*[=:]\s*"
        r"[\"']?(?P<s>(?!\[REDACTED)[^\s\"',;&]{3,})")),
    ("email", re.compile(r"(?P<s>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)")),
)  # fmt: skip
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SENSITIVE_KEYS = re.compile(
    r"(?i)(password|passwd|secret|token|authorization|cookie|api[_-]?key|credit|card|ssn)"
)


def _replace_group(kind: str) -> Any:
    def replace(match: re.Match[str]) -> str:
        start, end = match.span("s")
        whole_start = match.start()
        text = match.group(0)
        return text[: start - whole_start] + f"[REDACTED:{kind}]" + text[end - whole_start :]

    return replace


def redact_text(text: str) -> str:
    for kind, pattern in _RULES:
        text = pattern.sub(_replace_group(kind), text)

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "[REDACTED:card]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else match.group(0)

    return _CARD.sub(card, text)


def redact_value(value: Any, depth: int = 0) -> Any:
    """Redact strings inside JSON-like structures; values under sensitive keys are dropped."""
    if isinstance(value, str):
        return redact_text(value)
    if depth >= MAX_ATTRIBUTE_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        return {
            str(k): "[REDACTED:key]"
            if _SENSITIVE_KEYS.search(str(k))
            else redact_value(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(v, depth + 1) for v in value]
    return value
