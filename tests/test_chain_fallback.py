"""ChainFallbackProvider — прозрачная обёртка для фолбэка между провайдерами при
исчерпании квоты одного (см. app.providers.chain_fallback)."""

from __future__ import annotations

import pytest

from app.db.models import Job, ProviderAccountStatus, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import (
    AuthStatus,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderQuotaExceededError,
    ProviderResult,
    RunOptions,
)
from app.providers.chain_fallback import ChainFallbackProvider


class _FakeProvider:
    """Макет провайдера для тестирования."""

    def __init__(
        self,
        name: ProviderName,
        *,
        text: str | None = None,
        error: ProviderError | None = None,
        connected: bool = True,
    ):
        self.name = name
        self._text = text
        self._error = error  # инстанс исключения, которое нужно поднять, или None
        self._connected = connected
        self.calls: list[RunOptions | None] = []

    def auth_status(self) -> AuthStatus:
        """Возвращает статус подключения."""
        status = ProviderAccountStatus.CONNECTED if self._connected else ProviderAccountStatus.NOT_CONNECTED
        return AuthStatus(status=status)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        """Синхронный запуск промпта."""
        self.calls.append(options)
        if self._error is not None:
            raise self._error
        return ProviderResult(text=self._text or "")


class _FakeRegistry:
    """Макет реестра провайдеров."""

    def __init__(
        self,
        providers: dict[ProviderName, _FakeProvider],
        disabled: frozenset[ProviderName] | None = None,
    ):
        self._providers = providers
        self._disabled = disabled or frozenset()

    def get(self, name: ProviderName) -> _FakeProvider:
        return self._providers[name]

    def is_disabled(self, name: ProviderName) -> bool:
        return name in self._disabled


def _make_job(db) -> int:
    """Создаёт заглушку Job в тестовой БД и возвращает её id."""
    with get_session() as session:
        job = Job(task_type=TaskType.CHECK_FULL)
        session.add(job)
        session.flush()
        return job.id


def test_first_provider_success_no_fallback(db):
    """Первый провайдер в цепочке успешен — фолбэка не происходит,
    результат от него, имя провайдера-обёртки не меняется."""
    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        text="результат от claude_code",
    )
    # Остальные провайдеры будут с явной ошибкой, если их вдруг вызовут
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        error=AssertionError("SHOULD NOT BE CALLED — claude"),
    )
    codex = _FakeProvider(
        ProviderName.CODEX,
        error=AssertionError("SHOULD NOT BE CALLED — codex"),
    )

    registry = _FakeRegistry({
        ProviderName.CLAUDE_CODE: claude_code,
        ProviderName.CLAUDE: claude,
        ProviderName.CODEX: codex,
    })

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
    )

    result = chain_provider.run_prompt("test prompt")

    assert result.text == "результат от claude_code"
    assert chain_provider.name == ProviderName.CLAUDE_CODE
    assert len(claude_code.calls) == 1
    assert len(claude.calls) == 0
    assert len(codex.calls) == 0


def test_falls_back_to_next_connected_provider_on_quota_error(db):
    """CLAUDE_CODE поднимает ProviderQuotaExceededError, CLAUDE (следующий в цепочке
    и подключён) успешен. Результат от CLAUDE, имя обёртки обновляется на CLAUDE."""
    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        error=ProviderQuotaExceededError("no quota"),
    )
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        text="from claude",
        connected=True,
    )
    codex = _FakeProvider(
        ProviderName.CODEX,
        error=AssertionError("SHOULD NOT BE CALLED — codex"),
    )

    registry = _FakeRegistry({
        ProviderName.CLAUDE_CODE: claude_code,
        ProviderName.CLAUDE: claude,
        ProviderName.CODEX: codex,
    })

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
    )

    result = chain_provider.run_prompt("test prompt")

    assert result.text == "from claude"
    assert chain_provider.name == ProviderName.CLAUDE
    assert len(claude_code.calls) == 1
    assert len(claude.calls) == 1
    assert len(codex.calls) == 0


def test_skips_disabled_and_non_connected_providers(db):
    """CLAUDE_CODE поднимает квоту-ошибку. CLAUDE отключён (disabled=...).
    CODEX не подключён (connected=False). CURSOR подключён и успешен.
    Результат от CURSOR, CLAUDE и CODEX не вызывались."""
    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        error=ProviderQuotaExceededError("no quota"),
    )
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        error=AssertionError("SHOULD NOT BE CALLED — claude disabled"),
    )
    codex = _FakeProvider(
        ProviderName.CODEX,
        error=AssertionError("SHOULD NOT BE CALLED — codex not connected"),
        connected=False,  # явно не подключён
    )
    cursor = _FakeProvider(
        ProviderName.CURSOR,
        text="from cursor",
        connected=True,
    )

    registry = _FakeRegistry(
        {
            ProviderName.CLAUDE_CODE: claude_code,
            ProviderName.CLAUDE: claude,
            ProviderName.CODEX: codex,
            ProviderName.CURSOR: cursor,
        },
        disabled=frozenset([ProviderName.CLAUDE]),  # CLAUDE отключён вручную
    )

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
    )

    result = chain_provider.run_prompt("test prompt")

    assert result.text == "from cursor"
    assert chain_provider.name == ProviderName.CURSOR
    assert len(claude_code.calls) == 1
    assert len(claude.calls) == 0  # пропущен: disabled
    assert len(codex.calls) == 0  # пропущен: not connected
    assert len(cursor.calls) == 1


