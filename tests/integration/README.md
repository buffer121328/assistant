# Integration tests

This directory contains tests that require the documented local PostgreSQL, Redis,
Celery, and migration stack. Tests use isolated databases and never read private
provider credentials.

- CI sets `RUN_SERVICE_INTEGRATION=1` after migrating its isolated services.
- `uv run python -m scripts.ops.compose_smoke` builds an isolated Compose project,
  checks migrations, PostgreSQL, Redis and Celery, interrupts Redis and the worker,
  verifies recovery, and removes its volumes on exit.
- Real-provider smoke checks live under `scripts/ops/` and report unconfigured
  providers as skipped, not passed.
