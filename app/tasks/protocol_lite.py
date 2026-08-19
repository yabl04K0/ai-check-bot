"""🟢 Lite ЧЕК — Sonnet-оркестратор + локальная LLM вместо флота.

ctx.provider здесь — то, что выбрал роутер для CHECK_LITE (по умолчанию
локальная LLM как scout, см. app.providers.router.DEFAULT_PRIORITY).
Эскалация на Full — отдельная кнопка в отчёте бота, не часть этого
пайплайна (см. app/bot/handlers/check.py).
"""

from __future__ import annotations

from app.providers.base import RunOptions
from app.tasks import project_context as ctxdata
from app.tasks import scope as scope_util
from app.tasks.pipeline import Step, StepContext


class LiteStep1Orchestrator(Step):
    label = "1. Оркестратор — реестр/тесты/логи/sweep (как в Full)"

    def run(self, ctx: StepContext) -> None:
        parts = []
        for project in ctx.projects:
            parts.append(f"### {project.name}")
            parts.append("**Реестр:**\n" + ctxdata.gather_registry(project))
            parts.append("**Тесты:**\n" + ctxdata.gather_tests(project))
            parts.append("**Логи:**\n" + ctxdata.gather_logs(project))
        ctx.state["intake"] = "\n\n".join(parts)


class LiteStep2Scout(Step):
    label = "2. Scout — grep-паттерны + быстрый скан (локальная LLM)"

    def run(self, ctx: StepContext) -> None:
        path_filter = scope_util.path_filter(ctx.scope)
        sweep_results = "\n\n".join(
            f"{p.name}:\n{ctxdata.sweep(p, path_filter=path_filter)}" for p in ctx.projects
        )
        prompt = (
            f"Контекст:\n{ctx.state.get('intake', '')}\n\n"
            f"Grep-паттерны (TODO/FIXME/XXX/HACK):\n{sweep_results}\n\n"
            "Быстро просканируй и перечисли явные проблемы (без глубокого анализа, "
            "это Lite-режим). severity: critical/high/medium."
        )
        result = ctx.provider.run_prompt(prompt, RunOptions(system="Ты — scout, быстрый скан кода."))
        ctx.state["scout_report"] = result.text


class LiteStep3Fixer(Step):
    label = "3. Fixer (только если найдено и явно попросили)"

    FIX_TRIGGER_WORDS = ("фикс", "почини", "исправь", "fix")

    def run(self, ctx: StepContext) -> None:
        comment = (ctx.comment or "").lower()
        wants_fix = any(word in comment for word in self.FIX_TRIGGER_WORDS)
        scout_report = ctx.state.get("scout_report", "")
        if not wants_fix or not scout_report.strip():
            ctx.state["fix_proposal"] = None
            ctx.state["patch"] = None
            return
        prompt = (
            f"На основе находок предложи фикс:\n{scout_report}\n\n"
            "Ответь СТРОГО в формате unified diff (`git diff`: заголовки "
            "`--- a/путь` / `+++ b/путь`, ханки `@@`), без markdown-разметки "
            "и пояснений — на подтверждении в боте это идёт прямо в `git apply`."
        )
        result = ctx.provider.run_prompt(
            prompt, RunOptions(system="Ты — fixer в Lite-режиме, пишешь unified diff.")
        )
        ctx.state["fix_proposal"] = result.text
        ctx.state["patch"] = result.text


class LiteStep4Report(Step):
    label = "4. Отчёт (без Opus-критиков, рекомендация догнать Full)"

    def run(self, ctx: StepContext) -> None:
        ctx.state["final_report"] = (
            f"Scout:\n{ctx.state.get('scout_report', '')}\n\n"
            f"Фикс:\n{ctx.state.get('fix_proposal') or '(не запрашивался)'}\n\n"
            "Рекомендация: для полного покрытия догнать 🔴 Full ЧЕК."
        )


def build_steps() -> list[Step]:
    return [LiteStep1Orchestrator(), LiteStep2Scout(), LiteStep3Fixer(), LiteStep4Report()]
