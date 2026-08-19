from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from github.GithubException import GithubException

from app.github_integration.client import GitHubClient, GitHubError


def _fake_repo(full_name: str, *, private: bool, open_issues: int = 0) -> MagicMock:
    # MagicMock, а не SimpleNamespace — set_visibility() зовёт repo.edit(...),
    # у настоящего PyGithub Repository это метод, не просто поле.
    repo = MagicMock()
    repo.full_name = full_name
    repo.private = private
    repo.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo.open_issues_count = open_issues
    return repo


def _client_with_fake_gh() -> tuple[GitHubClient, MagicMock]:
    client = GitHubClient("fake-token")
    fake_gh = MagicMock()
    client._gh = fake_gh  # подменяем сетевой клиент на мок
    return client, fake_gh


def test_missing_token_raises():
    with pytest.raises(GitHubError):
        GitHubClient("")


def test_list_repos_maps_fields():
    client, fake_gh = _client_with_fake_gh()
    fake_gh.get_user.return_value.get_repos.return_value = [
        _fake_repo("me/pub", private=False, open_issues=3),
        _fake_repo("me/priv", private=True, open_issues=0),
    ]

    repos = client.list_repos()

    assert [r.full_name for r in repos] == ["me/pub", "me/priv"]
    assert repos[0].private is False
    assert repos[0].open_issues == 3
    assert repos[1].private is True


def test_close_all_public_only_touches_public_repos():
    client, fake_gh = _client_with_fake_gh()
    fake_gh.get_user.return_value.get_repos.return_value = [
        _fake_repo("me/pub1", private=False),
        _fake_repo("me/priv", private=True),
        _fake_repo("me/pub2", private=False),
    ]
    # set_visibility делает повторный get_repo — вернём приватную версию репо
    fake_gh.get_repo.return_value = _fake_repo("me/pub1", private=True)

    result = client.close_all_public()

    assert result.closed == ["me/pub1", "me/pub2"]
    assert result.failed == []
    assert fake_gh.get_repo.call_count == 4  # edit+refetch, x2 репо


def test_close_all_public_continues_after_one_repo_fails():
    """Одна упавшая репа не должна терять то, что уже успело закрыться —
    раньше исключение из set_visibility обрывало весь батч и второй репо
    не закрывался вообще, хотя мог бы."""
    client, fake_gh = _client_with_fake_gh()
    fake_gh.get_user.return_value.get_repos.return_value = [
        _fake_repo("me/breaks", private=False),
        _fake_repo("me/ok", private=False),
    ]

    def get_repo_side_effect(name):
        if name == "me/breaks":
            raise GithubException(403, {"message": "forbidden"}, None)
        return _fake_repo("me/ok", private=True)

    fake_gh.get_repo.side_effect = get_repo_side_effect

    result = client.close_all_public()

    assert result.closed == ["me/ok"]
    assert len(result.failed) == 1
    assert result.failed[0][0] == "me/breaks"


def test_list_issues_excludes_pull_requests():
    client, fake_gh = _client_with_fake_gh()
    issue = SimpleNamespace(number=1, title="Bug", html_url="http://x/1", state="open", pull_request=None)
    pr = SimpleNamespace(number=2, title="PR", html_url="http://x/2", state="open", pull_request=object())
    fake_gh.get_repo.return_value.get_issues.return_value = [issue, pr]

    issues = client.list_issues("me/repo")

    assert len(issues) == 1
    assert issues[0]["number"] == 1


def test_never_exposes_a_delete_method():
    """Жёсткое ограничение уровня кода (см. README): удаление репо
    недостижимо ни через один путь в кодовой базе."""
    forbidden = {"delete", "delete_repo", "remove", "remove_repo"}
    public_methods = {name for name in dir(GitHubClient) if not name.startswith("_")}
    assert forbidden.isdisjoint(public_methods)
