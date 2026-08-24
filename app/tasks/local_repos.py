"""Сканирование локальных git-репозиториев под LOCAL_REPOS_ROOT — чтобы
при добавлении проекта (📁 Проекты → ➕) можно было выбрать репозиторий
кнопкой вместо ручного набора пути и owner/repo."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def discover_local_repos(root: Path, *, max_depth: int = 2) -> list[Path]:
    """Директории под root с `.git` внутри — сама директория с `.git` не
    разбирается дальше (не ищем репо внутри репо), скрытые каталоги
    (`.cache` и т.п.) пропускаются."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    _walk(root, max_depth, found)
    return sorted(found)


def _walk(path: Path, depth_left: int, found: list[Path]) -> None:
    try:
        entries = sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return
    for entry in entries:
        if (entry / ".git").exists():
            found.append(entry)
            continue
        if depth_left > 0:
            _walk(entry, depth_left - 1, found)


def detect_repo_full_name(path: Path) -> str | None:
    """`owner/repo` из `git remote get-url origin`, если remote — GitHub
    (HTTPS или SSH форма). None, если remote нет/не GitHub/git недоступен —
    вызывающий код тогда просит ввести owner/repo текстом."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = _GITHUB_REMOTE_RE.search(result.stdout.strip())
    return f"{match.group('owner')}/{match.group('repo')}" if match else None
