"""📊 Лимиты (главное меню) + строка в прогрессе задачи — самооценка
расхода токенов за 5ч/неделю по провайдеру и аккаунту (account_label),
см. app.providers.quota.account_usage_summary. Не % от Anthropic (такого
API нет), просто то, что бот сам залогировал."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.bot.formatting import render_progress
from app.db.models import Job, ProviderName, QuotaUsageLog, TaskType
from app.db.session import get_session
from app.providers.quota import QuotaTracker, account_usage_summary


def _log(provider, account_label, input_tokens, output_tokens, hours_ago):
    with get_session() as session:
        session.add(
            QuotaUsageLog(
                provider=provider,
                account_label=account_label,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                ts=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            )
        )


def test_record_stores_account_label(db):
    tracker = QuotaTracker(ProviderName.CLAUDE_CODE)
    tracker.record(model=None, input_tokens=10, output_tokens=5, account_label="extra:1")

    summary = account_usage_summary(ProviderName.CLAUDE_CODE)
    assert summary["extra:1"] == (15, 15)


def test_account_usage_summary_splits_5h_and_week_windows(db):
    _log(ProviderName.CLAUDE_CODE, "primary", 100, 0, hours_ago=1)  # внутри 5ч и недели
    _log(ProviderName.CLAUDE_CODE, "primary", 200, 0, hours_ago=20)  # только неделя
    _log(ProviderName.CLAUDE_CODE, "primary", 400, 0, hours_ago=24 * 10)  # старше недели — не считается

    summary = account_usage_summary(ProviderName.CLAUDE_CODE)

    five_h, week = summary["primary"]
    assert five_h == 100
    assert week == 300


def test_account_usage_summary_separates_accounts(db):
    _log(ProviderName.CLAUDE_CODE, "primary", 50, 0, hours_ago=1)
    _log(ProviderName.CLAUDE_CODE, "extra:1", 30, 0, hours_ago=1)

    summary = account_usage_summary(ProviderName.CLAUDE_CODE)

    assert summary["primary"][0] == 50
    assert summary["extra:1"][0] == 30


def test_account_usage_summary_empty_for_unused_provider(db):
    assert account_usage_summary(ProviderName.GEMINI) == {}


def test_render_progress_includes_limits_line_when_provider_set(db):
    _log(ProviderName.CLAUDE_CODE, "primary", 1500, 500, hours_ago=1)
    job = Job(
        task_type=TaskType.CHECK_FULL,
        progress_step=3,
        progress_total=12,
        progress_label="6. Fleet-checkers",
        provider=ProviderName.CLAUDE_CODE,
    )

    text = render_progress(job)

    assert "claude_code" in text
    assert "primary" in text
    assert "2.0K" in text  # 1500 + 500 округлено


def test_render_progress_no_limits_line_without_provider(db):
    job = Job(task_type=TaskType.CHECK_FULL, progress_step=1, progress_total=4)
    text = render_progress(job)
    assert "💳" not in text


def test_limits_text_shows_usage_per_provider(db):
    from app.bot.handlers.menu import limits_text
    from app.providers.claude_code_cli import ClaudeCodeCliProvider
    from app.providers.registry import ProviderRegistry

    _log(ProviderName.CLAUDE_CODE, "primary", 1000, 0, hours_ago=1)
    registry = ProviderRegistry({ProviderName.CLAUDE_CODE: ClaudeCodeCliProvider("claude")})
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"provider_registry": registry}))

    text = limits_text(context)

    assert "claude_code" in text
    assert "primary" in text


def test_limits_text_reports_empty_state(db):
    from app.bot.handlers.menu import limits_text
    from app.providers.registry import ProviderRegistry

    registry = ProviderRegistry({})
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"provider_registry": registry}))
    text = limits_text(context)
    assert "Пока пусто" in text
