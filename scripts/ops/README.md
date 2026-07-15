# Operations scripts

This directory contains explicit backup, restore, Compose smoke, provider smoke,
and soak commands. Scripts fail closed and never print credentials.

Run the isolated local stack check from the repository root:

```bash
uv run python -m scripts.ops.compose_smoke
```

It uses `tests/integration/compose.env`, project name `assistant-v5-integration`,
and removes the isolated volumes on exit. It does not use the repository `.env`.

Run explicit real-provider checks only after filling the relevant `SMOKE_*`
variables in your local environment:

```bash
uv run python -m scripts.ops.provider_smoke
```

Each provider is reported separately as `skipped`, `passed`, or `failed`.
SMTP and CalDAV checks create one test email/event; browser requires an installed
Playwright Chromium binary. Skipped providers remain unverified.
