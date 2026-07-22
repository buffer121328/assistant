# Operations scripts

This directory contains explicit backup, restore, Compose smoke, and soak
commands. Scripts fail closed and never print credentials.

Run the isolated local stack check from the repository root:

```bash
uv run python -m scripts.ops.compose_smoke
```

It uses `tests/integration/compose.env`, project name `assistant-v5-integration`,
and removes the isolated volumes on exit. It does not use the repository `.env`.

`provider_smoke.py` is an opt-in operator script. Supply its `SMOKE_*` values
from a separate local secret source when running it; do not add them to the
application `.env` template.
