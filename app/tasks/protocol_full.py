"""🔴 Full ЧЕК — CHEK_PROTOCOL.md, 13 шагов (см. README и backend-architecture.mermaid).

Провайдер для каждого шага приходит из ctx.provider — единственный,
выбранный роутером на весь прогон (никакого хардкода Claude/Opus/Sonnet
внутри шагов; какая именно модель используется — решает provider.run_prompt
через RunOptions.model, если понадобится тонкая настройка позже) — ЕСЛИ
режим приоритетов аккаунтов выключен (см. app.providers.tiers). Включён —
шаги ниже, помеченные конкретным AccountPriority, просят
run_prompt_with_tier() направить вызов на аккаунт нужного тира (тихий
фолбэк на ctx.provider, если под тир ничего не назначено).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.db.models import ProviderMode
from app.providers import circuit_breaker
from app.providers.base import ProviderError, RunOptions
from app.providers.quota import account_quota_estimate_for
from app.providers.tiers import (
    AccountPriority,
    accounts_in_tier,
    delegation_mode_enabled,
    job_has_tier_overrides,
    job_tier_assignments,
    run_prompt_with_tier,
)
from app.registry_store.store import RegistryFinding, register_or_bump_finding
from app.scheduler.quota_warnings import WARN_THRESHOLD_PCT
from app.tasks import project_context as ctxdata
from app.tasks import scope as scope_util
from app.tasks.findings_parse import parse_structured_findings
from app.tasks.pipeline import Step, StepContext

MAX_CONVERGENCE_ROUNDS = 3
FLEET_CHECKER_DOMAINS_DEFAULT = 4


def _project_summary(ctx: StepContext) -> str:
    names = ", ".join(f"{p.name} ({p.repo_full_name})" for p in ctx.projects)
    scope = ctx.scope or "всё"
    comment = ctx.comment or "(без комментария)"
    return f"Проект(ы): {names}\nСкоуп: {scope}\nКомментарий: {comment}"


class Step1to4Registry(Step):
    label = "1-4. Реестр открытых проблем, тесты, логи, sweep"

    def run(self, ctx: StepContext) -> None:
        path_filter = scope_util.path_filter(ctx.scope)
        parts = []
        for project in ctx.projects:
            parts.append(f"### {project.name}")
            parts.append(
                "**Архитектура и история (PROJECT_MEMORY.md):**\n" + ctxdata.gather_project_memory(project)
            )
            parts.append(
                "**Продолжение с прошлой сессии (LAST_PROMPT.md):**\n" + ctxdata.gather_last_prompt(project)
            )
            parts.append("**Реестр:**\n" + ctxdata.gather_registry(project))
            parts.append("**Тесты:**\n" + ctxdata.gather_tests(project))
            parts.append("**Логи:**\n" + ctxdata.gather_logs(project))
            sweep_text = ctxdata.sweep(project, path_filter=path_filter)
            sweep_scope_note = f", путь: {path_filter}" if path_filter else ""
            parts.append(f"**Sweep (TODO/FIXME/XXX/HACK{sweep_scope_note}):**\n{sweep_text}")
        ctx.state["intake"] = "\n\n".join(parts)


class Step4bWebResearch(Step):
    label = "4b. Web-research (по триггеру)"

    TRIGGER_WORDS = ("web", "research", "исследуй", "погугли")

    def run(self, ctx: StepContext) -> None:
        comment = (ctx.comment or "").lower()
        if not any(word in comment for word in self.TRIGGER_WORDS):
            ctx.state["web_research"] = None
            return
        prompt = (
            "На основе комментария задачи оцени, какие внешние знания "
            "(библиотеки/CVE/best practices) стоит учесть перед аудитом. "
            f"Комментарий: {ctx.comment}"
        )
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — ресёрчер перед аудитом кода."))
        ctx.state["web_research"] = result.text


PLAN_APPROVAL_OK_WORDS = {"да", "ок", "окей", "yes", "ok", "+", "го", "давай"}


def _delegation_budget(ctx: StepContext) -> tuple[int, int] | None:
    registry = ctx.provider_registry
    if registry is None:
        return None
    job_override = job_has_tier_overrides(ctx.job.id)
    if not job_override and not delegation_mode_enabled():
        return None

    if job_override:
        accounts = [a for a, p in job_tier_assignments(ctx.job.id).items() if p == AccountPriority.DELEGATION]
    else:
        accounts = accounts_in_tier(AccountPriority.DELEGATION)
    if not accounts:
        return None

    usable = 0
    for account in accounts:
        if registry.is_disabled(account.provider):
            continue
        if circuit_breaker.is_open(account.provider, account.account_label):
            continue
        estimate = account_quota_estimate_for(registry, account.provider, account.account_label)
        if estimate.used_pct is not None and estimate.used_pct >= WARN_THRESHOLD_PCT:
            continue
        usable += 1
    return usable, len(accounts)


class Step5FleetPlanner(Step):
    label = "5. Fleet-planner — делит проект на домены"

    def run(self, ctx: StepContext) -> None:
        target_domains = FLEET_CHECKER_DOMAINS_DEFAULT
        budget_note = ""
        budget = _delegation_budget(ctx)
        if budget is not None:
            usable, total = budget
            if usable < FLEET_CHECKER_DOMAINS_DEFAULT:
                target_domains = max(1, usable)
                budget_note = (
                    f"Внимание: из {total} аккаунтов делегации сейчас реально доступно "
                    f"{usable} (лимит/cooldown) — урезаю флот до {target_domains}."
                )

        prompt = (
            f"{_project_summary(ctx)}\n\n"
            f"Контекст:\n{ctx.state.get('intake', '')}\n\n"
            f"Раздели аудит на {target_domains} независимых доменов "
            "(например: auth, api, db, frontend — под конкретный проект). "
            f"{budget_note} "
            "Ответь построчно, только названия доменов, без нумерации и пояснений."
        )
        result = run_prompt_with_tier(
            ctx, AccountPriority.HEAD, prompt, RunOptions(system="Ты — fleet-planner для аудита кода.")
        )
        domains = [line.strip("- ").strip() for line in result.text.splitlines() if line.strip()]
        domains = domains[:target_domains] or ["general"]
        if budget_note:
            ctx.state["fleet_budget_note"] = budget_note

        if ctx.job.provider_mode == ProviderMode.MANUAL:
            question = (
                f"План аудита — {len(domains)} доменов: {', '.join(domains)}."
                f"{(' ' + budget_note) if budget_note else ''}\n"
                "Ответь 'да' чтобы запустить флот, или пришли свой список доменов "
                "через запятую чтобы изменить план."
            )
            answer = ctx.ask_user(question)
            if answer and answer.strip().lower() not in PLAN_APPROVAL_OK_WORDS:
                edited = [d.strip() for d in answer.split(",") if d.strip()]
                if edited:
                    domains = edited[:FLEET_CHECKER_DOMAINS_DEFAULT]

        ctx.state["domains"] = domains


class Step6FleetCheckers(Step):
    label = "6. Fleet-checkers ×N параллельно, read-only"

    def run(self, ctx: StepContext) -> None:
        domains: list[str] = ctx.state.get("domains", ["general"])

        def check_domain(domain: str) -> tuple[str, str]:
            prompt = (
                f"{_project_summary(ctx)}\n\nДомен: {domain}\n\n"
                f"Контекст:\n{ctx.state.get('intake', '')}\n\n"
                "Проведи READ-ONLY аудит этого домена: найди баги, риски, "
                "нарушения инвариантов. Список находок с severity "
                "(critical/high/medium) и кратким описанием."
            )
            result = run_prompt_with_tier(
                ctx,
                AccountPriority.DELEGATION,
                prompt,
                RunOptions(system="Ты — fleet-checker, только чтение."),
            )
            return domain, result.text

        with ThreadPoolExecutor(max_workers=min(len(domains), 4)) as pool:
            reports = dict(pool.map(check_domain, domains))
        ctx.state["checker_reports"] = reports


class Step7Aggregation(Step):
    label = "7. Агрегация + coverage-check"

    def run(self, ctx: StepContext) -> None:
        reports: dict[str, str] = ctx.state.get("checker_reports", {})
        aggregated = "\n\n".join(f"## {domain}\n{report}" for domain, report in reports.items())
        ctx.state["aggregated_report"] = aggregated
        ctx.state["coverage"] = {"domains_covered": len(reports), "domains_total": len(reports)}


class Step8GapFinder(Step):
    label = "8. Gap-finder"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            "Вот агрегированный отчёт fleet-checkers:\n"
            f"{ctx.state.get('aggregated_report', '')}\n\n"
            "Найди пробелы: что могли упустить checkers (edge cases, "
            "не покрытые домены, скрытые зависимости между находками)."
        )
        result = run_prompt_with_tier(
            ctx, AccountPriority.HEAD, prompt, RunOptions(system="Ты — gap-finder, ищешь пропущенное.")
        )
        ctx.state["gaps"] = result.text


class Step8bRegisterFindings(Step):
    """Дописывает находки в chek_open.md проекта — до этого шага Full ЧЕК
    только генерировал текст отчёта, реестр (📜 в боте) оставался пустым
    даже после реального прогона."""

    label = "8b. Регистрация находок в chek_open.md"

    def run(self, ctx: StepContext) -> None:
        ignore_deferred = scope_util.is_ignore_registry(ctx.scope)
        project_names = ", ".join(p.name for p in ctx.projects)
        deferred_instruction = (
            "Скоуп 'ЧЕК всё' — включай в список ВСЕ находки, даже если они уже "
            "числятся в Отложено/Never в контексте выше."
            if ignore_deferred
            else "Находки, уже помеченные [LATER] или [NEVER] в контексте выше, "
            "заново НЕ включай — человек уже принял по ним решение."
        )
        prompt = (
            f"Отчёт аудита:\n{ctx.state.get('aggregated_report', '')}\n\n"
            f"Пробелы:\n{ctx.state.get('gaps', '')}\n\n"
            f"Реестр проекта (см. [OPEN]/[LATER]/[NEVER]):\n{ctx.state.get('intake', '')}\n\n"
            f"Проекты в этом прогоне: {project_names}\n\n"
            f"{deferred_instruction}\n\n"
            "Составь список находок для реестра. Ответь СТРОГО по одной "
            "находке на строку в формате:\n"
            "severity|project|file::symbol|краткое описание\n"
            "где severity — critical/high/medium, project — точное имя "
            "одного из проектов выше, file::symbol — файл и место в коде "
            "через '::'. Без заголовков, нумерации, markdown и пояснений "
            "вне этого формата — только сами строки."
        )
        result = ctx.provider.run_prompt(
            prompt, RunOptions(system="Ты составляешь реестр находок в строгом машиночитаемом формате.")
        )
        parsed = parse_structured_findings(result.text)
        projects_by_name = {p.name: p for p in ctx.projects}

        outcomes = {"new": 0, "bumped": 0, "deferred_skipped": 0, "moved_from_deferred": 0}
        skipped_no_path = 0
        for pf in parsed:
            project = projects_by_name.get(pf.project_name)
            path = ctxdata.local_path(project) if project else None
            if path is None:
                skipped_no_path += 1
                continue
            outcome = register_or_bump_finding(
                path,
                RegistryFinding(file_symbol=pf.file_symbol, description=pf.description, severity=pf.severity),
                ignore_deferred=ignore_deferred,
            )
            outcomes[outcome] += 1

        ctx.state["findings_registered"] = outcomes["new"] + outcomes["moved_from_deferred"]
        ctx.state["findings_bumped"] = outcomes["bumped"]
        ctx.state["findings_deferred_skipped"] = outcomes["deferred_skipped"]
        ctx.state["findings_skipped"] = skipped_no_path


class Step9Fixer(Step):
    label = "9. Fixer"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            "Находки аудита:\n"
            f"{ctx.state.get('aggregated_report', '')}\n\n"
            "Пробелы:\n"
            f"{ctx.state.get('gaps', '')}\n\n"
            "Предложи фиксы: для каждой находки — конкретный diff/патч-текст "
            "и краткое объяснение. Фикс НЕ применяется на диск автоматически — "
            "только текст патча для показа пользователю на Step 13."
        )
        result = run_prompt_with_tier(
            ctx, AccountPriority.MEDIUM, prompt, RunOptions(system="Ты — fixer, предлагаешь патчи.")
        )
        ctx.state["fix_proposal"] = result.text


CRITIC_A_FOCUS = "корректность и регрессии"
CRITIC_B_FOCUS = "безопасность и производительность"
CRITIC_A_TIER = AccountPriority.HEAD
CRITIC_B_TIER = AccountPriority.MEDIUM


def _run_critic(
    ctx: StepContext, focus: str, tier: AccountPriority, fix: str, other_opinion: str | None = None
) -> str:
    exchange = (
        f"\n\nМнение другого критика в прошлом раунде (другой фокус, не обязан совпадать):\n"
        f"{other_opinion}\nЕсли по существу не согласен с ним — прямо укажи, с чем и почему."
        if other_opinion
        else ""
    )
    prompt = f"Проверь предложенный фикс с фокусом на {focus}:\n{fix}{exchange}"
    return run_prompt_with_tier(ctx, tier, prompt, RunOptions(system=f"Ты — critic, фокус: {focus}.")).text


def _summarize_disagreement(ctx: StepContext, critic_a: str, critic_b: str) -> str:
    prompt = (
        f"Critic-A (фокус: {CRITIC_A_FOCUS}): {critic_a}\n\n"
        f"Critic-B (фокус: {CRITIC_B_FOCUS}): {critic_b}\n\n"
        "Критики не сошлись за несколько раундов доработки. В 2-3 предложениях: "
        "в чём именно конкретный камень преткновения — какой file/symbol, какая "
        "конкретная претензия каждой стороны."
    )
    return run_prompt_with_tier(
        ctx, AccountPriority.HEAD, prompt, RunOptions(system="Ты суммируешь разногласие между критиками.")
    ).text


class Step10Critics(Step):
    label = "10. Critic-A + Critic-B параллельно"

    def run(self, ctx: StepContext) -> None:
        fix_proposal = ctx.state.get("fix_proposal", "")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_run_critic, ctx, CRITIC_A_FOCUS, CRITIC_A_TIER, fix_proposal),
                pool.submit(_run_critic, ctx, CRITIC_B_FOCUS, CRITIC_B_TIER, fix_proposal),
            ]
            critic_a, critic_b = (f.result() for f in futures)
        ctx.state["critic_a"] = critic_a
        ctx.state["critic_b"] = critic_b


class Step11ConvergenceLoop(Step):
    label = "11. Convergence loop (до 3 раундов)"

    APPROVAL_MARKERS = ("одобрено", "approved", "нет замечаний", "lgtm")

    def run(self, ctx: StepContext) -> None:
        rounds = 0
        while rounds < MAX_CONVERGENCE_ROUNDS:
            critic_a_text = ctx.state.get("critic_a", "")
            critic_b_text = ctx.state.get("critic_b", "")
            converged = any(m in critic_a_text.lower() for m in self.APPROVAL_MARKERS) and any(
                m in critic_b_text.lower() for m in self.APPROVAL_MARKERS
            )
            if converged:
                break
            rounds += 1
            prompt = (
                f"Раунд {rounds}. Доработай фикс с учётом замечаний критиков:\n"
                f"Critic-A: {critic_a_text}\n"
                f"Critic-B: {critic_b_text}\n\n"
                f"Текущий фикс:\n{ctx.state.get('fix_proposal', '')}"
            )
            result = run_prompt_with_tier(
                ctx,
                AccountPriority.HEAD,
                prompt,
                RunOptions(system="Ты — fixer, дорабатываешь по замечаниям."),
            )
            ctx.state["fix_proposal"] = result.text

            fix_proposal = ctx.state["fix_proposal"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(_run_critic, ctx, CRITIC_A_FOCUS, CRITIC_A_TIER, fix_proposal, critic_b_text),
                    pool.submit(_run_critic, ctx, CRITIC_B_FOCUS, CRITIC_B_TIER, fix_proposal, critic_a_text),
                ]
                critic_a_new, critic_b_new = (f.result() for f in futures)
            ctx.state["critic_a"], ctx.state["critic_b"] = critic_a_new, critic_b_new

        ctx.state["convergence_rounds"] = rounds
        escalated = rounds >= MAX_CONVERGENCE_ROUNDS
        ctx.state["escalated"] = escalated
        if escalated:
            ctx.state["escalation_crux"] = _summarize_disagreement(
                ctx, ctx.state.get("critic_a", ""), ctx.state.get("critic_b", "")
            )


class Step12TestWriter(Step):
    label = "12. Test-writer + обязательный stash-check"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            "Напиши тесты, покрывающие фикс:\n"
            f"{ctx.state.get('fix_proposal', '')}"
        )
        result = run_prompt_with_tier(
            ctx, AccountPriority.MEDIUM, prompt, RunOptions(system="Ты — test-writer.")
        )
        ctx.state["tests_written"] = result.text

        stash_results = []
        stash_ok = True
        for project in ctx.projects:
            ok, detail = ctxdata.stash_check(project)
            stash_ok = stash_ok and ok
            stash_results.append(f"{project.name}: {detail}")
        ctx.state["stash_check_ok"] = stash_ok
        ctx.state["stash_check_detail"] = "\n".join(stash_results)
        if not stash_ok:
            raise ProviderError(
                "Обязательный stash-check провален — есть незавершённая работа в stash:\n"
                + ctx.state["stash_check_detail"]
            )


class Step13HumanConfirm(Step):
    """Только собирает финальный отчёт. Коммит — исключительно руками
    пользователя через бот/GitHub-модуль, здесь НИКАКОГО git commit нет
    и не должно появиться ни при каком провайдере."""

    label = "13. Human confirm + commit (агент не коммитит сам)"

    def run(self, ctx: StepContext) -> None:
        # Итоговый патч для job.patch_text — в этом пайплайне это скорее
        # справочный текст (fixer мог смешать несколько находок + пояснения
        # в прозе), чем гарантированно применимый diff. Реально применяемый
        # патч под одну задачу получается через "Фикс всё/выборочно" →
        # отдельный FIX-job на generic-пайплайне (см. app/tasks/generic.py).
        skipped = ctx.state.get("findings_skipped", 0)
        skipped_note = f", пропущено {skipped} (не распарсилось/нет local_path)" if skipped else ""
        deferred = ctx.state.get("findings_deferred_skipped", 0)
        deferred_note = f", не тронуто (уже в Отложено/Never) {deferred}" if deferred else ""

        budget_note = ctx.state.get("fleet_budget_note")
        budget_line = f"\n⚠️ {budget_note}" if budget_note else ""
        crux = ctx.state.get("escalation_crux")
        crux_line = f"\nКамень преткновения: {crux}" if crux else ""

        ctx.state["patch"] = ctx.state.get("fix_proposal")
        ctx.state["final_report"] = (
            f"Домены: {', '.join(ctx.state.get('domains', []))}{budget_line}\n"
            f"Раундов конвергенции: {ctx.state.get('convergence_rounds', 0)}"
            f"{' (эскалировано)' if ctx.state.get('escalated') else ''}{crux_line}\n"
            f"Реестр: новых находок {ctx.state.get('findings_registered', 0)}, "
            f"повторных {ctx.state.get('findings_bumped', 0)}{deferred_note}{skipped_note}\n\n"
            f"Отчёт:\n{ctx.state.get('aggregated_report', '')}\n\n"
            f"Пробелы:\n{ctx.state.get('gaps', '')}\n\n"
            f"Финальный фикс:\n{ctx.state.get('fix_proposal', '')}\n\n"
            f"Тесты:\n{ctx.state.get('tests_written', '')}"
        )


def build_steps() -> list[Step]:
    return [
        Step1to4Registry(),
        Step4bWebResearch(),
        Step5FleetPlanner(),
        Step6FleetCheckers(),
        Step7Aggregation(),
        Step8GapFinder(),
        Step8bRegisterFindings(),
        Step9Fixer(),
        Step10Critics(),
        Step11ConvergenceLoop(),
        Step12TestWriter(),
        Step13HumanConfirm(),
    ]
