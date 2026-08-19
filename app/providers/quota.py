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


@dataclass
class QuotaTracker:
    provider: ProviderName
    weekly_token_budget: int | None = None

    def record(self, *, model: str | None, input_tokens: int, output_tokens: int) -> None:
        if input_tokens == 0 and output_tokens == 0:
            return
        with get_session() as session:
            session.add(
                QuotaUsageLog(
                    provider=self.provider,
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
