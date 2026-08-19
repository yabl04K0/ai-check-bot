from __future__ import annotations

from app.bot.formatting import render_progress
from app.db.models import Job, TaskType


def _job(step: int, total: int, label: str | None = None) -> Job:
    job = Job(task_type=TaskType.CHECK_FULL, progress_step=step, progress_total=total, progress_label=label)
    return job


def test_render_progress_shows_percentage_and_step():
    text = render_progress(_job(6, 13, "fleet-checkers"))
    assert "46%" in text  # round(100*6/13) == 46
    assert "Шаг 6/13" in text
    assert "fleet-checkers" in text


def test_render_progress_zero_total_does_not_crash():
    text = render_progress(_job(0, 0))
    assert "0%" in text


def test_render_progress_full_bar_at_completion():
    text = render_progress(_job(4, 4, "готово"))
    assert "▓▓▓▓▓▓▓▓▓▓ 100%" in text
