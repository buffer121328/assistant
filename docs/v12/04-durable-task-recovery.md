# V12-04 持久任务恢复

## 阶段目标

让长任务在 worker 被杀、服务重启、等待审批或外部工具失败时，可以恢复、补偿或进入可诊断的失败状态，而不是静默丢失或从头重复高风险动作。

## 当前问题

当前项目有：

- Celery worker。
- Celery Beat / heartbeat。
- task 状态。
- task events。
- approvals。
- ToolLog / ModelLog。
- 部分 pending/running 补偿扫描。

但尚未形成完整的持久工作流执行记录和步骤级恢复协议。

## 范围

- worker kill/restart 后任务状态处理。
- graph node / step attempt 持久化。
- recoverable failed 与 dead-letter。
- 外部副作用工具幂等键。
- 审批等待后的恢复。

## 不做事项

- V12 本地阶段不强制引入 Temporal。
- 不引入 Kafka/RabbitMQ。
- 不做分布式高可用。
- 不做跨机器 worker 调度。

## 验收标准

- [x] worker 被强制停止后，running task 不会永久卡住。
- [x] 服务重启后，任务状态、审批状态、工具日志仍可查询。
- [x] 等待审批的任务恢复后不会重复创建审批项。
- [x] 高风险工具重复执行必须依赖幂等键或被拒绝。
- [x] 重试耗尽后任务进入 dead-letter 或明确 failed 状态。
- [x] Electron 能显示“可重试 / 不可重试 / 等待审批 / 已死信”的状态。

## 建议设计

本地优先实现轻量表或等价结构：

```text
workflow_instances
workflow_steps
workflow_step_attempts
workflow_dead_letters
workflow_compensations
```

如不新增表，也至少要在现有 `agent_runs`、`task_events`、`tool_logs` 中形成稳定协议：

- step name
- attempt number
- status
- started_at
- finished_at
- checkpoint_id
- retryable
- error_code
- compensation_status

## 何时考虑 Temporal

当满足任意条件时再评估 Temporal：

- 单个任务会持续数小时或数天。
- 经常等待人工审批后继续。
- 一个任务跨多个外部系统。
- 必须支持暂停、恢复、取消、补偿。
- 不能接受从头重跑。


## 完成状态（2026-07-21）

- heartbeat 对 stale running task 继续标记 failed，并新增 `task.recovery.dead_letter` 事件，payload 包含 recovery_status、retryable、reason、timeout_seconds。
- 新增 waiting approval 诊断函数，重复运行保持幂等，不新增 Approval，也不重复写同类 recovery event。
- ToolRegistry 对 L3/L4 非幂等工具重复执行增加 idempotency_key 要求；若审批本身不匹配，仍优先进入等待审批，保持旧审批契约。
- 桌面任务事件收到 recovery payload 时展示 waiting/dead-letter 与可重试性。
- 本阶段未新增 workflow_instances/workflow_steps 等表，恢复协议基于现有 TaskEvent、ToolLog、Approval 和 Task 状态。
