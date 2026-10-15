FROM ghcr.io/astral-sh/uv:0.11.16 AS uv
FROM postgres:16-bookworm AS pgtools
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 liblz4-1 libzstd1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY data ./data
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
RUN groupadd --gid 10001 commerce && useradd --uid 10001 --gid commerce --no-create-home commerce \
    && mkdir /app/.data && chown commerce:commerce /app/.data
ENV PATH="/app/.venv/bin:$PATH"
USER commerce
CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
