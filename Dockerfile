FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/apps/api:/app"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY alembic.ini ./
COPY apps ./apps
COPY migrations ./migrations
COPY packages ./packages
COPY prompts ./prompts

RUN groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin assistant \
    && mkdir -p /app/data /app/run \
    && chown -R assistant:assistant /app/data /app/run

USER assistant

EXPOSE 8000

CMD ["uvicorn", "--app-dir", "apps/api", "assistant_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
