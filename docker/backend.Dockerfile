FROM ghcr.io/astral-sh/uv:0.11.16 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY data ./data
COPY migrations ./migrations
COPY alembic.ini ./
RUN groupadd --gid 10001 commerce && useradd --uid 10001 --gid commerce --no-create-home commerce \
    && mkdir /app/.data && chown commerce:commerce /app/.data
ENV PATH="/app/.venv/bin:$PATH"
USER commerce
CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
