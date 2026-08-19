"""Сборка telegram.ext.Application: bot_data + регистрация всех хендлеров."""

from __future__ import annotations

from telegram.ext import Application

from app.bot.handlers import check, github, menu, projects, registry, settings_admin, start
from app.config import Settings
from app.providers.registry import ProviderRegistry


def build_application(settings: Settings) -> Application:
    application = Application.builder().token(settings.require_bot_token()).build()

    application.bot_data["settings"] = settings
    application.bot_data["provider_registry"] = ProviderRegistry.from_settings(settings)
    application.bot_data["autocheck_enabled_override"] = settings.autocheck.enabled

    for module in (start, menu, projects, check, registry, github, settings_admin):
        module.register(application)

    return application
