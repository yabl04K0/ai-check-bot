FROM python:3.11-slim

# git — нужен пайплайну для stash-check/tests на локальных чекаутах
# проектов; grep — для sweep() (см. app/tasks/project_context.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git grep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite-кэш бота (см. app/config.py: db_path = <project_root>/data/…)
VOLUME ["/bot/data"]

CMD ["python", "-m", "app.main"]
