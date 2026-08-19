"""Точка входа: конфиг → БД → бот → планировщик → polling."""

from __future__ import annotations

import logging

from telegram.ext import Application

from app.bot.app import build_application
from app.config import load_settings
from app.db.session import get_session, init_db
from app.logging_setup import configure_logging, log_action
from app.scheduler.autocheck import start_scheduler
from app.tasks.queue import JobQueue

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

    # Планировщик стартует внутри уже работающего event loop PTB — если
    # запустить AsyncIOScheduler до application.run_polling(), он может
    # зацепиться не за тот loop, что реально крутит бота.
    application.bot_data["scheduler"] = start_scheduler(application)
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
