# daily

Task type: `daily`

Purpose: handle personal daily assistance, reminders, task status, and lightweight context-aware help.

Current shared implementation points:

- Feature runtime definition: `backend/features/daily/definition.py`
- Command parsing: `backend/app/support/commands.py`
- Notification and reminder APIs: `backend/app/api/routers/notifications.py`
- Runtime execution: `backend/workers/runtime.py`
- Agent profile adapter: `backend/agent/planning/profiles.py`
- Notification services: `backend/notifications`
- Memory services: `backend/memory`
- Prompt templates: `backend/resources/prompts`
- Skill packages: `backend/resources/skillpacks`
- Acceptance tests: `tests/acceptance`

Add scenario-specific behavior here first, then wire it into the shared layers above.
