from __future__ import annotations

from app.tasks import scope as scope_util


def test_is_ignore_registry():
    assert scope_util.is_ignore_registry("all_ignore_registry") is True
    assert scope_util.is_ignore_registry("all") is False
    assert scope_util.is_ignore_registry(None) is False
    assert scope_util.is_ignore_registry("path:app/") is False


def test_path_filter_extracts_subpath():
    assert scope_util.path_filter("path:app/auth.py") == "app/auth.py"
    assert scope_util.path_filter("path:  app/auth.py  ") == "app/auth.py"


def test_path_filter_none_for_other_scopes():
    assert scope_util.path_filter("all") is None
    assert scope_util.path_filter("all_ignore_registry") is None
    assert scope_util.path_filter(None) is None
    assert scope_util.path_filter("path:") is None
