"""notify_external — best-effort дублирование отчёта в Slack/Discord.
Telegram остаётся единственным гарантированным каналом: ошибка в одном
внешнем канале не должна мешать другому, и оба вместе никогда не должны
поднимать исключение наружу (иначе сломали бы доставку в Telegram,
которая в job_runner идёт раньше)."""

from __future__ import annotations

import asyncio

import httpx

from app.notifications.webhook import notify_external


def _run(coro):
    return asyncio.run(coro)


def _fake_post_factory(captured, status_code=200):
    async def fake_post(self, url, **kwargs):
        captured.append((url, kwargs.get("json")))
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, request=request)

    return fake_post


def test_posts_to_slack_with_text_payload(monkeypatch):
    captured = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post_factory(captured))

    _run(
        notify_external(
            "hello", slack_webhook_url="https://hooks.slack.com/x", discord_webhook_url=None
        )
    )

    assert len(captured) == 1
    url, payload = captured[0]
    assert url == "https://hooks.slack.com/x"
    assert payload == {"text": "hello"}


def test_posts_to_discord_with_content_payload(monkeypatch):
    captured = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post_factory(captured))

    _run(
        notify_external(
            "hello", slack_webhook_url=None, discord_webhook_url="https://discord.com/api/webhooks/x"
        )
    )

    assert len(captured) == 1
    url, payload = captured[0]
    assert url == "https://discord.com/api/webhooks/x"
    assert payload == {"content": "hello"}


def test_posts_to_both_when_both_configured(monkeypatch):
    captured = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post_factory(captured))

    _run(
        notify_external(
            "hello",
            slack_webhook_url="https://hooks.slack.com/x",
            discord_webhook_url="https://discord.com/api/webhooks/x",
        )
    )

    assert len(captured) == 2


def test_neither_configured_sends_nothing(monkeypatch):
    captured = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post_factory(captured))

    _run(notify_external("hello", slack_webhook_url=None, discord_webhook_url=None))

    assert captured == []


def test_slack_failure_does_not_stop_discord_from_being_tried(monkeypatch):
    captured = []

    async def fake_post(self, url, **kwargs):
        if "slack" in url:
            raise httpx.ConnectError("slack down")
        captured.append((url, kwargs.get("json")))
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    # не должно поднять исключение наружу
    _run(
        notify_external(
            "hello",
            slack_webhook_url="https://hooks.slack.com/x",
            discord_webhook_url="https://discord.com/api/webhooks/x",
        )
    )

    assert len(captured) == 1
    assert "discord" in captured[0][0]


def test_http_error_status_does_not_raise(monkeypatch):
    async def fake_post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(403, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    # webhook вернул 403 (например, отозванный URL) — не должно упасть
    _run(
        notify_external(
            "hello", slack_webhook_url="https://hooks.slack.com/x", discord_webhook_url=None
        )
    )


def test_long_text_truncated_per_platform_limit(monkeypatch):
    captured = []
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post_factory(captured))

    long_text = "x" * 5000
    _run(
        notify_external(
            long_text,
            slack_webhook_url="https://hooks.slack.com/x",
            discord_webhook_url="https://discord.com/api/webhooks/x",
        )
    )

    by_url = dict(captured)
    assert len(by_url["https://hooks.slack.com/x"]["text"]) <= 3900
    assert len(by_url["https://discord.com/api/webhooks/x"]["content"]) <= 1900
