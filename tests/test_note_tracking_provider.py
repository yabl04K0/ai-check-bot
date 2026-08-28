"""NoteTrackingProvider — обёртка вокруг провайдера, которая после каждого
run_prompt сохраняет фрагмент ответа в Job.progress_detail, чтобы прогресс
в Telegram показывал не только номер шага, но и что ИИ реально сказал
(см. app.providers.note_tracking, запрос пользователя на "подробное
описание работы ИИ")."""

from __future__ import annotations

from app.db.models import Job, ProviderName, TaskType
from app.db.session import get_session
from app.providers.base import AuthStatus, ProviderAccountStatus, ProviderResult
from app.providers.note_tracking import NoteTrackingProvider as _NoteTrackingProvider


class _FakeProvider:
    name = ProviderName.CLAUDE_CODE

    def auth_status(self):
        return AuthStatus(status=ProviderAccountStatus.CONNECTED)

    def run_prompt(self, prompt, options=None):
        return ProviderResult(text="  Нашёл проблему в auth.py:\n  строка 42.  ")


def test_run_prompt_saves_snippet_to_job_progress_detail(db):
    with get_session() as session:
        job = Job(task_type=TaskType.CHECK_FULL)
        session.add(job)
        session.flush()
        job_id = job.id

    wrapped = _NoteTrackingProvider(_FakeProvider(), job_id)
    result = wrapped.run_prompt("проверь auth.py")

    assert result.text.strip().startswith("Нашёл проблему")
    with get_session() as session:
        job = session.get(Job, job_id)
        assert job.progress_detail == "Нашёл проблему в auth.py: строка 42."


def test_delegates_unknown_attributes_to_inner_provider(db):
    wrapped = _NoteTrackingProvider(_FakeProvider(), job_id=1)
    assert wrapped.name == ProviderName.CLAUDE_CODE
    assert wrapped.auth_status().status == ProviderAccountStatus.CONNECTED
