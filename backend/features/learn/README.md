# learn

Task type: `learn`

Purpose: search, read, summarize, and connect information with user knowledge and memory.

Current shared implementation points:

- Feature runtime definition: `backend/features/learn/definition.py`
- Command parsing: `backend/app/support/commands.py`
- Knowledge APIs: `backend/app/api/routers/knowledge.py`
- Memory APIs and retrieval: `backend/app/api/routers/memories.py`
- Runtime execution: `backend/workers/runtime.py`
- Agent profile adapter: `backend/agent/planning/profiles.py`
- Search and knowledge tools: `backend/agent/tool_management`
- Knowledge and memory services: `backend/knowledge`, `backend/agent/memory`
- Prompt templates: `backend/resources/prompts`
- Skill packages: `backend/resources/skillpacks`
- Acceptance tests: `tests/acceptance`

Add scenario-specific behavior here first, then wire it into the shared layers above.
