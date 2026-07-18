# Assistant Desktop V7 Release Notes

## Distribution Mode

V7 desktop uses **external installed mode** for the Python Agent Server.

The Electron app does not bundle Python, `.venv`, PostgreSQL, Redis, Playwright, Office libraries, PySide6, local models, or test fixtures. Users must start the Python API/worker stack separately and configure the desktop app to connect to a localhost `/local/*` API endpoint.

Sidecar startup, bundled Python runtimes, code signing, and automatic updates are intentionally deferred until those flows can be measured and verified.

## Repeatable Build

From `apps/desktop-web`:

```bash
npm ci
npm run build
npm run dist:dir
```

`npm run dist` can generate platform installers after Node dependencies are installed.

## Packaging Boundary

The packaging config includes only:

- Electron/Vite build output under `dist/**`
- `package.json`
- this release note

The packaging config excludes:

- `.venv`
- `.git`
- `.mypy_cache`
- `.pytest_cache`
- `.ruff_cache`
- `.vite`
- test directories
- node module build caches
- source maps

## Startup Diagnostics

On launch, the renderer loads local settings, checks `/local/health` and `/local/config`, and shows a disconnected diagnostic state when the Agent Server is unavailable. The user can edit the local API URL in Settings and validate it through `/local/settings/validate`.

## Metrics

Package size: not measured. Node/Electron dependencies were not installed in this workspace during V7-06 implementation.

Cold startup time: not measured. The Electron app was not launched in a built package during V7-06 implementation.

Idle memory usage: not measured. The Electron app was not launched in a built package during V7-06 implementation.

These fields must be replaced with measured values after `npm ci`, `npm run dist:dir`, and a local launch smoke test on the target platform.
