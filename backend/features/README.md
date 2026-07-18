# Backend Feature Layout

`backend/features/<task_type>` is the first stop for user-facing Agent scenarios.

Use it when adding or changing a scenario such as `plan`, `learn`, `daily`, `office`, or a future `travel`:

- Describe the scenario contract and user-visible behavior here.
- Add runtime wiring in `backend/features/<task_type>/definition.py`.
- Wire command parsing through `backend/app/support/commands.py`.
- Add or adjust Agent profile adaptation in `backend/agent/planning/profiles.py`.
- Put reusable prompt templates under `backend/resources/prompts`.
- Put Skill packages under `backend/resources/skillpacks`.
- Put shared tools under `backend/agent/tool_management` and providers under `backend/integrations`.
- Add acceptance coverage under `tests/acceptance`.

Keep cross-cutting runtime code in shared backend layers. Feature directories should make ownership and extension points obvious; they should not become dumping grounds for unrelated infrastructure.
