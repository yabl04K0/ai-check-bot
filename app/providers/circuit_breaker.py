from __future__ import annotations

import time

from app.db.models import ProviderName

COOLDOWN_SECONDS = 300

_TRIPPED: dict[tuple[ProviderName, str], float] = {}


def reset() -> None:
    _TRIPPED.clear()


def record_failure(provider: ProviderName, account_label: str) -> None:
    _TRIPPED[(provider, account_label)] = time.monotonic()


def record_success(provider: ProviderName, account_label: str) -> None:
    _TRIPPED.pop((provider, account_label), None)


def is_open(provider: ProviderName, account_label: str) -> bool:
    tripped_at = _TRIPPED.get((provider, account_label))
    if tripped_at is None:
        return False
    if time.monotonic() - tripped_at >= COOLDOWN_SECONDS:
        _TRIPPED.pop((provider, account_label), None)
        return False
    return True
