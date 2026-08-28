from __future__ import annotations

import json

from app.bot.formatting import render_progress, render_report_header
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


def test_render_report_header_no_confidence_badge_without_state():
    job = Job(task_type=TaskType.CHECK_FULL)
    text = render_report_header(job)
    assert "раунда" not in text and "согласились" not in text and "не сошлись" not in text


def test_render_report_header_green_badge_on_zero_rounds():
    state = json.dumps({"convergence_rounds": 0, "escalated": False})
    job = Job(task_type=TaskType.CHECK_FULL, state_json=state)
    text = render_report_header(job)
    assert "🟢" in text


def test_render_report_header_yellow_badge_after_rounds():
    state = json.dumps({"convergence_rounds": 2, "escalated": False})
    job = Job(task_type=TaskType.CHECK_FULL, state_json=state)
    text = render_report_header(job)
    assert "🟡" in text
    assert "2" in text


def test_render_report_header_red_badge_when_escalated():
    state = json.dumps({"convergence_rounds": 3, "escalated": True})
    job = Job(task_type=TaskType.CHECK_FULL, state_json=state)
    text = render_report_header(job)
    assert "🔴" in text


def test_render_report_header_red_badge_includes_crux_when_present():
    state = json.dumps(
        {
            "convergence_rounds": 3,
            "escalated": True,
            "escalation_crux": "Спор о app/auth.py::validate_token",
        }
    )
    job = Job(task_type=TaskType.CHECK_FULL, state_json=state)
    text = render_report_header(job)
    assert "🔴" in text
    assert "validate_token" in text


def test_render_report_header_red_badge_without_crux_still_works():
    state = json.dumps({"convergence_rounds": 3, "escalated": True})
    job = Job(task_type=TaskType.CHECK_FULL, state_json=state)
    text = render_report_header(job)
    assert "🔴" in text
    assert "None" not in text


def test_render_report_header_ignores_malformed_state_json():
    job = Job(task_type=TaskType.CHECK_FULL, state_json="not json")
    text = render_report_header(job)
    assert "раунда" not in text and "согласились" not in text and "не сошлись" not in text
