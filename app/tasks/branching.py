"""Ветвление bot-коммитов — см. BRANCHING.md GATE-CONFIDENT: прямой коммит
в текущую ветку требует root-cause названного словами, только заявленный
скоуп, зелёные тесты, PROOF (red→green регресс-тест или тривиальная
проверка чтением) и отдельный critic-pass. Пайплайн Фичи/Фикса/Рефакторинга
(см. app/tasks/generic.py: план→патч→тесты, без критиков и без
regression-теста) физически не производит это доказательство — то есть
"уверенного" коммита от бота сегодня не бывает никогда, и коммитить его
прямо в ветку, которая была открыта у пользователя, значит без
предупреждения мешать бот-правки с его собственной работой.

main/beta/alpha из BRANCHING.md — конвенция конкретно одного проекта
(MeCelium), не универсальная топология; бот не предполагает, что у
произвольного проекта есть такие ветки. Вместо промоушена по уровням бот
просто изолирует каждый свой коммит в отдельную топик-ветку от текущего
HEAD и оставляет решение "смержить или нет" человеку — тот же принцип
безопасности, без привязки к чужой конвенции."""

from __future__ import annotations

from datetime import datetime

from app.db.models import Job, TaskType

_PREFIX = {
    TaskType.CHECK_FULL: "chek",
    TaskType.CHECK_LITE: "chek",
    TaskType.FIX: "fix",
    TaskType.FEATURE: "feat",
    TaskType.REFACTOR: "chore",
    TaskType.CUSTOM: "fix",
}


def topic_branch_name(job: Job) -> str:
    prefix = _PREFIX.get(job.task_type, "fix")
    date = datetime.now().strftime("%Y%m%d")
    return f"{prefix}/bot-job{job.id}-{date}"
