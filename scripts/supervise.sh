#!/usr/bin/env bash
# Внешний supervisor для self-check сценария (см. SELFNOTE в
# docs/architecture/ui-map.mermaid): бот НЕ умеет перезапустить сам себя
# средствами Python после того, как self-check закоммитил патч в
# собственный код. Этот скрипт — auto-restart loop вокруг процесса бота.
#
# Код выхода 0 — штатная остановка (Ctrl+C/SIGTERM), цикл завершается.
# Любой другой код выхода — падение, скрипт перезапускает бота после паузы.

set -u
cd "$(dirname "$0")/.."

RESTART_DELAY_SECONDS="${SUPERVISE_RESTART_DELAY:-5}"

while true; do
  echo "[supervise] $(date -Iseconds) запускаю: python -m app.main"
  python -m app.main
  code=$?

  if [ "$code" -eq 0 ]; then
    echo "[supervise] $(date -Iseconds) чистая остановка (exit 0), больше не перезапускаю"
    break
  fi

  echo "[supervise] $(date -Iseconds) бот упал с кодом $code, перезапуск через ${RESTART_DELAY_SECONDS}с"
  sleep "$RESTART_DELAY_SECONDS"
done
