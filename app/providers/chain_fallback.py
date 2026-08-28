from __future__ import annotations

import logging

from app.db.models import Job, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers import circuit_breaker
from app.providers.base import AIProvider, ProviderError, ProviderResult, RunOptions
from app.providers.registry import ProviderRegistry
from app.providers.router import fallback_chain

logger = logging.getLogger(__name__)

_CIRCUIT_LABEL = "_chain"


class ChainFallbackProvider:
    def __init__(
        self,
        inner: AIProvider,
        registry: ProviderRegistry,
        task_type: TaskType,
        job_id: int | None = None,
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self._registry = registry
        self._task_type = task_type
        self._job_id = job_id

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        if options is not None and options.forced_account_label is not None:
            return self._inner.run_prompt(prompt, options)

        errors: list[tuple[ProviderName, ProviderError]] = []
        try:
            result = self._inner.run_prompt(prompt, options)
            circuit_breaker.record_success(self.name, _CIRCUIT_LABEL)
            return result
        except ProviderError as exc:
            circuit_breaker.record_failure(self.name, _CIRCUIT_LABEL)
            errors.append((self.name, exc))

        for candidate_name in fallback_chain(self._task_type):
            if candidate_name == self.name or self._registry.is_disabled(candidate_name):
                continue
            if circuit_breaker.is_open(candidate_name, _CIRCUIT_LABEL):
                continue
            candidate = self._registry.get(candidate_name)
            if candidate.auth_status().status != ProviderAccountStatus.CONNECTED:
                continue
            logger.warning(
                "Job #%s: %s недоступен/квота исчерпана, пробую %s",
                self._job_id,
                self.name.value,
                candidate_name.value,
            )
            try:
                result = candidate.run_prompt(prompt, options)
            except ProviderError as exc:
                circuit_breaker.record_failure(candidate_name, _CIRCUIT_LABEL)
                errors.append((candidate_name, exc))
                continue
            circuit_breaker.record_success(candidate_name, _CIRCUIT_LABEL)
            self._inner = candidate
            self.name = candidate_name
            self._persist_switch(candidate_name)
            return result

        summary = "; ".join(f"{name.value}: {exc}" for name, exc in errors)
        last_error = errors[-1][1]
        raise type(last_error)(
            f"вся цепочка провайдеров ({len(errors)}) недоступна — {summary}"
        ) from last_error

    def _persist_switch(self, new_name: ProviderName) -> None:
        if self._job_id is None:
            return
        with get_session() as session:
            job = session.get(Job, self._job_id)
            if job is not None:
                job.provider = new_name
