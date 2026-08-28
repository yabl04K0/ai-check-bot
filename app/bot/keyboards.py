"""Билдеры инлайн-клавиатур — один источник правды по callback_data."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import FindingStatus, Project, TaskType


def main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    """is_admin по умолчанию False — пункт 👑 Админка показывается, только
    если вызывающий явно подтвердил права (см. app.bot.access_control.is_admin),
    а не всем подряд, как было раньше (кнопка вела на экран, который для
    не-админа всё равно сразу отказывал)."""
    rows = [
        # ▶️, не 🔴 — 🔴 уже занят статусом "Открыто" в 📜 Реестре, один и
        # тот же эмодзи не должен значить разное в соседних экранах.
        [InlineKeyboardButton("▶️ ЧЕК", callback_data="chk:start:check_full"),
         InlineKeyboardButton("🟢 LITE", callback_data="chk:start:check_lite")],
        [InlineKeyboardButton("📁 Проекты", callback_data="menu:projects"),
         InlineKeyboardButton("📜 Реестр", callback_data="menu:registry")],
        [InlineKeyboardButton("✨ Фича", callback_data="chk:start:feature"),
         InlineKeyboardButton("🔧 Фикс", callback_data="chk:start:fix")],
        [InlineKeyboardButton("♻️ Рефакторинг", callback_data="chk:start:refactor"),
         InlineKeyboardButton("📝 Кастом", callback_data="chk:start:custom")],
        [InlineKeyboardButton("🕘 История", callback_data="menu:history"),
         InlineKeyboardButton("📊 Лимиты", callback_data="menu:limits"),
         InlineKeyboardButton("🤖 Активность", callback_data="menu:activity")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="menu:settings"),
         InlineKeyboardButton("🐙 GitHub", callback_data="menu:github")],
        [InlineKeyboardButton("🗨 ИИ-чат", callback_data="menu:ai_chat")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Админка", callback_data="menu:admin")])
    return InlineKeyboardMarkup(rows)


def back_button(target: str = "menu:main") -> InlineKeyboardButton:
    return InlineKeyboardButton("◀️ Назад", callback_data=target)


def home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 Меню", callback_data="menu:main")


def nav_row(back_target: str = "menu:main", *, home: bool = True) -> list[InlineKeyboardButton]:
    """Строка навигации для экрана глубже 1 уровня.

    "Назад" ведёт на предыдущий шаг ИМЕННО этого потока (например, экран
    scope визарда ЧЕКа — назад на выбор проектов), "Меню" — сразу в
    корень. Раньше back_button() почти everywhere по умолчанию вёл в
    menu:main, из-за чего "назад" внутри визарда превращался в "сбросить
    весь прогресс" — если back_target и так menu:main, вторая кнопка не
    нужна (это уже и есть корень)."""
    row = [back_button(back_target)]
    if home and back_target != "menu:main":
        row.append(home_button())
    return row


def confirm_row(
    yes_callback: str,
    no_callback: str,
    *,
    yes_label: str = "✅ Да",
    no_label: str = "❌ Отмена",
) -> list[InlineKeyboardButton]:
    """Строка подтверждения перед необратимым действием — единый паттерн
    вместо срабатывания сразу по одному тапу (см. delete_project и т.п.)."""
    return [
        InlineKeyboardButton(yes_label, callback_data=yes_callback),
        InlineKeyboardButton(no_label, callback_data=no_callback),
    ]


PAGE_SIZE = 8


def paginate_rows(
    rows: list[list[InlineKeyboardButton]], page: int, *, nav_prefix: str, per_page: int = PAGE_SIZE
) -> tuple[list[list[InlineKeyboardButton]], int]:
    """Режет уже построенные ряды кнопок на страницы по per_page элементов
    и, если страниц больше одной, добавляет нижнюю строку ◀️ N/M ▶️.

    callback_data пагинации несёт только номер страницы (`{nav_prefix}:{n}`),
    не сами данные — короче 64-байтного лимита Telegram, и источник
    правды остаётся на сервере (сам список перечитывается заново на
    каждый тап). Средняя кнопка со счётчиком страниц — noop (см.
    app/bot/handlers/menu.py::noop), просто индикатор, не действие.

    Возвращает (ряды_для_текущей_страницы, всего_страниц) — второе нужно,
    чтобы вызывающий код мог написать "стр. N/M" в тексте сообщения."""
    total_pages = max(1, -(-len(rows) // per_page))
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_rows = list(rows[start : start + per_page])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"{nav_prefix}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"{nav_prefix}:{page + 1}"))
        page_rows.append(nav)
    return page_rows, total_pages


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


def scope_menu(back_target: str = "chk:back:projects") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Всё", callback_data="chk:scope:all")],
        [InlineKeyboardButton("ЧЕК всё (игнор отложенного)", callback_data="chk:scope:all_ignore_registry")],
        [InlineKeyboardButton("Файл/модуль…", callback_data="chk:scope:module")],
        nav_row(back_target),
    ]
    return InlineKeyboardMarkup(rows)


def comment_menu(back_target: str = "chk:back:scope") -> InlineKeyboardMarkup:
    # Дефолт "chk:back:scope" — реально используемый путь (комментарий у
    # типов ЧЕК всегда идёт после скоупа); прежний "chk:back:projects" был
    # мёртвым значением, которое ни один вызов не использовал.
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Пропустить", callback_data="chk:comment:skip")], nav_row(back_target)]
    )


def confirm_menu(task_type: TaskType, back_target: str = "chk:back:ai") -> InlineKeyboardMarkup:
    """back_target — последний шаг визарда ПЕРЕД подтверждением: и для
    типов ЧЕК (проекты→скоуп→комментарий→🤖ИИ→подтверждение), и для
    остальных (проекты→комментарий→🤖ИИ→подтверждение) это экран выбора
    ИИ для задачи, см. check.py::back_to_ai. Раньше тут была только
    жёсткая "✖ Отмена" → menu:main, стиравшая весь прогресс — единственный
    шаг визарда без пути назад (см. аудит меню)."""
    is_check = task_type in (TaskType.CHECK_FULL, TaskType.CHECK_LITE)
    label = "Запустить ЧЕК" if is_check else "Запустить"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✅ {label}", callback_data="chk:confirm")],
            nav_row(back_target),
        ]
    )


def progress_menu(job_id: int, *, paused: bool = False) -> InlineKeyboardMarkup:
    pause_button = (
        InlineKeyboardButton("▶️ Продолжить", callback_data=f"job:resume:{job_id}")
        if paused
        else InlineKeyboardButton("⏸ Пауза", callback_data=f"job:pause:{job_id}")
    )
    return InlineKeyboardMarkup(
        [
            [pause_button, InlineKeyboardButton("✖ Отмена", callback_data=f"job:cancel:{job_id}")],
            [
                InlineKeyboardButton("💬 Комментарий", callback_data=f"job:note:{job_id}"),
                InlineKeyboardButton("📦 Архив", callback_data=f"job:archive:{job_id}"),
            ],
        ]
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
