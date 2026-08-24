"""Собственная оценка недельного расхода токенов — заменяет отсутствующий
официальный API учёта квоты у Anthropic/OpenAI (см. README).

Провайдер после каждого run_prompt пишет расход в QuotaUsageLog через
record(); estimate() агрегирует последние 7 дней и сравнивает с
WEEKLY_TOKEN_BUDGET, если он задан в .env. Не задан — считаем, что оценка
недоступна (лучше явное "не знаю", чем придуманная цифра).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import ProviderName, QuotaUsageLog
from app.db.session import get_session
from app.providers.base import QuotaEstimate

WEEK = timedelta(days=7)
FIVE_HOURS = timedelta(hours=5)


@dataclass
class QuotaTracker:
    provider: ProviderName
    weekly_token_budget: int | None = None

    def record(
        self,
        *,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        account_label: str | None = None,
    ) -> None:
        if input_tokens == 0 and output_tokens == 0:
            return
        with get_session() as session:
            session.add(
                QuotaUsageLog(
                    provider=self.provider,
                    account_label=account_label,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )

    def estimate(self) -> QuotaEstimate:
        if not self.weekly_token_budget:
            return QuotaEstimate(used_pct=None, hours_to_reset=None)

        since = datetime.now(timezone.utc) - WEEK
        with get_session() as session:
            tokens_sum = func.coalesce(
                func.sum(QuotaUsageLog.input_tokens + QuotaUsageLog.output_tokens), 0
            )
            total = session.scalar(
                select(tokens_sum).where(QuotaUsageLog.provider == self.provider, QuotaUsageLog.ts >= since)
            )
            oldest = session.scalar(
                select(func.min(QuotaUsageLog.ts)).where(
                    QuotaUsageLog.provider == self.provider, QuotaUsageLog.ts >= since
                )
            )

        used_pct = min(100.0, 100.0 * total / self.weekly_token_budget)
        hours_to_reset = None
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            reset_at = oldest + WEEK
            hours_to_reset = max(0.0, (reset_at - datetime.now(timezone.utc)).total_seconds() / 3600)

        return QuotaEstimate(used_pct=used_pct, hours_to_reset=hours_to_reset)


def account_usage_summary(provider: ProviderName) -> dict[str | None, tuple[int, int]]:
    """{account_label: (токенов за 5ч, токенов за 7д)} для конкретного
    провайдера — для UI "лимитов" (⚙️ Настройки/главное меню/прогресс
    задачи). Сырые счётчики того, что бот сам отправил — не % от
    Anthropic (такого API нет ни у claude_code/подписки, ни у большинства
    остальных провайдеров, см. README). account_label=None — записи от
    провайдеров/версий кода без разметки по аккаунту (обратная
    совместимость)."""
    now = datetime.now(timezone.utc)
    since_5h = now - FIVE_HOURS
    since_week = now - WEEK
    tokens_sum = func.coalesce(func.sum(QuotaUsageLog.input_tokens + QuotaUsageLog.output_tokens), 0)

    with get_session() as session:
        rows_5h = session.execute(
            select(QuotaUsageLog.account_label, tokens_sum)
            .where(QuotaUsageLog.provider == provider, QuotaUsageLog.ts >= since_5h)
            .group_by(QuotaUsageLog.account_label)
        ).all()
        rows_week = session.execute(
            select(QuotaUsageLog.account_label, tokens_sum)
            .where(QuotaUsageLog.provider == provider, QuotaUsageLog.ts >= since_week)
            .group_by(QuotaUsageLog.account_label)
        ).all()

    by_label: dict[str | None, tuple[int, int]] = {}
    for label, total in rows_week:
        by_label[label] = (0, total)
    for label, total in rows_5h:
        _, week_total = by_label.get(label, (0, 0))
        by_label[label] = (total, week_total)
    return by_label
