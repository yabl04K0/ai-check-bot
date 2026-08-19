"""Сборка telegram.ext.Application: bot_data + регистрация всех хендлеров."""

from __future__ import annotations

from telegram.ext import Application

from app.bot import access_control
from app.bot.error_handler import handle_error
from app.bot.handlers import check, github, menu, projects, registry, settings_admin, start
from app.config import Settings
from app.providers.registry import ProviderRegistry


def build_application(settings: Settings) -> Application:
    application = Application.builder().token(settings.require_bot_token()).build()

    application.bot_data["settings"] = settings
    application.bot_data["provider_registry"] = ProviderRegistry.from_settings(settings)
    application.bot_data["autocheck_enabled_override"] = settings.autocheck.enabled

    application.add_error_handler(handle_error)

    # Гейт доступа — первым, ниже всех остальных групп (см. GATE_GROUP):
    # не-владельца бот дальше не пускает ни к одному хендлеру ниже.
    access_control.register(application)

    for module in (start, menu, projects, check, registry, github, settings_admin):
        module.register(application)

    return application
