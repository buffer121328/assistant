# Backend Feature Layout

`backend/features/<task_type>` is the first stop for user-facing slash-command scenarios.

Use it when adding or changing an Agent scenario such as `plan`, `learn`, `daily`, `office`, or a future `travel`:

- Describe the scenario contract and user-visible behavior here.
- Add runtime wiring in `backend/features/<task_type>/definition.py`.
- Wire command parsing through `backend/app/support/commands.py` and `FEATURE_COMMANDS`.
- Add or adjust Agent profile adaptation in `backend/agent/planning/profiles.py` when the feature runs through the Agent planner.
- Put reusable prompt templates under `backend/resources/prompts`.
- Put Skill packages under `backend/resources/skillpacks`.
- Put shared tools under `backend/tools` and providers under `backend/integrations`.
- Add acceptance coverage under `tests/acceptance`.

Utility commands such as `memory` and `status` also live here for command discovery, but their application services remain in their owning backend layers, such as `backend/application/user_memory` and `backend/application/task_execution`.

Keep cross-cutting runtime code in shared backend layers. Feature directories should make ownership and extension points obvious; they should not become dumping grounds for unrelated infrastructure.
