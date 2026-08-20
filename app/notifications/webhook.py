"""Опциональный дублирующий канал уведомлений о завершении задачи —
Slack/Discord, оба через простые incoming webhook (без ботов/токенов
этих платформ, просто POST JSON на URL из .env). Telegram остаётся
основным и единственным ОБЯЗАТЕЛЬНЫМ каналом; это — best-effort
дублирование того же текста, которое никогда не должно ломать доставку
в Telegram, даже если webhook не настроен, недоступен или платформа
вернула ошибку."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_SLACK_TEXT_LIMIT = 3900  # Slack режет длинные сообщения примерно на 4000 символов
_DISCORD_CONTENT_LIMIT = 1900  # Discord — жёсткий лимит 2000 символов на content


async def _post_slack(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(webhook_url, json={"text": text[:_SLACK_TEXT_LIMIT]})
        response.raise_for_status()


async def _post_discord(webhook_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(webhook_url, json={"content": text[:_DISCORD_CONTENT_LIMIT]})
        response.raise_for_status()


async def notify_external(
    text: str, *, slack_webhook_url: str | None, discord_webhook_url: str | None
) -> None:
    """Шлёт text в оба канала, если настроены. Ошибка одного канала не
    должна мешать другому — оба best-effort, ни один не поднимает
    исключение наружу (сравни с доставкой в Telegram — та единственная,
    что осталась гарантированной)."""
    if slack_webhook_url:
        try:
            await _post_slack(slack_webhook_url, text)
        except httpx.HTTPError:
            logger.exception("Не удалось отправить уведомление в Slack")
    if discord_webhook_url:
        try:
            await _post_discord(discord_webhook_url, text)
        except httpx.HTTPError:
            logger.exception("Не удалось отправить уведомление в Discord")
