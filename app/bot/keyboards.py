"""Билдеры инлайн-клавиатур — один источник правды по callback_data."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import FindingStatus, Project, TaskType


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔴 ЧЕК", callback_data="chk:start:check_full")],
        [InlineKeyboardButton("🟢 LITE ЧЕК", callback_data="chk:start:check_lite")],
        [InlineKeyboardButton("✨ Фича", callback_data="chk:start:feature"),
         InlineKeyboardButton("🔧 Фикс", callback_data="chk:start:fix")],
        [InlineKeyboardButton("♻️ Рефакторинг", callback_data="chk:start:refactor"),
         InlineKeyboardButton("📝 Кастом", callback_data="chk:start:custom")],
        [InlineKeyboardButton("📁 Проекты", callback_data="menu:projects")],
        [InlineKeyboardButton("📜 Реестр (все)", callback_data="menu:registry")],
        [InlineKeyboardButton("🕘 История ЧЕКов", callback_data="menu:history")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu:settings")],
        [InlineKeyboardButton("🐙 GitHub", callback_data="menu:github")],
        [InlineKeyboardButton("👑 Админка", callback_data="menu:admin")],
    ]
    return InlineKeyboardMarkup(rows)


def back_button(target: str = "menu:main") -> InlineKeyboardButton:
    return InlineKeyboardButton("« Назад", callback_data=target)


def project_multiselect(projects: list[Project], selected: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for project in projects:
        mark = "☑️" if project.id in selected else "⬜️"
        self_tag = " (self)" if project.is_self else ""
        rows.append(
            [InlineKeyboardButton(f"{mark} {project.name}{self_tag}", callback_data=f"chk:proj:{project.id}")]
        )
    rows.append([InlineKeyboardButton("➕ Добавить проект", callback_data="proj:add")])
    if selected:
        rows.append([InlineKeyboardButton("Далее ▶️", callback_data="chk:proj:next")])
    rows.append([back_button()])
    return InlineKeyboardMarkup(rows)


def scope_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Всё", callback_data="chk:scope:all")],
        [InlineKeyboardButton("ЧЕК всё (игнор отложенного)", callback_data="chk:scope:all_ignore_registry")],
        [InlineKeyboardButton("Файл/модуль…", callback_data="chk:scope:module")],
        [back_button()],
    ]
    return InlineKeyboardMarkup(rows)


def comment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="chk:comment:skip")]])


def confirm_menu(task_type: TaskType) -> InlineKeyboardMarkup:
    label = "Запустить ЧЕК" if task_type == TaskType.CHECK_FULL else "Запустить"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✅ {label}", callback_data="chk:confirm")],
            [InlineKeyboardButton("✖ Отмена", callback_data="menu:main")],
        ]
    )


def progress_menu(job_id: int, *, paused: bool = False) -> InlineKeyboardMarkup:
    pause_button = (
        InlineKeyboardButton("▶️ Продолжить", callback_data=f"job:resume:{job_id}")
        if paused
        else InlineKeyboardButton("⏸ Пауза", callback_data=f"job:pause:{job_id}")
    )
    return InlineKeyboardMarkup(
        [[pause_button, InlineKeyboardButton("✖ Отмена", callback_data=f"job:cancel:{job_id}")]]
    )


def report_menu(job_id: int, *, is_check: bool) -> InlineKeyboardMarkup:
    if is_check:
        rows = [
            [InlineKeyboardButton("🔧 Фикс всё", callback_data=f"report:fix_all:{job_id}"),
             InlineKeyboardButton("🎯 Фикс выборочно", callback_data=f"report:fix_select:{job_id}")],
            [InlineKeyboardButton("⏭️ Отложить", callback_data=f"report:later:{job_id}"),
             InlineKeyboardButton("🚫 Не баг", callback_data=f"report:never:{job_id}")],
            [InlineKeyboardButton("🔍 Детали", callback_data=f"report:details:{job_id}")],
            [InlineKeyboardButton("OK", callback_data="menu:main")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("🔍 Детали", callback_data=f"report:details:{job_id}")],
            [InlineKeyboardButton("💾 Зафиксить и запушить?", callback_data=f"commit:ask:{job_id}")],
            [InlineKeyboardButton("OK", callback_data="menu:main")],
        ]
    return InlineKeyboardMarkup(rows)


def commit_confirm_menu(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да", callback_data=f"commit:yes:{job_id}"),
             InlineKeyboardButton("Нет", callback_data=f"commit:no:{job_id}")],
            [InlineKeyboardButton("Показать полный diff", callback_data=f"commit:diff:{job_id}")],
        ]
    )


def registry_tabs(project_id: int) -> InlineKeyboardMarkup:
    def tab(label: str, status: FindingStatus) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"reg:tab:{project_id}:{status.value}")

    return InlineKeyboardMarkup(
        [
            [
                tab("🔴 Открыто", FindingStatus.OPEN),
                tab("🟡 Отложено", FindingStatus.LATER),
                tab("⚫ Never", FindingStatus.NEVER),
            ],
            [back_button("menu:registry")],
        ]
    )


def approval_menu(job_id: int) -> InlineKeyboardMarkup:
    """Запрос на запуск задачи, пока включён доступ ИИ к GITHUB_TOKEN и
    выключено автоодобрение (см. app.providers.ai_autonomy) — тот же
    паттерн подтверждения перед выполнением, что у вайб-кодинг-приложений."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Разрешить", callback_data=f"job:approve:{job_id}"),
             InlineKeyboardButton("❌ Отклонить", callback_data=f"job:reject:{job_id}")],
        ]
    )


def dismiss_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("OK", callback_data="dismiss")]])
