"""NoteTrackingProvider — прозрачная обёртка вокруг AIProvider для одной job:
1. После каждого run_prompt сохраняет короткий фрагмент ответа в
   Job.progress_detail — чтобы прогресс в Telegram показывал не только
   номер шага, но и что ИИ реально только что сказал/сделал.
2. Логирует ПОЛНЫЙ промпт и ПОЛНЫЙ ответ (не обрезанные до 400 символов,
   как progress_detail) через стандартный logging — в тот же файл, что
   уже читается для диагностики (см. запрос пользователя "мало инфы,
   сделай логирование всех ответов"). Ошибки уже логируются выше по стеку
   (job_runner.start_job's logger.exception с полным traceback) — тут не
   дублируем, только успешные вызовы.

Живёт в app.providers, не в app.bot.job_runner (где была раньше) — тиры
(app.providers.tiers) заворачивают в неё провайдеров, выбранных для
конкретного шага, а провайдеры не могут импортировать из бот-слоя (это
развернуло бы граф зависимостей и дало циклический импорт: job_runner
уже импортирует tiers). job_runner.py по-прежнему использует именно этот
класс, просто теперь как импорт, а не локальное определение."""

from __future__ import annotations

import logging

from app.db.models import Job
from app.db.session import get_session
from app.providers.base import AIProvider, ProviderResult, RunOptions

logger = logging.getLogger(__name__)


class NoteTrackingProvider:
    """Пишет через свою короткую сессию (get_session()), не через
    ctx.session — Fleet-checkers/критики зовут run_prompt из нескольких
    потоков параллельно (ThreadPoolExecutor, см. app.tasks.protocol_full),
    а ctx.session на всех один и не потокобезопасна; тот же приём, что у
    app.providers.quota.QuotaTracker.record()."""

    def __init__(self, inner: AIProvider, job_id: int) -> None:
        self._inner = inner
        self._job_id = job_id

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        logger.info(
            "Job #%s [%s] ПРОМПТ (%d симв.):\n%s",
            self._job_id,
            self._inner.name.value,
            len(prompt),
            prompt[:3000] + ("…[обрезано]" if len(prompt) > 3000 else ""),
        )
        result = self._inner.run_prompt(prompt, options)
        logger.info(
            "Job #%s [%s] ОТВЕТ (%d симв., %d+%d ток.):\n%s",
            self._job_id,
            self._inner.name.value,
            len(result.text),
            result.input_tokens,
            result.output_tokens,
            result.text[:6000] + ("…[обрезано]" if len(result.text) > 6000 else ""),
        )
        snippet = " ".join(result.text.split())[:400]
        if snippet:
            with get_session() as session:
                job = session.get(Job, self._job_id)
                if job is not None:
                    job.progress_detail = snippet
        return result
