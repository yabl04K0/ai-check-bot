"""Удаление проекта (📁 Проекты → 🗑️ Убрать из списка) раньше оставляло
висячие строки в job_projects — SQLite по умолчанию не проверяет FK
(PRAGMA foreign_keys выключен), так что рассинхрон не падал ошибкой,
просто тихо копился. Project.job_links (cascade="all, delete-orphan")
чинит это на уровне ORM, независимо от того, какой код удаляет проект."""

from __future__ import annotations

from sqlalchemy import text

from app.db.models import Job, Project, TaskType
from app.db.session import get_session
from app.tasks.queue import JobQueue


def test_deleting_project_removes_job_projects_row(db):
    with get_session() as session:
        project = Project(name="P", repo_full_name="owner/p")
        session.add(project)
        session.flush()
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.FIX, [project.id])
        job_id = job.id
        project_id = project.id

    with get_session() as session:
        session.delete(session.get(Project, project_id))
        session.commit()

    with get_session() as session:
        rows = session.execute(text("SELECT * FROM job_projects")).all()
        assert rows == []

        job = session.get(Job, job_id)
        assert job is not None  # сам job переживает удаление проекта
        assert job.projects == []  # но связь с удалённым проектом ушла


def test_deleting_one_of_two_projects_keeps_the_other_linked(db):
    with get_session() as session:
        p1 = Project(name="P1", repo_full_name="owner/p1")
        p2 = Project(name="P2", repo_full_name="owner/p2")
        session.add_all([p1, p2])
        session.flush()
        queue = JobQueue(session)
        job = queue.enqueue(TaskType.CHECK_FULL, [p1.id, p2.id])
        job_id = job.id
        p1_id = p1.id

    with get_session() as session:
        session.delete(session.get(Project, p1_id))
        session.commit()

    with get_session() as session:
        job = session.get(Job, job_id)
        assert [p.name for p in job.projects] == ["P2"]
