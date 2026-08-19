"""Парсер/писатель chek_open.md / chek_later.md / chek_never.md.

Формат — один заголовок ## на находку, дальше строки `ключ: значение`,
затем абзац описания:

    ## 🟥 app/auth.py::validate_token
    - severity: critical
    - attempts: 2
    - created: 2026-08-10
    - updated: 2026-08-12

    Токен не проверяется на None перед decode, падает с AttributeError.

Это единственный источник правды по находкам — SQLite (app.db.models.Finding)
только кэш, синкается из этих файлов после каждого коммита (Step 13).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

OPEN_FILENAME = "chek_open.md"
LATER_FILENAME = "chek_later.md"
NEVER_FILENAME = "chek_never.md"

_HEADER_RE = re.compile(r"^##\s*(?:(?P<emoji>\S+)\s+)?(?P<file_symbol>.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s*(?P<key>\w+):\s*(?P<value>.*)$")

SEVERITY_EMOJI_TO_NAME = {"🟥": "critical", "🟧": "high", "🟨": "medium"}
NAME_TO_SEVERITY_EMOJI = {v: k for k, v in SEVERITY_EMOJI_TO_NAME.items()}


@dataclass
class RegistryFinding:
    file_symbol: str
    description: str = ""
    severity: str | None = None  # critical/high/medium — только для open
    reason: str | None = None  # причина отложить/never
    attempts: int = 0
    created: str | None = None
    updated: str | None = None

    def to_markdown(self) -> str:
        emoji = NAME_TO_SEVERITY_EMOJI.get(self.severity or "", "")
        header = f"## {emoji + ' ' if emoji else ''}{self.file_symbol}".rstrip()
        fields = []
        if self.severity:
            fields.append(f"- severity: {self.severity}")
        if self.reason:
            fields.append(f"- reason: {self.reason}")
        fields.append(f"- attempts: {self.attempts}")
        fields.append(f"- created: {self.created or date.today().isoformat()}")
        fields.append(f"- updated: {self.updated or date.today().isoformat()}")
        body = "\n".join([header, *fields, "", self.description.strip()])
        return body


@dataclass
class Registry:
    open: list[RegistryFinding] = field(default_factory=list)
    later: list[RegistryFinding] = field(default_factory=list)
    never: list[RegistryFinding] = field(default_factory=list)


def _parse_file(path: Path) -> list[RegistryFinding]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=##\s)", text)
    findings: list[RegistryFinding] = []
    for block in blocks:
        block = block.strip()
        if not block.startswith("##"):
            continue
        lines = block.splitlines()
        header_match = _HEADER_RE.match(lines[0])
        if not header_match:
            continue
        file_symbol = header_match.group("file_symbol")
        emoji = header_match.group("emoji")
        severity = SEVERITY_EMOJI_TO_NAME.get(emoji or "", None)

        fields: dict[str, str] = {}
        description_lines: list[str] = []
        in_description = False
        for line in lines[1:]:
            if not in_description:
                field_match = _FIELD_RE.match(line)
                if field_match:
                    fields[field_match.group("key")] = field_match.group("value")
                    continue
                if line.strip() == "":
                    in_description = True
                    continue
            in_description = True
            description_lines.append(line)

        findings.append(
            RegistryFinding(
                file_symbol=file_symbol,
                description="\n".join(description_lines).strip(),
                severity=fields.get("severity", severity),
                reason=fields.get("reason"),
                attempts=int(fields.get("attempts", 0) or 0),
                created=fields.get("created"),
                updated=fields.get("updated"),
            )
        )
    return findings


def read_registry(project_path: Path) -> Registry:
    return Registry(
        open=_parse_file(project_path / OPEN_FILENAME),
        later=_parse_file(project_path / LATER_FILENAME),
        never=_parse_file(project_path / NEVER_FILENAME),
    )


def _write_file(path: Path, findings: list[RegistryFinding]) -> None:
    content = "\n\n".join(f.to_markdown() for f in findings)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def write_registry(project_path: Path, registry: Registry) -> None:
    _write_file(project_path / OPEN_FILENAME, registry.open)
    _write_file(project_path / LATER_FILENAME, registry.later)
    _write_file(project_path / NEVER_FILENAME, registry.never)


def move_finding(
    project_path: Path,
    file_symbol: str,
    *,
    to: str,
    reason: str | None = None,
) -> bool:
    """Переносит находку между open/later/never. Возвращает False если не нашли."""
    if to not in {"open", "later", "never"}:
        raise ValueError(f"Некорректный статус назначения: {to}")

    registry = read_registry(project_path)
    buckets = {"open": registry.open, "later": registry.later, "never": registry.never}

    found: RegistryFinding | None = None
    for items in buckets.values():
        for finding in items:
            if finding.file_symbol == file_symbol:
                found = finding
                items.remove(finding)
                break
        if found:
            break

    if found is None:
        return False

    found.updated = date.today().isoformat()
    if to != "open":
        found.reason = reason or found.reason
    buckets[to].append(found)
    write_registry(project_path, registry)
    return True


def add_open_finding(project_path: Path, finding: RegistryFinding) -> None:
    registry = read_registry(project_path)
    finding.created = finding.created or date.today().isoformat()
    finding.updated = finding.updated or finding.created
    registry.open.append(finding)
    write_registry(project_path, registry)
