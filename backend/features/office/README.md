# office

Task type: `office`

Purpose: operate controlled productivity workflows such as email, calendar, browser-backed tasks, and document-oriented actions.

Current shared implementation points:

- Feature runtime definition: `backend/features/office/definition.py`
- Command parsing: `backend/app/support/commands.py`
- Account connection APIs: `backend/app/api/routers/accounts.py`
- Runtime execution: `backend/workers/runtime.py`
- Agent profile adapter: `backend/agent/planning/profiles.py`
- Productivity tools: `backend/agent/tool_management`
- Account-backed providers: `backend/integrations`
- Prompt templates: `backend/resources/prompts`
- Skill packages: `backend/resources/skillpacks`
- Acceptance tests: `tests/acceptance`

Add scenario-specific behavior here first, then wire it into the shared layers above.
