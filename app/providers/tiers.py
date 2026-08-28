"""Приоритеты аккаунтов ("👑 Глава" / "⚖️ Средний" / "🤖 Делегация") — какой
именно аккаунт какого провайдера отвечает за какой класс шагов в пайплайне
ЧЕК/Фича/Фикс, когда включён режим делегации (см. запрос пользователя:
"хочу задавать некоторые акки как акки для делегации работы").

Выключено по умолчанию (тот же BotSetting-тумблер-паттерн, что
app.providers.ai_autonomy) — пока выключено, шаги пайплайна используют
ctx.provider как раньше, поведение не меняется вообще. Включено — каждый
шаг просит "дай мне аккаунт тира X" через run_prompt_with_tier(); если
такой аккаунт назначен — вызов идёт именно туда (через
RunOptions.forced_account_label), причём если он принадлежит ДРУГОМУ
провайдеру, чем ctx.provider, вызов уходит в тот провайдер из registry, а
не в ctx.provider. Если под тир ничего не назначено (или назначенный
провайдер отключён) — тихий фолбэк на ctx.provider, прогон никогда не
падает из-за неполной настройки тиров.

Тир "делегация" рассчитан на несколько аккаунтов сразу — TierPicker
раздаёт их round-robin, чтобы N параллельных fleet-checker-доменов
(ThreadPoolExecutor, см. app.tasks.protocol_full.Step6FleetCheckers)
получили N РАЗНЫХ аккаунтов вместо одного и того же на всех — это и есть
смысл делегации, не просто "запасной аккаунт"."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import (
    AccountPriority,
    AccountTierAssignment,
    BotSetting,
    JobAccountTierAssignment,
    ProviderAccountStatus,
    ProviderName,
)
from app.db.session import get_session
from app.providers import circuit_breaker
from app.providers.accounts_store import list_extra_accounts
from app.providers.base import ProviderError, ProviderResult, RunOptions
from app.providers.note_tracking import NoteTrackingProvider
from app.providers.prompt_augment import PromptAugmentProvider
from app.tasks.pipeline import StepContext

_DELEGATION_MODE_KEY = "delegation_mode_enabled"


def delegation_mode_enabled() -> bool:
    with get_session() as session:
        row = session.get(BotSetting, _DELEGATION_MODE_KEY)
        return row is not None and row.value == "true"


def set_delegation_mode(enabled: bool) -> None:
    with get_session() as session:
        row = session.get(BotSetting, _DELEGATION_MODE_KEY)
        if row is None:
            session.add(BotSetting(key=_DELEGATION_MODE_KEY, value="true" if enabled else "false"))
        else:
            row.value = "true" if enabled else "false"


@dataclass(frozen=True)
class TierAccount:
    provider: ProviderName
    account_label: str


def get_tier(provider: ProviderName, account_label: str) -> AccountPriority | None:
    with get_session() as session:
        row = session.scalar(
            select(AccountTierAssignment).where(
                AccountTierAssignment.provider == provider,
                AccountTierAssignment.account_label == account_label,
            )
        )
        return row.priority if row else None


def set_tier(provider: ProviderName, account_label: str, priority: AccountPriority | None) -> None:
    """priority=None снимает назначение (тир "не задан")."""
    with get_session() as session:
        existing = session.scalar(
            select(AccountTierAssignment).where(
                AccountTierAssignment.provider == provider,
                AccountTierAssignment.account_label == account_label,
            )
        )
        if priority is None:
            if existing is not None:
                session.delete(existing)
            return
        if existing is not None:
            existing.priority = priority
        else:
            session.add(
                AccountTierAssignment(provider=provider, account_label=account_label, priority=priority)
            )


def accounts_in_tier(priority: AccountPriority) -> list[TierAccount]:
    with get_session() as session:
        rows = session.scalars(
            select(AccountTierAssignment).where(AccountTierAssignment.priority == priority)
        ).all()
        return [TierAccount(provider=r.provider, account_label=r.account_label) for r in rows]


def all_tier_assignments() -> dict[TierAccount, AccountPriority]:
    with get_session() as session:
        rows = session.scalars(select(AccountTierAssignment)).all()
        return {TierAccount(provider=r.provider, account_label=r.account_label): r.priority for r in rows}


# Общие для Настроек (глобальные тиры) И визарда задачи (per-job оверрайд,
# см. app.bot.handlers.check) — раньше жили приватной копией только в
# settings_admin.py, здесь единственный источник, чтобы экран визарда не
# разъезжался с экраном Настроек в иконках/названиях/порядке цикла.
TIER_ICON = {
    AccountPriority.HEAD: "👑",
    AccountPriority.MEDIUM: "⚖️",
    AccountPriority.DELEGATION: "🤖",
}
TIER_RU_NAME = {
    AccountPriority.HEAD: "Глава",
    AccountPriority.MEDIUM: "Средний",
    AccountPriority.DELEGATION: "Делегация",
}
# Тап по аккаунту двигает его по кругу: не задан → Глава → Средний →
# Делегация → не задан.
TIER_CYCLE: dict[AccountPriority | None, AccountPriority | None] = {
    None: AccountPriority.HEAD,
    AccountPriority.HEAD: AccountPriority.MEDIUM,
    AccountPriority.MEDIUM: AccountPriority.DELEGATION,
    AccountPriority.DELEGATION: None,
}


def job_tier_assignments(job_id: int) -> dict[TierAccount, AccountPriority]:
    """Per-job оверрайд тиров (см. JobAccountTierAssignment) — задан
    визардом задачи (app.bot.handlers.check), НЕ Настройками."""
    with get_session() as session:
        rows = session.scalars(
            select(JobAccountTierAssignment).where(JobAccountTierAssignment.job_id == job_id)
        ).all()
        return {TierAccount(provider=r.provider, account_label=r.account_label): r.priority for r in rows}


def job_has_tier_overrides(job_id: int) -> bool:
    """Есть хоть одна per-job строка -> для ЭТОЙ задачи используем ТОЛЬКО
    её оверрайды (accounts without приоритета этой задачи не участвуют),
    а не глобальные Настройки — см. JobAccountTierAssignment докстринг и
    run_prompt_with_tier ниже."""
    with get_session() as session:
        return (
            session.scalar(
                select(JobAccountTierAssignment.id)
                .where(JobAccountTierAssignment.job_id == job_id)
                .limit(1)
            )
            is not None
        )


def set_job_tier(job_id: int, provider: ProviderName, account_label: str, priority: AccountPriority) -> None:
    """Только для job-визарда: задача только что создаётся (queue.enqueue),
    строк для неё заведомо ещё нет — апсерт/priority=None как в set_tier не
    нужен, экран визарда (_ai_picker_view) просто не шлёт сюда аккаунт без
    выбранного тира."""
    with get_session() as session:
        session.add(
            JobAccountTierAssignment(
                job_id=job_id, provider=provider, account_label=account_label, priority=priority
            )
        )


def call_tier_account(
    priority: AccountPriority, registry, prompt: str, options: RunOptions | None = None
) -> tuple[TierAccount, ProviderResult] | None:
    """Одиночный вызов "дай мне любой аккаунт тира X" вне пайплайна (см.
    app.ai_chat.orchestrator — там нет StepContext/job'ы, только чат) —
    без round-robin (это для одного вызова, не для параллельного
    фан-аута, см. TierPicker) и без NoteTrackingProvider (та обёртка
    пишет в Job.progress_detail, которого у чата нет). None — если под
    тир ничего не назначено или назначенный провайдер отключён;
    вызывающий сам решает, что делать дальше (в чате — сказать об этом
    пользователю, а не падать)."""
    accounts = accounts_in_tier(priority)
    for account in accounts:
        if registry.is_disabled(account.provider):
            continue
        if circuit_breaker.is_open(account.provider, account.account_label):
            continue
        provider = PromptAugmentProvider(
            registry.get(account.provider), force_limits=(priority == AccountPriority.HEAD)
        )
        call_options = RunOptions(
            model=(options.model if options else None),
            system=(options.system if options else None),
            max_tokens=(options.max_tokens if options else RunOptions().max_tokens),
            temperature=(options.temperature if options else RunOptions().temperature),
            forced_account_label=account.account_label,
        )
        try:
            result = provider.run_prompt(prompt, call_options)
        except ProviderError:
            circuit_breaker.record_failure(account.provider, account.account_label)
            continue
        circuit_breaker.record_success(account.provider, account.account_label)
        return account, result
    return None


def all_known_accounts(registry) -> list[TierAccount]:
    """Каждый настроенный аккаунт (primary — если у провайдера вообще есть
    что подключить, плюс все extra) по ВСЕМ провайдерам — для экрана
    "🎚 Приоритеты аккаунтов" в Настройках. Шире, чем
    app.proxies.consumers.PROXIED_PROVIDERS: тиры не завязаны на прокси,
    подойдёт и claude_code без единого прокси в пуле."""
    accounts: list[TierAccount] = []
    for name, provider in registry.all().items():
        if provider.auth_status().status == ProviderAccountStatus.CONNECTED:
            accounts.append(TierAccount(provider=name, account_label="primary"))
        for i, _entry in enumerate(list_extra_accounts(name), start=1):
            accounts.append(TierAccount(provider=name, account_label=f"extra:{i}"))
    return accounts


def seed_default_tier(provider: ProviderName, priority: AccountPriority) -> None:
    """Назначает тир ВСЕМ уже известным аккаунтам провайдера, но только тем,
    у кого тир ещё не задан — не перетирает то, что человек уже настроил
    руками (см. запрос пользователя: "хочу что бы клод коды были главными
    щас" — одноразовый сид при первом включении функции, не насильная
    перезапись при каждом рестарте бота)."""
    with get_session() as session:
        existing_labels = {
            row.account_label
            for row in session.scalars(
                select(AccountTierAssignment).where(AccountTierAssignment.provider == provider)
            ).all()
        }
        labels = ["primary"] + [f"extra:{i}" for i in range(1, len(list_extra_accounts(provider)) + 1)]
        for label in labels:
            if label in existing_labels:
                continue
            session.add(AccountTierAssignment(provider=provider, account_label=label, priority=priority))


class TierPicker:
    """Живёт на один прогон job'ы (см. StepContext.tier_picker) — раздаёт
    аккаунты тира round-robin, чтобы параллельные вызовы (fleet-checkers)
    получали разные аккаунты, а не один и тот же на всех. Потокобезопасна:
    Step6FleetCheckers зовёт pick() из нескольких потоков одновременно
    (ThreadPoolExecutor).

    job_id задан -> источник ТОЛЬКО per-job оверрайды этой задачи
    (JobAccountTierAssignment) — аккаунт без приоритета в НИХ не участвует
    в этом прогоне, даже если у него есть глобальный тир в Настройках.
    job_id=None (по умолчанию) -> как раньше, глобальные Настройки
    (AccountTierAssignment). Кого из двух источников использовать решает
    вызывающий (run_prompt_with_tier), не сам picker."""

    def __init__(self, job_id: int | None = None) -> None:
        if job_id is not None:
            by_tier: dict[AccountPriority, list[TierAccount]] = {
                priority: [] for priority in AccountPriority
            }
            for account, priority in job_tier_assignments(job_id).items():
                by_tier[priority].append(account)
            self._by_tier = by_tier
        else:
            self._by_tier = {priority: accounts_in_tier(priority) for priority in AccountPriority}
        self._cursors: dict[AccountPriority, int] = dict.fromkeys(AccountPriority, 0)
        self._lock = threading.Lock()

    def pick(self, priority: AccountPriority) -> TierAccount | None:
        accounts = self._by_tier.get(priority) or []
        if not accounts:
            return None
        with self._lock:
            idx = self._cursors[priority] % len(accounts)
            self._cursors[priority] += 1
        return accounts[idx]

    def pick_all(self, priority: AccountPriority) -> list[TierAccount]:
        accounts = self._by_tier.get(priority) or []
        if not accounts:
            return []
        with self._lock:
            start = self._cursors[priority] % len(accounts)
            self._cursors[priority] += 1
        return accounts[start:] + accounts[:start]


def run_prompt_with_tier(ctx: StepContext, priority: AccountPriority, prompt: str, options: RunOptions):
    """То, что зовут шаги пайплайна вместо ctx.provider.run_prompt(), когда
    для этого шага задуман конкретный тир (см. модульный докстрин — карта
    шаг→тир зашита в самих Step-классах protocol_full/protocol_lite/generic,
    не здесь). Всегда безопасна: без registry/picker на ctx, без включённого
    режима делегации/оверрайда задачи или без назначенных аккаунтов —
    просто как раньше.

    Per-job оверрайд (см. app.bot.handlers.check, JobAccountTierAssignment)
    ПЕРЕБИВАЕТ глобальный тумблер delegation_mode_enabled: если пользователь
    явно выбрал ИИ для ЭТОЙ задачи в визарде, это работает независимо от
    того, включён ли режим приоритетов в Настройках — выбор для конкретной
    задачи сам по себе однозначное намерение. Без оверрайда — прежнее
    поведение, полностью завязанное на глобальный тумблер."""
    registry = ctx.provider_registry
    if registry is None:
        return ctx.provider.run_prompt(prompt, options)

    job_override = job_has_tier_overrides(ctx.job.id)
    if not job_override and not delegation_mode_enabled():
        return ctx.provider.run_prompt(prompt, options)

    picker = ctx.state.get("_tier_picker")
    if picker is None:
        picker = TierPicker(ctx.job.id if job_override else None)
        ctx.state["_tier_picker"] = picker

    candidates = picker.pick_all(priority)
    for account in candidates:
        if registry.is_disabled(account.provider):
            continue
        if circuit_breaker.is_open(account.provider, account.account_label):
            continue
        tiered_provider = PromptAugmentProvider(
            registry.get(account.provider), force_limits=(priority == AccountPriority.HEAD)
        )
        provider = NoteTrackingProvider(tiered_provider, ctx.job.id)
        tiered_options = RunOptions(
            model=options.model,
            system=options.system,
            max_tokens=options.max_tokens,
            temperature=options.temperature,
            extra=options.extra,
            forced_account_label=account.account_label,
        )
        try:
            result = provider.run_prompt(prompt, tiered_options)
        except ProviderError:
            circuit_breaker.record_failure(account.provider, account.account_label)
            continue
        circuit_breaker.record_success(account.provider, account.account_label)
        return result

    return ctx.provider.run_prompt(prompt, options)
