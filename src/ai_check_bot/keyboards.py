"""Inline keyboards. Pattern taken from sd-forge-bot's keyboards.py (same python-telegram-
bot library as this project, unlike MeCelium/AutoPost's aiogram): compact 2-column rows,
the current value shown inside the button label, a 🔙 Назад row on every submenu."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ai_check_bot.config import MAX_PROBES_PER_DAY
from ai_check_bot.models import AIAccount, ProbeSchedule


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔌 Провайдеры ИИ", callback_data="menu:accounts")],
            [InlineKeyboardButton("🔄 Проверить всё сейчас", callback_data="job:probe_all")],
            [InlineKeyboardButton("✨ Новая задача", callback_data="task:new")],
        ]
    )


def accounts_menu(accounts: list[AIAccount], schedule_counts: dict[int, int]) -> InlineKeyboardMarkup:
    rows = []
    for acc in accounts:
        n = schedule_counts.get(acc.id, 0)
        mark = "" if acc.enabled else " ⏸"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{acc.provider} · {acc.label} ({n}/{MAX_PROBES_PER_DAY}){mark}",
                    callback_data=f"acc:{acc.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("➕ Добавить аккаунт", callback_data="acc:add")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def account_detail_menu(account: AIAccount, schedule_count: int) -> InlineKeyboardMarkup:
    toggle_label = "⏸ Выключить" if account.enabled else "▶️ Включить"
    proxy_label = f"🌐 Прокси: {account.proxy_url}" if account.proxy_url else "🌐 Прокси: не задан"
    rows = [
        [InlineKeyboardButton(f"📅 Расписание ({schedule_count}/{MAX_PROBES_PER_DAY})", callback_data=f"sch:{account.id}")],
        [InlineKeyboardButton(proxy_label, callback_data=f"acc:proxy:{account.id}")],
        [
            InlineKeyboardButton("▶️ Проверить сейчас", callback_data=f"acc:probe:{account.id}"),
            InlineKeyboardButton(toggle_label, callback_data=f"acc:toggle:{account.id}"),
        ],
        [InlineKeyboardButton("🗑 Удалить аккаунт", callback_data=f"acc:delconfirm:{account.id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="menu:accounts")],
    ]
    return InlineKeyboardMarkup(rows)


def confirm_delete_menu(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Да, удалить", callback_data=f"acc:delete:{account_id}"),
                InlineKeyboardButton("Отмена", callback_data=f"acc:{account_id}"),
            ]
        ]
    )


def schedule_menu(account_id: int, schedules: list[ProbeSchedule]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🗑 {s.time_of_day}", callback_data=f"sch:del:{account_id}:{s.id}")]
        for s in schedules
    ]
    if len(schedules) < MAX_PROBES_PER_DAY:
        rows.append([InlineKeyboardButton("➕ Добавить время", callback_data=f"sch:add:{account_id}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data=f"acc:{account_id}")])
    return InlineKeyboardMarkup(rows)


def cancel_job_menu(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Отмена", callback_data=f"job:cancel:{job_id}")]])
