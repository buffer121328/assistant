# status feature

`status` 对应 `/status` 命令入口。

- Feature runtime definition: `backend/features/status_command/definition.py`
- Command parsing: `backend/app/support/commands.py`
- Task status execution: `backend/tasks/status.py`
- Application dispatch: `backend/application/task_execution/executor.py`
- Acceptance tests: `tests/acceptance`

该 feature 只声明命令到 task type 的映射；状态查询执行逻辑仍由 `backend/tasks` 与 runtime special-case 承载。
