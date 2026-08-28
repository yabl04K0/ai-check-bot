from __future__ import annotations

from app.db.models import BotSetting, ProviderName
from app.db.session import get_session

_EDIT_PREFIX = "agent_can_edit_code"
_PUSH_PREFIX = "agent_can_push_github"
_NATIVE_ALWAYS_PREFIX = "agent_native_always_allowed"


def _get(key: str, *, default: bool) -> bool:
    with get_session() as session:
        row = session.get(BotSetting, key)
        return default if row is None else row.value == "true"


def _set(key: str, enabled: bool) -> None:
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is None:
            session.add(BotSetting(key=key, value="true" if enabled else "false"))
        else:
            row.value = "true" if enabled else "false"


def can_edit_code(provider: ProviderName) -> bool:
    return _get(f"{_EDIT_PREFIX}:{provider.value}", default=True)


def set_can_edit_code(provider: ProviderName, enabled: bool) -> None:
    _set(f"{_EDIT_PREFIX}:{provider.value}", enabled)


def can_push_github(provider: ProviderName) -> bool:
    key = f"{_PUSH_PREFIX}:{provider.value}"
    with get_session() as session:
        row = session.get(BotSetting, key)
    if row is not None:
        return row.value == "true"

    from app.providers.tiers import AccountPriority, accounts_in_tier

    return any(account.provider == provider for account in accounts_in_tier(AccountPriority.HEAD))


def set_can_push_github(provider: ProviderName, enabled: bool) -> None:
    _set(f"{_PUSH_PREFIX}:{provider.value}", enabled)


def native_agent_always_allowed(project_name: str) -> bool:
    return _get(f"{_NATIVE_ALWAYS_PREFIX}:{project_name}", default=False)


def set_native_agent_always_allowed(project_name: str, enabled: bool) -> None:
    _set(f"{_NATIVE_ALWAYS_PREFIX}:{project_name}", enabled)
