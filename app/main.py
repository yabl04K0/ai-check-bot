"""Точка входа: конфиг → БД → бот → планировщик → polling."""

from __future__ import annotations

import logging

from telegram.ext import Application

from app.bot.app import build_application
from app.config import load_settings
from app.db.models import AccountPriority, ProviderName
from app.db.session import get_session, init_db
from app.logging_setup import configure_logging, log_action
from app.providers.tiers import seed_default_tier
from app.proxies.alerts import notify_admin
from app.proxies.xray_bridge import restart_bridge
from app.scheduler.autocheck import start_scheduler
from app.scheduler.proxy_maintenance import register as register_proxy_maintenance
from app.tasks.queue import JobQueue
from app.tasks.types import TASK_TYPE_LABELS

logger = logging.getLogger(__name__)


async def _on_startup(application: Application) -> None:
    # RUNNING/PAUSED_MANUAL в БД с прошлого запуска — их больше некому
    # доводить до конца, а is_busy() видит эти статусы как "занято" и
    # блокирует очередь навсегда, если их не разгрести здесь.
    with get_session() as session:
        orphaned = JobQueue(session).reconcile_orphaned()
    if orphaned:
        orphaned_ids = [j.id for j in orphaned]
        logger.warning("Разобрано %s зависших с прошлого запуска задач: %s", len(orphaned), orphaned_ids)
        # Раньше это было видно только в логе — пользователь мог часами ждать
        # ответа на задачу, которая честно резюмировалась после сброса
        # квоты, работала дальше, но попала под рестарт бота и тихо умерла
        # без единого сообщения в чат (см. живой случай: job #17).
        job_list = "\n".join(
            f"#{j.id} {TASK_TYPE_LABELS.get(j.task_type, j.task_type.value)}" for j in orphaned
        )
        await notify_admin(
            application,
            f"⚠️ При запуске бота прервано {len(orphaned)} зависших задач(и) — процесс, "
            f"который их выполнял, больше не работал:\n{job_list}\n\nЗапусти заново, если нужно.",
        )

    # Xray-мост не переживает рестарт процесса бота (это отдельный
    # subprocess, который никто больше не отслеживает) — поднимаем заново
    # под уже импортированные shadowsocks-прокси, если они есть.
    settings = application.bot_data["settings"]
    with get_session() as session:
        restart_bridge(session, config_path=settings.db_path.parent / "xray_proxy_bridge.json")

    # Одноразовый (см. seed_default_tier — не трогает уже сделанный
    # человеком выбор) сид: claude_code — тир "Глава" по умолчанию, пока
    # человек не переставит руками в ⚙️ Настройки → 🎚 Приоритеты
    # аккаунтов (см. запрос пользователя: "хочу что бы клод коды были
    # главными щас").
    seed_default_tier(ProviderName.CLAUDE_CODE, AccountPriority.HEAD)

    # Планировщик стартует внутри уже работающего event loop PTB — если
    # запустить AsyncIOScheduler до application.run_polling(), он может
    # зацепиться не за тот loop, что реально крутит бота.
    scheduler = start_scheduler(application)
    register_proxy_maintenance(scheduler, application)
    application.bot_data["scheduler"] = scheduler
    log_action("system", "bot_start", "polling запущен")


def main() -> None:
    configure_logging()
    settings = load_settings()
    init_db(settings.db_path)

    application = build_application(settings)
    application.post_init = _on_startup

    logger.info("ai-check-bot стартует (polling)…")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
