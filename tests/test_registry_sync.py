from __future__ import annotations

from sqlalchemy import select

from app.db.models import Finding, FindingStatus, Project, Severity
from app.db.session import get_session
from app.registry_store.store import Registry, RegistryFinding, write_registry
from app.registry_store.sync import sync_project_findings


def _make_project(session, tmp_path, name="P1") -> Project:
    project = Project(name=name, repo_full_name=f"owner/{name.lower()}", local_path=str(tmp_path))
    session.add(project)
    session.flush()
    return project


def test_sync_populates_cache_from_md(db, tmp_path):
    write_registry(
        tmp_path,
        Registry(
            open=[RegistryFinding(file_symbol="a.py::f", description="сломано", severity="critical")],
            later=[RegistryFinding(file_symbol="b.py::g", description="", reason="занят")],
        ),
    )
    with get_session() as session:
        project = _make_project(session, tmp_path)
        sync_project_findings(session, project)
        session.commit()

        rows = session.scalars(select(Finding).where(Finding.project_id == project.id)).all()
        by_symbol = {r.file_symbol: r for r in rows}

    assert len(by_symbol) == 2
    assert by_symbol["a.py::f"].status == FindingStatus.OPEN
    assert by_symbol["a.py::f"].severity == Severity.CRITICAL
    assert by_symbol["b.py::g"].status == FindingStatus.LATER
    assert by_symbol["b.py::g"].reason == "занят"


def test_sync_updates_existing_row_on_status_change(db, tmp_path):
    write_registry(tmp_path, Registry(open=[RegistryFinding(file_symbol="a.py::f", description="x")]))
    with get_session() as session:
        project = _make_project(session, tmp_path)
        sync_project_findings(session, project)
        session.commit()
        project_id = project.id

    # находку перенесли в later прямо в файле (как это делает move_finding)
    write_registry(tmp_path, Registry(later=[RegistryFinding(file_symbol="a.py::f", description="x", reason="потом")]))

    with get_session() as session:
        project = session.get(Project, project_id)
        sync_project_findings(session, project)
        session.commit()

        rows = session.scalars(select(Finding).where(Finding.project_id == project_id)).all()

    assert len(rows) == 1  # не задвоилось
    assert rows[0].status == FindingStatus.LATER
    assert rows[0].reason == "потом"


def test_sync_removes_stale_rows_no_longer_in_md(db, tmp_path):
    write_registry(
        tmp_path,
        Registry(open=[RegistryFinding(file_symbol="a.py::f", description="x"), RegistryFinding(file_symbol="b.py::g", description="y")]),
    )
    with get_session() as session:
        project = _make_project(session, tmp_path)
        sync_project_findings(session, project)
        session.commit()
        project_id = project.id

    # находку a.py::f убрали из файла вручную
    write_registry(tmp_path, Registry(open=[RegistryFinding(file_symbol="b.py::g", description="y")]))

    with get_session() as session:
        project = session.get(Project, project_id)
        sync_project_findings(session, project)
        session.commit()

        rows = session.scalars(select(Finding).where(Finding.project_id == project_id)).all()

    assert {r.file_symbol for r in rows} == {"b.py::g"}


def test_sync_noop_without_local_path(db):
    with get_session() as session:
        project = Project(name="NoPath", repo_full_name="owner/nopath")
        session.add(project)
        session.flush()
        sync_project_findings(session, project)  # не должно упасть
        session.commit()

        rows = session.scalars(select(Finding).where(Finding.project_id == project.id)).all()

    assert rows == []
