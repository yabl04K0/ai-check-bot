"""Парсинг строгого построчного формата находок из ответа LLM.

Используется в Full ЧЕК (см. app/tasks/protocol_full.py), чтобы реально
дописывать находки в chek_open.md проекта, а не только показывать текст
отчёта человеку. Формат нарочно тупой и однозначный — LLM просят вывести
СТРОГО так, парсер ничего другого не поймёт (что и требуется: лучше
пропустить кривую строку, чем угадывать структуру из вольного текста).
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_SEVERITIES = {"critical", "high", "medium"}


@dataclass(frozen=True)
class ParsedFinding:
    severity: str
    project_name: str
    file_symbol: str
    description: str


def parse_structured_findings(text: str) -> list[ParsedFinding]:
    """Ожидаемый формат строки: `severity|project|file::symbol|описание`."""
    results: list[ParsedFinding] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            continue
        severity, project_name, file_symbol, description = parts
        severity = severity.lower()
        if severity not in VALID_SEVERITIES or not project_name or not file_symbol or not description:
            continue
        results.append(ParsedFinding(severity, project_name, file_symbol, description))
    return results
