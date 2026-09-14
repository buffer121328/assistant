# Operations commands

This directory contains explicit database backup, restore, and bounded local soak
commands. They fail closed and never print credentials.

- `backup.py` creates a verified PostgreSQL custom-format backup and manifest.
- `restore.py` restores only into an explicitly confirmed empty database and
  validates the manifest, table counts, and Alembic version.
- `soak.py` performs a bounded authenticated local API probe.

Compose, browser, screenshot, provider, and desktop-release smoke checks live in
[`scripts/smoke/`](../smoke/). Run the isolated local Compose check from the
repository root:

```bash
PYTHONPATH=. uv --project backend run python -m scripts.smoke.compose_smoke
```

`provider_smoke.py` is opt-in. Supply its `SMOKE_*` values from a separate local
secret source when running it; do not add them to the application `.env` template.
