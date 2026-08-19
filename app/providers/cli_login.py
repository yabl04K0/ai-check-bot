"""Общий помощник для CLI/OAuth-логина провайдеров через сам бот
(Cursor Agent CLI, Codex CLI) — раздел 🔌 Провайдеры ИИ в Настройках.

Такие команды обычно печатают URL/код устройства в stdout и блокируются,
ожидая, пока человек авторизуется в браузере. Из бота мы не можем провести
интерактивную сессию терминала, поэтому: запускаем с таймаутом, показываем
человеку весь захваченный вывод (там и будет ссылка), а реальный статус
подключения проверяется отдельным вызовом auth_status() после того, как
человек завершит авторизацию в браузере.
"""

from __future__ import annotations

import subprocess

from app.providers.base import LoginResult, ProviderError

LOGIN_TIMEOUT_SECONDS = 90


def run_cli_login(cli_path: str | None, *, missing_path_hint: str, timeout: int = LOGIN_TIMEOUT_SECONDS) -> LoginResult:
    if not cli_path:
        raise ProviderError(missing_path_hint)

    try:
        result = subprocess.run(
            [cli_path, "login"], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        # Типичный случай: команда ждёт браузерную авторизацию дольше
        # таймаута. Показываем то, что успело напечататься (обычно там URL).
        captured = (exc.stdout or "") + (exc.stderr or "")
        return LoginResult(
            success=False,
            message=(
                "Команда логина не завершилась за отведённое время — обычно "
                "значит, что она ждёт подтверждения в браузере.\n\n"
                f"{captured.strip()}\n\n"
                "Заверши авторизацию в браузере, потом проверь статус ещё раз "
                "в Настройках."
            ),
        )
    except OSError as exc:
        raise ProviderError(f"Не удалось запустить {cli_path} login: {exc}") from exc

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        return LoginResult(success=True, message=output.strip() or "Логин выполнен.")
    return LoginResult(success=False, message=output.strip() or f"login завершился с кодом {result.returncode}")
