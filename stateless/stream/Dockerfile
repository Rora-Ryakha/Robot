FROM python:3.10-slim

RUN pip install poetry==2.3.2

ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR='/tmp/poetry_cache'

# RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
# RUN curl -sSL https://install.python-poetry.org | python3 -

WORKDIR /app

COPY ./packages /packages
COPY ./stream/pyproject.toml ./stream/poetry.lock /app/
RUN poetry install --only main --no-root

COPY ./stream/src /app/src
COPY ./stream/.env /app/.env
RUN poetry install

CMD ["poetry", "run", "python", "src/main.py"]
