"""Обёртка над PyGithub — только то, что разрешено скоупом токена.

Никогда не добавляй сюда метод удаления репозитория. Если когда-нибудь
понадобится очистка репо — это делается руками в самом GitHub, не через
бота (см. README и docs/architecture/backend-architecture.mermaid, G3).
"""

from __future__ import annotations

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

    def close_all_public(self) -> list[str]:
        """⚡ Закрыть все публичные — батч-операция из меню GitHub."""
        closed = []
        for status in self.list_repos():
            if not status.private:
                self.set_visibility(status.full_name, private=True)
                closed.append(status.full_name)
        return closed

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
        """Пуш после Step 13 (human confirm). Требует, чтобы local_path уже
        был git-репозиторием с origin, настроенным на аутентификацию токеном
        (credential helper / git remote с токеном в URL настраивает
        вызывающий код — сюда токен намеренно не передаётся, чтобы не
        светить его в аргументах процесса/логах)."""
        try:
            result = subprocess.run(
                ["git", "push", "origin", branch],
                cwd=local_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubError(f"git push не выполнился: {exc}") from exc
        if result.returncode != 0:
            raise GitHubError(f"git push завершился с ошибкой: {result.stderr.strip()}")
        return result.stdout.strip()
