"""Обёртка над PyGithub — только то, что разрешено скоупом токена.

Никогда не добавляй сюда метод удаления репозитория. Если когда-нибудь
понадобится очистка репо — это делается руками в самом GitHub, не через
бота (см. README и docs/architecture/backend-architecture.mermaid, G3).
"""

from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from github import Auth, Github
from github.GithubException import GithubException


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoStatus:
    full_name: str
    private: bool
    updated_at: str
    open_issues: int


@dataclass(frozen=True)
class BatchCloseResult:
    closed: list[str]
    failed: list[tuple[str, str]]  # (repo_full_name, текст ошибки)


class GitHubClient:
    """Единственная точка входа к GitHub API в проекте."""

    def __init__(self, token: str) -> None:
        if not token:
            raise GitHubError("GITHUB_TOKEN не задан.")
        self._gh = Github(auth=Auth.Token(token))
        self._token = token

    def list_repos(self) -> list[RepoStatus]:
        try:
            repos = self._gh.get_user().get_repos()
            return [
                RepoStatus(
                    full_name=r.full_name,
                    private=r.private,
                    updated_at=r.updated_at.isoformat() if r.updated_at else "",
                    open_issues=r.open_issues_count,
                )
                for r in repos
            ]
        except GithubException as exc:
            raise GitHubError(f"Не удалось получить список репозиториев: {exc}") from exc

    def set_visibility(self, repo_full_name: str, *, private: bool) -> RepoStatus:
        """Единственная Administration-операция, разрешённая скоупом токена."""
        try:
            repo = self._gh.get_repo(repo_full_name)
            repo.edit(private=private)
            repo = self._gh.get_repo(repo_full_name)
        except GithubException as exc:
            raise GitHubError(f"Не удалось изменить видимость {repo_full_name}: {exc}") from exc
        return RepoStatus(
            full_name=repo.full_name,
            private=repo.private,
            updated_at=repo.updated_at.isoformat() if repo.updated_at else "",
            open_issues=repo.open_issues_count,
        )

    def close_all_public(self) -> BatchCloseResult:
        """⚡ Закрыть все публичные — батч-операция из меню GitHub.

        Одна упавшая репа (rate limit, специфичные права именно на неё)
        не должна обрывать всю операцию и терять то, что уже успело
        закрыться — поэтому продолжаем и репортим оба списка, а не
        падаем с первой же ошибкой."""
        closed = []
        failed = []
        for status in self.list_repos():
            if status.private:
                continue
            try:
                self.set_visibility(status.full_name, private=True)
                closed.append(status.full_name)
            except GitHubError as exc:
                failed.append((status.full_name, str(exc)))
        return BatchCloseResult(closed=closed, failed=failed)

    def list_issues(self, repo_full_name: str, *, state: str = "open") -> list[dict]:
        try:
            repo = self._gh.get_repo(repo_full_name)
            issues = repo.get_issues(state=state)
            return [
                {"number": i.number, "title": i.title, "url": i.html_url, "state": i.state}
                for i in issues
                if i.pull_request is None
            ]
        except GithubException as exc:
            raise GitHubError(f"Не удалось получить issues {repo_full_name}: {exc}") from exc

    def push_commit(self, local_path: Path, branch: str = "main") -> str:
        """Пуш после Step 13 (human confirm).

        Аутентификация — GITHUB_TOKEN, переданный через переменные
        окружения (GIT_CONFIG_KEY_0/VALUE_0, официальный механизм git
        начиная с 2.31), НЕ через argv: `ps aux` виден другим локальным
        пользователям хоста, окружение процесса — только тому же
        пользователю/root, тот же уровень доверия, что у остальных
        секретов в этом проекте (.env). Работает только для HTTPS-remote
        (`https://github.com/...`) — fine-grained PAT так и задуман,
        для SSH-remote (`git@github.com:...`) эта аутентификация не
        применяется, git использует свой обычный SSH-механизм."""
        basic_auth = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
        env = {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic_auth}",
        }
        try:
            result = subprocess.run(
                ["git", "push", "origin", branch],
                cwd=local_path,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubError(f"git push не выполнился: {exc}") from exc
        if result.returncode != 0:
            raise GitHubError(f"git push завершился с ошибкой: {result.stderr.strip()}")
        return result.stdout.strip()
