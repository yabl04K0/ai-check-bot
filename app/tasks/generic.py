"""Пайплайн для Фичи/Фикса/Рефакторинга/Кастома.

Тот же движок Step/Pipeline, что и у ЧЕКа, но без частей протокола,
специфичных именно для аудита (fleet-planner, критики и т.д. — они нужны
только проверке). Здесь: спланировать → написать → протестировать →
показать диф (см. README, "Типы задач").
"""

from __future__ import annotations

from app.db.models import TaskType
from app.providers.base import RunOptions
from app.providers.tiers import AccountPriority, run_prompt_with_tier
from app.tasks import project_context as ctxdata
from app.tasks.pipeline import Step, StepContext

TASK_TYPE_VERB = {
    TaskType.FEATURE: "реализуй новую функциональность",
    TaskType.FIX: "точечно исправь баг",
    TaskType.REFACTOR: "сделай рефакторинг без изменения поведения",
    TaskType.CUSTOM: "выполни задачу",
}


_PLAN_SYSTEM_PROMPT = (
    "Ты — планировщик задач разработки. Если задача действительно неоднозначна и "
    "без ответа пользователя нельзя составить нормальный план — ответь РОВНО одной "
    "строкой: ВОПРОС: текст вопроса. Во всех остальных случаях просто составляй план, "
    "не задавай вопрос по мелочам."
)


class GenericStep1Plan(Step):
    label = "1. План"

    def run(self, ctx: StepContext) -> None:
        verb = TASK_TYPE_VERB.get(ctx.job.task_type, "выполни задачу")
        intake = []
        for project in ctx.projects:
            intake.append(f"### {project.name}\n" + ctxdata.gather_registry(project))
        prompt = (
            f"Задача ({verb}): {ctx.comment}\n\n"
            f"Контекст проекта(ов):\n{chr(10).join(intake)}\n\n"
            "Составь короткий план: какие файлы менять и зачем, по пунктам."
        )
        result = run_prompt_with_tier(
            ctx, AccountPriority.HEAD, prompt, RunOptions(system=_PLAN_SYSTEM_PROMPT)
        )
        text = result.text.strip()
        if text.upper().startswith("ВОПРОС:"):
            question = text.split(":", 1)[1].strip()
            answer = ctx.ask_user(question)
            if answer:
                prompt += f"\n\nОтвет пользователя на уточняющий вопрос '{question}': {answer}"
                result = run_prompt_with_tier(
                    ctx, AccountPriority.HEAD, prompt, RunOptions(system=_PLAN_SYSTEM_PROMPT)
                )
                text = result.text.strip()
            else:
                text = (
                    f"(вопрос '{question}' остался без ответа — план ниже составлен без уточнения)\n\n{text}"
                )
        ctx.state["plan"] = text


class GenericStep2Implement(Step):
    label = "2. Написать"

    def run(self, ctx: StepContext) -> None:
        prompt = (
            f"План:\n{ctx.state.get('plan', '')}\n\n"
            f"Исходная задача: {ctx.comment}\n\n"
            "Сгенерируй патч, реализующий план, СТРОГО в формате unified diff "
            "(как `git diff`: заголовки `--- a/путь` / `+++ b/путь`, ханки `@@`). "
            "Ответь ТОЛЬКО самим диффом — без markdown-разметки (```), без "
            "пояснений до или после. Патч не применяется на диск автоматически: "
            "человек должен нажать подтверждение (human-in-the-loop, см. "
            "README) — только после этого app.tasks.patch_apply запускает "
            "`git apply` + `git commit`, поэтому невалидный diff здесь = "
            "неприменимый патч в боте."
        )
        result = run_prompt_with_tier(
            ctx,
            AccountPriority.MEDIUM,
            prompt,
            RunOptions(system="Ты — разработчик, пишешь unified diff патч."),
        )
        ctx.state["patch"] = result.text


class GenericStep3Test(Step):
    label = "3. Протестировать"

    def run(self, ctx: StepContext) -> None:
        results = []
        for project in ctx.projects:
            results.append(f"{project.name}:\n{ctxdata.gather_tests(project)}")
        ctx.state["test_results"] = "\n\n".join(results)


class GenericStep4ShowDiff(Step):
    label = "4. Показать диф"

    def run(self, ctx: StepContext) -> None:
        ctx.state["final_report"] = (
            f"План:\n{ctx.state.get('plan', '')}\n\n"
            f"Патч:\n{ctx.state.get('patch', '')}\n\n"
            f"Тесты:\n{ctx.state.get('test_results', '')}"
        )


def build_steps() -> list[Step]:
    return [GenericStep1Plan(), GenericStep2Implement(), GenericStep3Test(), GenericStep4ShowDiff()]
