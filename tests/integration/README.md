# Integration tests

This directory contains tests that require the documented local PostgreSQL, Redis,
Celery, and migration stack. Tests use isolated databases and never read private
provider credentials.

- Run service integration checks explicitly with `RUN_SERVICE_INTEGRATION=1`; migrate the isolated services before executing them.
- `PYTHONPATH=. uv --project backend run python -m scripts.smoke.compose_smoke` builds an isolated Compose project,
  checks API health and authenticated local configuration, writable runtime storage,
  migrations, PostgreSQL, Redis and Celery, interrupts Redis and the worker,
  verifies recovery, and removes its volumes on exit.
- Real-provider smoke checks live under `scripts/smoke/` and report unconfigured
  providers as skipped, not passed.
