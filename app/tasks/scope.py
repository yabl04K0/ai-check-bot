"""Скоуп прогона ЧЕКа — три варианта из README/ui-map.mermaid:

- "all" (или пусто) — обычный прогон, уважает решения человека по
  Отложено/Never (не переоткрывает их).
- "all_ignore_registry" — "ЧЕК всё": игнорирует Отложено/Never, всё
  считается заново открытым.
- "path:<путь>" — файл/модуль: сужает sweep() до конкретного подпути.
"""

from __future__ import annotations

IGNORE_REGISTRY_SCOPE = "all_ignore_registry"
PATH_SCOPE_PREFIX = "path:"


def is_ignore_registry(scope: str | None) -> bool:
    return scope == IGNORE_REGISTRY_SCOPE


def path_filter(scope: str | None) -> str | None:
    """Возвращает подпуть из скоупа "path:<путь>", иначе None."""
    if scope and scope.startswith(PATH_SCOPE_PREFIX):
        value = scope[len(PATH_SCOPE_PREFIX) :].strip()
        return value or None
    return None
