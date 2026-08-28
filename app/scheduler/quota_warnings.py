from __future__ import annotations

import logging

from telegram.error import TelegramError
from telegram.ext import Application

from app.db.models import ProviderName
from app.providers.quota import account_quota_estimate_for
from app.providers.registry import ProviderRegistry
from app.providers.tiers import AccountPriority, accounts_in_tier

logger = logging.getLogger(__name__)

WARN_THRESHOLD_PCT = 85.0

_WARNED: set[tuple] = set()


async def check_and_warn(application: Application) -> None:
    settings = application.bot_data.get("settings")
    admin_tg_id = getattr(settings, "admin_tg_id", None)
    if not admin_tg_id:
        return
    registry: ProviderRegistry = application.bot_data["provider_registry"]

    for account in accounts_in_tier(AccountPriority.HEAD):
        key = (account.provider, account.account_label)
        if registry.is_disabled(account.provider):
            _WARNED.discard(key)
            continue
        estimate = account_quota_estimate_for(registry, account.provider, account.account_label)
        if estimate.used_pct is None:
            continue
        if estimate.used_pct < WARN_THRESHOLD_PCT:
            _WARNED.discard(key)
            continue
        if key in _WARNED:
            continue
        _WARNED.add(key)

        reset_note = (
            f", сброс примерно через {estimate.hours_to_reset:.0f}ч" if estimate.hours_to_reset else ""
        )
        if estimate.is_estimate:
            source_note = "оценка бота"
        elif account.provider == ProviderName.CLAUDE_CODE and account.account_label == "primary":
            source_note = "🧪 реальные данные, неофициальный эндпоинт"
        else:
            source_note = "реальные данные API"
        text = (
            f"⚠️ Главная нейронка (👑 Глава) {account.provider.value}:{account.account_label} "
            f"израсходовала {estimate.used_pct:.0f}% квоты ({source_note}){reset_note}.\n\n"
            "Если это единственный аккаунт в тире 👑 Глава — добавь ещё один и тоже поставь "
            "ему тир 👑 Глава (🎚 Приоритеты аккаунтов): несколько аккаунтов в одном тире "
            "работают как одна устойчивая нейронка, при исчерпании одного бот сам "
            "переключится на следующего."
        )
        try:
            await application.bot.send_message(admin_tg_id, text)
        except TelegramError:
            logger.exception("quota_warnings: не удалось отправить предупреждение по %s", key)
