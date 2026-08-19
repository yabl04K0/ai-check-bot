"""🔴 Full ЧЕК — CHEK_PROTOCOL.md, 13 шагов (см. README и backend-architecture.mermaid).

Провайдер для каждого шага приходит из ctx.provider — единственный,
выбранный роутером на весь прогон (никакого хардкода Claude/Opus/Sonnet
внутри шагов; какая именно модель используется — решает provider.run_prompt
через RunOptions.model, если понадобится тонкая настройка позже).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.providers.base import ProviderError, RunOptions
from app.tasks import project_context as ctxdata
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
        parts = []
        for project in ctx.projects:
            parts.append(f"### {project.name}")
            parts.append("**Реестр:**\n" + ctxdata.gather_registry(project))
            parts.append("**Тесты:**\n" + ctxdata.gather_tests(project))
            parts.append("**Логи:**\n" + ctxdata.gather_logs(project))
            parts.append("**Sweep (TODO/FIXME/XXX/HACK):**\n" + ctxdata.sweep(project))
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


class Step5FleetPlanner(Step):
    label = "5. Fleet-planner — делит проект на домены"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            f"{_project_summary(ctx)}\n\n"
            f"Контекст:\n{ctx.state.get('intake', '')}\n\n"
            f"Раздели аудит на {FLEET_CHECKER_DOMAINS_DEFAULT} независимых доменов "
            "(например: auth, api, db, frontend — под конкретный проект). "
            "Ответь построчно, только названия доменов, без нумерации и пояснений."
        )
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — fleet-planner для аудита кода."))
        domains = [line.strip("- ").strip() for line in result.text.splitlines() if line.strip()]
        ctx.state["domains"] = domains[:FLEET_CHECKER_DOMAINS_DEFAULT] or ["general"]


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
            result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — fleet-checker, только чтение."))
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
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — gap-finder, ищешь пропущенное."))
        ctx.state["gaps"] = result.text


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
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — fixer, предлагаешь патчи."))
        ctx.state["fix_proposal"] = result.text


class Step10Critics(Step):
    label = "10. Critic-A + Critic-B параллельно"

    def run(self, ctx: StepContext) -> None:
        fix_proposal = ctx.state.get("fix_proposal", "")

        def critic(focus: str) -> str:
            prompt = f"Проверь предложенный фикс с фокусом на {focus}:\n{fix_proposal}"
            result = ctx.provider.run_prompt(prompt, RunOptions(system=f"Ты — critic, фокус: {focus}."))
            return result.text

        with ThreadPoolExecutor(max_workers=2) as pool:
            critic_a, critic_b = pool.map(critic, ["корректность и регрессии", "безопасность и производительность"])
        ctx.state["critic_a"] = critic_a
        ctx.state["critic_b"] = critic_b


class Step11ConvergenceLoop(Step):
    label = "11. Convergence loop (до 3 раундов)"

    APPROVAL_MARKERS = ("одобрено", "approved", "нет замечаний", "lgtm")

    def run(self, ctx: StepContext) -> None:
        rounds = 0
        while rounds < MAX_CONVERGENCE_ROUNDS:
            critic_a = ctx.state.get("critic_a", "").lower()
            critic_b = ctx.state.get("critic_b", "").lower()
            converged = any(m in critic_a for m in self.APPROVAL_MARKERS) and any(
                m in critic_b for m in self.APPROVAL_MARKERS
            )
            if converged:
                break
            rounds += 1
            prompt = (
                f"Раунд {rounds}. Доработай фикс с учётом замечаний критиков:\n"
                f"Critic-A: {ctx.state.get('critic_a', '')}\n"
                f"Critic-B: {ctx.state.get('critic_b', '')}\n\n"
                f"Текущий фикс:\n{ctx.state.get('fix_proposal', '')}"
            )
            result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — fixer, дорабатываешь по замечаниям."))
            ctx.state["fix_proposal"] = result.text

            def critic(focus: str, fix: str = ctx.state["fix_proposal"]) -> str:
                p = f"Проверь доработанный фикс с фокусом на {focus}:\n{fix}"
                return ctx.provider.run_prompt(p, RunOptions(system=f"Ты — critic, фокус: {focus}.")).text

            with ThreadPoolExecutor(max_workers=2) as pool:
                critic_a_new, critic_b_new = pool.map(
                    critic, ["корректность и регрессии", "безопасность и производительность"]
                )
            ctx.state["critic_a"], ctx.state["critic_b"] = critic_a_new, critic_b_new

        ctx.state["convergence_rounds"] = rounds
        ctx.state["escalated"] = rounds >= MAX_CONVERGENCE_ROUNDS


class Step12TestWriter(Step):
    label = "12. Test-writer + обязательный stash-check"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            "Напиши тесты, покрывающие фикс:\n"
            f"{ctx.state.get('fix_proposal', '')}"
        )
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — test-writer."))
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
        ctx.state["final_report"] = (
            f"Домены: {', '.join(ctx.state.get('domains', []))}\n"
            f"Раундов конвергенции: {ctx.state.get('convergence_rounds', 0)}"
            f"{' (эскалировано)' if ctx.state.get('escalated') else ''}\n\n"
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
        Step9Fixer(),
        Step10Critics(),
        Step11ConvergenceLoop(),
        Step12TestWriter(),
        Step13HumanConfirm(),
    ]
