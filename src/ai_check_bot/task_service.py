"""Runs a free-text task (README Task Type 'Кастом') against an automatically picked
account — the actual payoff of providers/router.py's multi-account pooling: the caller
names a provider, not a specific account, and gets routed to whichever account has sat
idle longest."""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from ai_check_bot.providers.base import TaskResult
from ai_check_bot.providers.registry import get_provider
from ai_check_bot.providers.router import pick_account


class NoAccountAvailableError(Exception):
    pass


async def run_custom_task(
    session_factory: sessionmaker, provider_name: str, prompt: str
) -> tuple[str, TaskResult]:
    """Returns (account_label, TaskResult) so the caller can show which account answered.
    The DB session is only held for the fast pick-an-account step, not across the slow
    network call — provider.run_task() runs with no session open."""
    with session_factory() as session:
        account = pick_account(session, provider_name)
        if account is None:
            raise NoAccountAvailableError(f"no enabled '{provider_name}' account configured")
        provider = get_provider(account)
        label = account.label
    result = await provider.run_task(prompt)
    return label, result
