FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONPATH="/app/backend:/app"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /usr/local/bin/
# Dependencies are installed before the source copy so source-only changes reuse
# this layer instead of re-downloading the whole dependency set on every build.
# The project itself is not installed here: it has no source yet and runs via PYTHONPATH.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-dev --extra office --no-install-project
WORKDIR /app
COPY alembic.ini ./
COPY backend ./backend
COPY scripts ./scripts

RUN groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin assistant \
    && mkdir -p /app/data /app/run \
    && chown -R assistant:assistant /app/data /app/run

USER assistant

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
