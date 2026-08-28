from __future__ import annotations

from app.db.models import BotSetting, ProviderName
from app.db.session import get_session

_PREFIX = "account_note"


def _key(provider: ProviderName, account_label: str) -> str:
    return f"{_PREFIX}:{provider.value}:{account_label}"


def get_note(provider: ProviderName, account_label: str) -> str | None:
    with get_session() as session:
        row = session.get(BotSetting, _key(provider, account_label))
        return row.value if row and row.value else None


def set_note(provider: ProviderName, account_label: str, text: str) -> None:
    key = _key(provider, account_label)
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is None:
            session.add(BotSetting(key=key, value=text))
        else:
            row.value = text


def clear_note(provider: ProviderName, account_label: str) -> None:
    key = _key(provider, account_label)
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is not None:
            session.delete(row)
