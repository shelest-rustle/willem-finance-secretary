FROM python:3.13-slim

ENV POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root

COPY . .
RUN poetry install --only main

CMD ["python", "main.py"]