def test_forced_account_label_disables_chain_fallback(db):
    """Если RunOptions.forced_account_label установлен, фолбэк отключается.
    Ошибка от CLAUDE_CODE должна пройти насквозь БЕЗ попытки других провайдеров."""
    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        error=ProviderQuotaExceededError("forced call — no fallback allowed"),
    )
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        error=AssertionError("SHOULD NOT BE CALLED — forced call skips fallback"),
    )
    codex = _FakeProvider(
        ProviderName.CODEX,
        error=AssertionError("SHOULD NOT BE CALLED — forced call skips fallback"),
    )

    registry = _FakeRegistry({
        ProviderName.CLAUDE_CODE: claude_code,
        ProviderName.CLAUDE: claude,
        ProviderName.CODEX: codex,
    })

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
    )

    options = RunOptions(forced_account_label="extra:1")

    with pytest.raises(ProviderQuotaExceededError):
        chain_provider.run_prompt("test prompt", options)

    assert len(claude_code.calls) == 1
    assert len(claude.calls) == 0
    assert len(codex.calls) == 0


def test_all_providers_exhausted_raises_last_error_type(db):
    """Все 12 провайдеров в CHECK_FULL цепочке поднимают ошибки.
    Последний (FIREWORKS) поднимает ProviderNotAuthenticatedError.
    Обёртка должна поднять ProviderNotAuthenticatedError, а не
    ProviderQuotaExceededError, так как это тип последней ошибки в цепочке."""
    # Все провайдеры в цепочке CHECK_FULL (в порядке приоритета):
    # CLAUDE_CODE, CLAUDE, CODEX, CURSOR, GEMINI, DEEPSEEK, GROK,
    # MISTRAL, OPENROUTER, TOGETHER, PERPLEXITY, FIREWORKS
    providers = {}
    provider_list = [
        ProviderName.CLAUDE_CODE,
        ProviderName.CLAUDE,
        ProviderName.CODEX,
        ProviderName.CURSOR,
        ProviderName.GEMINI,
        ProviderName.DEEPSEEK,
        ProviderName.GROK,
        ProviderName.MISTRAL,
        ProviderName.OPENROUTER,
        ProviderName.TOGETHER,
        ProviderName.PERPLEXITY,
        ProviderName.FIREWORKS,
    ]

    for i, provider_name in enumerate(provider_list):
        if i < len(provider_list) - 1:
            # Все, кроме последнего — ProviderQuotaExceededError
            error = ProviderQuotaExceededError(f"quota exhausted in {provider_name.value}")
        else:
            # Последний — ProviderNotAuthenticatedError
            error = ProviderNotAuthenticatedError(f"no key for {provider_name.value}")
        providers[provider_name] = _FakeProvider(provider_name, error=error)

    registry = _FakeRegistry(providers)

    chain_provider = ChainFallbackProvider(
        inner=providers[ProviderName.CLAUDE_CODE],
        registry=registry,
        task_type=TaskType.CHECK_FULL,
    )

    # Должен поднять ProviderNotAuthenticatedError (тип последней ошибки)
    with pytest.raises(ProviderNotAuthenticatedError) as exc_info:
        chain_provider.run_prompt("test prompt")

    # Проверяем, что сообщение содержит информацию об исчерпанности цепочки
    error_msg = str(exc_info.value)
    assert "вся цепочка провайдеров" in error_msg or "12" in error_msg


def test_switch_persists_job_provider_in_db(db):
    """Когда провайдер переключается и передан job_id, имя провайдера
    должно персистентно записаться в Job.provider в БД."""
    job_id = _make_job(db)

    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        error=ProviderQuotaExceededError("no quota"),
    )
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        text="from claude",
        connected=True,
    )

    registry = _FakeRegistry({
        ProviderName.CLAUDE_CODE: claude_code,
        ProviderName.CLAUDE: claude,
    })

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
        job_id=job_id,
    )

    result = chain_provider.run_prompt("test prompt")
    assert result.text == "from claude"

    # Открываем новую сессию и перепроверяем, что Job.provider обновился
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.provider == ProviderName.CLAUDE


def test_job_id_none_does_not_crash_on_switch(db):
    """Когда job_id=None, переключение провайдера не должно крашнуть
    (just early-return в _persist_switch)."""
    claude_code = _FakeProvider(
        ProviderName.CLAUDE_CODE,
        error=ProviderQuotaExceededError("no quota"),
    )
    claude = _FakeProvider(
        ProviderName.CLAUDE,
        text="from claude",
        connected=True,
    )

    registry = _FakeRegistry({
        ProviderName.CLAUDE_CODE: claude_code,
        ProviderName.CLAUDE: claude,
    })

    chain_provider = ChainFallbackProvider(
        inner=claude_code,
        registry=registry,
        task_type=TaskType.CHECK_FULL,
        job_id=None,  # явно None
    )

    result = chain_provider.run_prompt("test prompt")
    assert result.text == "from claude"
    assert chain_provider.name == ProviderName.CLAUDE
