from __future__ import annotations

from app.db.models import Job, JobStatus, Project, TaskType
from app.tasks.archive_export import build_handoff_markdown


def _make_job(**overrides) -> Job:
    defaults = dict(
        id=1,
        task_type=TaskType.CHECK_FULL,
        status=JobStatus.DONE,
        progress_step=13,
        progress_total=13,
        progress_label=None,
        comment=None,
        live_notes=None,
        progress_detail=None,
        report_text=None,
        patch_text=None,
        handover_note=None,
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_build_handoff_markdown_minimal_has_only_base_sections():
    job = _make_job()

    text = build_handoff_markdown(job, [])

    assert "# Хендовер — задача #1 (🔴 Full ЧЕК)" in text
    assert "Проекты: (без проекта)" in text
    assert "Прогресс: шаг 13/13" in text
    assert "## Исходная задача" not in text
    assert "## Комментарии во время выполнения" not in text
    assert "## Последнее, что делал ИИ" not in text
    assert "## Отчёт/план на текущий момент" not in text
    assert "## Патч" not in text
    assert "## Заметка HANDOVER" not in text
    assert text.strip().endswith("продолжить работу оттуда.")


def test_build_handoff_markdown_includes_comment_section_when_present():
    job = _make_job(comment="Почини баг X")

    text = build_handoff_markdown(job, [])

    assert "## Исходная задача" in text
    assert "Почини баг X" in text


def test_build_handoff_markdown_includes_live_notes_section_when_present():
    job = _make_job(live_notes="[10:00] добавь ещё Y")

    text = build_handoff_markdown(job, [])

    assert "## Комментарии во время выполнения" in text
    assert "добавь ещё Y" in text


def test_build_handoff_markdown_includes_progress_detail_section_when_present():
    job = _make_job(progress_detail="Правил файл foo.py")

    text = build_handoff_markdown(job, [])

    assert "## Последнее, что делал ИИ" in text
    assert "foo.py" in text


def test_build_handoff_markdown_includes_report_section_when_present():
    job = _make_job(report_text="Итоговый план: ...")

    text = build_handoff_markdown(job, [])

    assert "## Отчёт/план на текущий момент" in text
    assert "Итоговый план" in text


def test_build_handoff_markdown_includes_patch_section_wrapped_in_diff_fence():
    job = _make_job(patch_text="--- a/x\n+++ b/x")

    text = build_handoff_markdown(job, [])

    assert "## Патч (unified diff), если успел сгенерировать" in text
    assert "```diff" in text
    assert "--- a/x" in text
    assert "```" in text


def test_build_handoff_markdown_includes_handover_note_section_when_present():
    job = _make_job(handover_note="Обрыв на шаге 5/13")

    text = build_handoff_markdown(job, [])

    assert "## Заметка HANDOVER" in text
    assert "Обрыв на шаге 5/13" in text


def test_build_handoff_markdown_lists_multiple_project_names():
    projects = [
        Project(name="Alpha", repo_full_name="o/alpha"),
        Project(name="Beta", repo_full_name="o/beta"),
    ]
    job = _make_job()

    text = build_handoff_markdown(job, projects)

    assert "Проекты: Alpha, Beta" in text


def test_build_handoff_markdown_includes_progress_label_when_set():
    job = _make_job(progress_label="Шаг 5: фикс")

    text = build_handoff_markdown(job, [])

    assert "Прогресс: шаг 13/13 — Шаг 5: фикс" in text


def test_build_handoff_markdown_omits_progress_label_dash_when_unset():
    job = _make_job(progress_label=None)

    text = build_handoff_markdown(job, [])

    assert "Прогресс: шаг 13/13\n" in text


def test_build_handoff_markdown_all_sections_together_preserve_order():
    job = _make_job(
        comment="c",
        live_notes="ln",
        progress_detail="pd",
        report_text="rt",
        patch_text="pt",
        handover_note="hn",
    )

    text = build_handoff_markdown(job, [])

    headers = [
        "## Исходная задача",
        "## Комментарии во время выполнения",
        "## Последнее, что делал ИИ",
        "## Отчёт/план на текущий момент",
        "## Патч",
        "## Заметка HANDOVER",
    ]
    positions = [text.index(h) for h in headers]
    assert positions == sorted(positions)


def test_build_handoff_markdown_uses_task_type_value_for_unknown_label():
    job = _make_job(task_type=TaskType.CUSTOM)

    text = build_handoff_markdown(job, [])

    assert "📝 Кастом" in text
