"""Сбор контекста проекта с диска — реестр/тесты/логи/sweep.

Работает только когда у Project задан local_path (локальный чекаут).
Если пути нет — шаги честно отмечают контекст как недоступный, а не
подделывают данные.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.db.models import Project
from app.registry_store.store import read_registry

UNAVAILABLE = "(нет локального чекаута — local_path не задан для проекта)"


def local_path(project: Project) -> Path | None:
    if not project.local_path:
        return None
    path = Path(project.local_path)
    return path if path.is_dir() else None


def gather_registry(project: Project) -> str:
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    registry = read_registry(path)
    lines = [f"Открыто: {len(registry.open)}, Отложено: {len(registry.later)}, Never: {len(registry.never)}"]
    for finding in registry.open:
        lines.append(f"- [{finding.severity or '?'}] {finding.file_symbol}: {finding.description}")
    return "\n".join(lines)


def gather_tests(project: Project, *, timeout: int = 300) -> str:
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    has_pytest_config = (path / "pytest.ini").exists() or (path / "pyproject.toml").exists()
    has_test_files = any(path.glob("test_*.py")) or any(path.glob("tests/**/*.py"))
    if not has_pytest_config and not has_test_files:
        return "(тестов не найдено — pytest не запускался)"
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "-q", "--tb=short"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(не удалось запустить тесты: {exc})"
    tail = "\n".join(result.stdout.splitlines()[-40:])
    return f"exit={result.returncode}\n{tail}"


def gather_logs(project: Project, *, max_lines: int = 200) -> str:
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    logs_dir = path / "logs"
    if not logs_dir.is_dir():
        return "(папки logs/ нет)"
    chunks = []
    for log_file in sorted(logs_dir.glob("*.log"))[:5]:
        try:
            lines = log_file.read_text(errors="replace").splitlines()[-max_lines:]
        except OSError:
            continue
        chunks.append(f"== {log_file.name} ==\n" + "\n".join(lines))
    return "\n\n".join(chunks) if chunks else "(логов не найдено)"


def sweep(project: Project) -> str:
    """Быстрый grep-скан на TODO/FIXME/XXX и прочие явные маркеры."""
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    try:
        result = subprocess.run(
            ["grep", "-rn", "-E", "TODO|FIXME|XXX|HACK", "--include=*.py", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(sweep не удался: {exc})"
    hits = result.stdout.strip().splitlines()
    if not hits:
        return "(маркеров TODO/FIXME/XXX/HACK не найдено)"
    return "\n".join(hits[:200])


def stash_check(project: Project) -> tuple[bool, str]:
    """Обязательная проверка перед финализацией: нет забытого stash с работой."""
    path = local_path(project)
    if path is None:
        return True, UNAVAILABLE
    try:
        result = subprocess.run(
            ["git", "stash", "list"], cwd=path, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"не удалось проверить git stash: {exc}"
    stash_list = result.stdout.strip()
    if stash_list:
        return False, f"В stash есть незавершённая работа:\n{stash_list}"
    return True, "stash чист"
