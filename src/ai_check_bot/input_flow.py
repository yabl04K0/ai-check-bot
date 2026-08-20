"""Free-text input after an inline-button prompt ("send me the API key now"). A known
footgun in the sibling bots' own CLAUDE.md: `waiting_for` set without `waiting_for_set_at`
leaves the state stuck forever if the user never replies (or replies days later to a
menu that has moved on). Every waiting_for entry here carries a TTL and is checked on
read, not just on write."""
from __future__ import annotations

import time

from telegram.ext import ContextTypes

WAITING_FOR_KEY = "waiting_for"
WAITING_FOR_SET_AT_KEY = "waiting_for_set_at"
DEFAULT_TTL_SECONDS = 300


def set_waiting(context: ContextTypes.DEFAULT_TYPE, kind: str, **extra: str) -> None:
    context.user_data[WAITING_FOR_KEY] = {"kind": kind, **extra}
    context.user_data[WAITING_FOR_SET_AT_KEY] = time.monotonic()


def pop_waiting(
    context: ContextTypes.DEFAULT_TYPE, *, ttl_seconds: float = DEFAULT_TTL_SECONDS
) -> dict[str, str] | None:
    """Consume and return the pending waiting_for entry, or None if there isn't one or
    it expired. Always clears the state — a caller that gets None must not act."""
    entry = context.user_data.pop(WAITING_FOR_KEY, None)
    set_at = context.user_data.pop(WAITING_FOR_SET_AT_KEY, None)
    if entry is None or set_at is None:
        return None
    if time.monotonic() - set_at > ttl_seconds:
        return None
    return entry
