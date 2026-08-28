"""Сбор контекста проекта с диска — реестр/тесты/логи/sweep.

Работает только когда у Project задан local_path (локальный чекаут).
Если пути нет — шаги честно отмечают контекст как недоступный, а не
подделывают данные.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.db.models import Project
from app.registry_store.last_prompt import read_last_prompt
from app.registry_store.project_memory import read_architecture
from app.registry_store.store import read_registry

UNAVAILABLE = "(нет локального чекаута — local_path не задан для проекта)"
NO_LAST_PROMPT = "(LAST_PROMPT.md пуст — прошлая сессия не оставила заметок)"
NO_PROJECT_MEMORY = "(PROJECT_MEMORY.md нет в проекте — архитектурная память не заведена)"


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
        lines.append(f"- [OPEN][{finding.severity or '?'}] {finding.file_symbol}: {finding.description}")
    # later/never тоже показываем — checker должен знать, что человек уже
    # принял по ним решение, и не тратить прогон на их повторное открытие
    # (если не выбран скоуп "ЧЕК всё", см. app.tasks.scope).
    for finding in registry.later:
        updated_note = f" (решено {finding.updated})" if finding.updated else ""
        lines.append(f"- [LATER]{updated_note} {finding.file_symbol}: {finding.reason or ''}")
    for finding in registry.never:
        updated_note = f" (решено {finding.updated})" if finding.updated else ""
        lines.append(f"- [NEVER]{updated_note} {finding.file_symbol}: {finding.reason or ''}")
    return "\n".join(lines)


def gather_last_prompt(project: Project) -> str:
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    text = read_last_prompt(path)
    return text if text else NO_LAST_PROMPT


def gather_project_memory(project: Project) -> str:
    """Архитектура+инварианты+история решений (PROJECT_MEMORY.md, без
    хвоста SESSION LOG — см. registry_store.project_memory.read_architecture
    и CLAUDE.md "ALWAYS read at session start"). Только для Full ЧЕК —
    Lite сознательно облегчённый режим (см. protocol_lite.py)."""
    path = local_path(project)
    if path is None:
        return UNAVAILABLE
    text = read_architecture(path)
    return text if text else NO_PROJECT_MEMORY


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
            text=True, encoding="utf-8", errors="replace",
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


def sweep(project: Project, *, path_filter: str | None = None) -> str:
    """Быстрый grep-скан на TODO/FIXME/XXX и прочие явные маркеры.

    path_filter — сужение до подпути (скоуп "Файл/модуль", см.
    app.tasks.scope). Значение приходит из текста, который человек ввёл в
    боте — проверяем, что оно не выходит за пределы local_path проекта
    (без этого можно было бы просканировать что угодно на диске бота)."""
    path = local_path(project)
    if path is None:
        return UNAVAILABLE

    target = path
    if path_filter:
        candidate = (path / path_filter).resolve()
        try:
            candidate.relative_to(path.resolve())
        except ValueError:
            return f"(скоуп '{path_filter}' указывает за пределы проекта — игнорирую)"
        if not candidate.exists():
            return f"(путь '{path_filter}' не найден в проекте)"
        target = candidate

    try:
        result = subprocess.run(
            ["grep", "-rn", "-E", "TODO|FIXME|XXX|HACK", "--include=*.py", str(target)],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
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
            ["git", "stash", "list"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"не удалось проверить git stash: {exc}"
    stash_list = result.stdout.strip()
    if stash_list:
        return False, f"В stash есть незавершённая работа:\n{stash_list}"
    return True, "stash чист"
