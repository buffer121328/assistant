# plan

Task type: `plan`

Purpose: turn a user goal into an executable, reviewable plan.

Current shared implementation points:

- Feature runtime definition: `backend/features/plan/definition.py`
- Command parsing: `backend/app/support/commands.py`
- Task creation and lifecycle: `backend/domain/task_lifecycle.py`
- Runtime execution: `backend/workers/runtime.py`
- Agent profile adapter: `backend/agent/planning/profiles.py`
- Planning and execution loop: `backend/agent/core`
- Prompt templates: `backend/resources/prompts`
- Skill packages: `backend/resources/skillpacks`
- Acceptance tests: `tests/acceptance`

Add scenario-specific behavior here first, then wire it into the shared layers above.
