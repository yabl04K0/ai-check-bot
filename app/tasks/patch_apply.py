"""Применение сгенерированного unified-diff патча на диск + git commit.

Вызывается ТОЛЬКО после явного подтверждения человеком (кнопка "Да" в
💾 Зафиксить и запушить?, см. app/bot/handlers/check.py commit_yes) — сам
пайплайн (app/tasks/pipeline.py) никогда это не вызывает и не может
закоммитить самостоятельно, что бы ни сгенерировал провайдер.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class PatchApplyError(RuntimeError):
    pass


def clean_patch_text(text: str) -> str:
    """Снимает markdown-обёртку (```diff ... ```), которую модели иногда
    добавляют вопреки просьбе прислать чистый diff."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # убираем открывающий ```` или ```diff
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


def apply_patch(local_path: Path, patch_text: str) -> tuple[bool, str]:
    """`git apply --check` затем `git apply`. Не коммитит — только пишет на диск."""
    cleaned = clean_patch_text(patch_text)
    if not cleaned.strip():
        return False, "Патч пуст."

    fd, patch_path_str = tempfile.mkstemp(suffix=".patch")
    patch_path = Path(patch_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(cleaned)

        check = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check.returncode != 0:
            return False, f"git apply --check провалился (патч не применится чисто):\n{check.stderr.strip()}"

        apply = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if apply.returncode != 0:
            return False, f"git apply провалился:\n{apply.stderr.strip()}"
        return True, "Патч применён."
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Не удалось применить патч: {exc}"
    finally:
        patch_path.unlink(missing_ok=True)


def commit_all(local_path: Path, message: str) -> tuple[bool, str]:
    """`git add -A && git commit -m message`. Вызывающий код отвечает за
    то, чтобы это шло ПОСЛЕ явного человеческого подтверждения."""
    try:
        add = subprocess.run(
            ["git", "add", "-A"], cwd=local_path, capture_output=True, text=True, timeout=30
        )
        if add.returncode != 0:
            return False, f"git add провалился:\n{add.stderr.strip()}"

        commit = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=local_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if commit.returncode != 0:
            return False, f"git commit провалился:\n{commit.stderr.strip()}"
        return True, commit.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"git commit не выполнился: {exc}"


def has_uncommitted_changes(local_path: Path) -> bool:
    """Использует мануальный пуш (см. app.bot.handlers.projects.manual_push) —
    коммитить нужно, только если реально есть незакоммиченное, иначе
    `git commit` просто упал бы с "nothing to commit"."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=local_path, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def current_commit_sha(local_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=local_path, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None
