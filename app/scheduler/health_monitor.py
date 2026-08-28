from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from telegram.error import TelegramError
from telegram.ext import Application

from app.providers import circuit_breaker
from app.providers.health_check import NO_ACTIVE_PROBE, probe_account
from app.providers.registry import ProviderRegistry
from app.providers.tiers import TierAccount, all_known_accounts

logger = logging.getLogger(__name__)

_BROKEN: set[tuple] = set()


def _run_probes(registry: ProviderRegistry) -> tuple[list[TierAccount], list[TierAccount]]:
    accounts = [a for a in all_known_accounts(registry) if not registry.is_disabled(a.provider)]
    active_accounts = [a for a in accounts if a.provider not in NO_ACTIVE_PROBE]
    passive_accounts = [a for a in accounts if a.provider in NO_ACTIVE_PROBE]

    def probe(account: TierAccount) -> bool:
        return probe_account(registry, account.provider, account.account_label)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(zip(active_accounts, pool.map(probe, active_accounts), strict=True))

    newly_broken: list[TierAccount] = []
    newly_recovered: list[TierAccount] = []
    for account, ok in results:
        key = (account.provider, account.account_label)
        if not ok and key not in _BROKEN:
            _BROKEN.add(key)
            newly_broken.append(account)
        elif ok and key in _BROKEN:
            _BROKEN.discard(key)
            newly_recovered.append(account)

    for account in passive_accounts:
        key = (account.provider, account.account_label)
        tripped = circuit_breaker.is_open(account.provider, account.account_label)
        if tripped and key not in _BROKEN:
            _BROKEN.add(key)
            newly_broken.append(account)
        elif not tripped and key in _BROKEN:
            _BROKEN.discard(key)

    return newly_broken, newly_recovered


async def check_and_notify(application: Application) -> None:
    settings = application.bot_data.get("settings")
    admin_tg_id = getattr(settings, "admin_tg_id", None)
    if not admin_tg_id:
        return
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    newly_broken, newly_recovered = await asyncio.to_thread(_run_probes, registry)

    for account in newly_broken:
        text = f"🔴 {account.provider.value}:{account.account_label} не отвечает — иишка сейчас не работает."
        try:
            await application.bot.send_message(admin_tg_id, text)
        except TelegramError:
            logger.exception("health_monitor: не удалось отправить уведомление по %s", account)
    for account in newly_recovered:
        text = f"✅ {account.provider.value}:{account.account_label} снова работает."
        try:
            await application.bot.send_message(admin_tg_id, text)
        except TelegramError:
            logger.exception("health_monitor: не удалось отправить уведомление по %s", account)
