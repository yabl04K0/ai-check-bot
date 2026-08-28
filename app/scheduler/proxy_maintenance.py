"""Периодический health-check назначенных прокси + поддержание покрытия
(назначение прокси потребителям, у которых его ещё нет) — см.
app.proxies.health/pool/consumers и запрос пользователя: "если какой-то
упадёт — пусть бот заменит; если не хватает на все акки и апи — пусть
пишет мне"."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from app.db.session import get_session
from app.providers.registry import ProviderRegistry
from app.proxies.alerts import notify_admin
from app.proxies.consumers import active_consumers
from app.proxies.health import run_maintenance
from app.proxies.pool import assign_proxy
from app.proxies.xray_bridge import restart_bridge

logger = logging.getLogger(__name__)

TICK_MINUTES = 20


async def _tick(application: Application) -> None:
    registry: ProviderRegistry = application.bot_data["provider_registry"]
    settings = application.bot_data["settings"]

    with get_session() as session:
        result = run_maintenance(session)

        missing: list[tuple] = []
        for consumer in active_consumers(registry):
            if assign_proxy(session, consumer) is None:
                missing.append((consumer.provider, consumer.account_label))

        # Синхронизирует Xray-мост со свежим статусом (умершие shadowsocks-
        # прокси, только что помеченные DEAD в run_maintenance, выше — не
        # должны продолжать занимать инбаунд после следующей регенерации).
        restart_bridge(session, config_path=settings.db_path.parent / "xray_proxy_bridge.json")

        session.commit()

    for provider, label in result.replaced:
        await notify_admin(
            application, f"🔁 Прокси для {provider.value} ({label}) упал — заменён на другой из пула."
        )
    for provider, label in result.lost_coverage:
        await notify_admin(
            application,
            f"⚠️ Прокси для {provider.value} ({label}) упал, а замены в пуле не нашлось.",
        )
    if missing:
        names = ", ".join(f"{p.value}:{lbl}" for p, lbl in missing)
        await notify_admin(
            application, f"⚠️ Не хватает прокси на все аккаунты/API — без прокси остались: {names}."
        )
    if result.all_dead and result.checked:
        await notify_admin(
            application, "🔴 Все прокси в пуле мертвы — обнови пул из MeCelium (⚙️ Настройки → 🌐 Прокси)."
        )


def register(scheduler: AsyncIOScheduler, application: Application) -> None:
    scheduler.add_job(
        _tick,
        "interval",
        minutes=TICK_MINUTES,
        args=[application],
        id="proxy_maintenance_tick",
        max_instances=1,
        coalesce=True,
    )
