# memory feature

`memory` 对应 `/memory` 命令入口。

- Feature runtime definition: `backend/features/memory_command/definition.py`
- Command parsing: `backend/app/support/commands.py`
- User memory service: `backend/application/user_memory`
- Memory APIs: `backend/app/api/routers/memories.py`
- Agent memory tool: `backend/tools/builtin/agent_memory`
- Acceptance tests: `tests/acceptance`

该 feature 只声明命令到 task type 的映射；用户长期记忆 CRUD、治理、语义同步由 `backend/application/user_memory` 承载。
