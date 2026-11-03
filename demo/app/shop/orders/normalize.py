"""Input normalisation for orders."""

from __future__ import annotations

MAX_NOTE_LENGTH = 500


def normalize_customer_note(note: str | None) -> str | None:
    """Collapse whitespace and cap the length. Missing or blank notes become ``None``."""
    if note is None:
        return None
    cleaned = " ".join(note.split())[:MAX_NOTE_LENGTH]
    return cleaned or None
