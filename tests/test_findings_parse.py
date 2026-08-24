from __future__ import annotations

from app.tasks.findings_parse import parse_structured_findings


def test_parses_valid_lines():
    text = (
        "critical|Demo|app/auth.py::validate_token|Токен не проверяется на None\n"
        "high|Demo|app/db.py::save|Не закрывается соединение\n"
    )
    parsed = parse_structured_findings(text)
    assert len(parsed) == 2
    assert parsed[0].severity == "critical"
    assert parsed[0].project_name == "Demo"
    assert parsed[0].file_symbol == "app/auth.py::validate_token"
    assert parsed[1].severity == "high"


def test_skips_malformed_lines():
    text = (
        "critical|Demo|app/auth.py::validate_token|описание\n"
        "это не по формату вообще\n"
        "too|few|parts\n"
        "unknown_severity|P|f::s|d\n"
        "\n"
        "# комментарий, если модель всё-таки его добавила\n"
    )
    parsed = parse_structured_findings(text)
    assert len(parsed) == 1
    assert parsed[0].file_symbol == "app/auth.py::validate_token"


def test_severity_is_case_insensitive():
    parsed = parse_structured_findings("CRITICAL|P|f::s|d")
    assert parsed[0].severity == "critical"


def test_empty_text_returns_empty_list():
    assert parse_structured_findings("") == []
    assert parse_structured_findings("   \n  \n") == []
