# V12-03 Agent 预算与循环控制

## 阶段目标

为每个 Agent 任务建立统一预算守卫，确保任务不会无限循环、无限调用工具、无限消耗 token 或长时间卡住。

## 当前问题

当前 `ControlledLoop` 已经控制 `max_steps`，但参考标准要求更完整的 Loop Engineering：

- max_steps
- max_tool_calls
- max_tokens
- max_cost
- deadline
- repeat_detection
- no_progress_detection
- confidence_threshold
- stop_reason
- human_escalation

当前项目还缺统一预算对象和可审计的 stop reason。

## 范围

- Agent 执行预算模型。
- LangGraph 节点执行前后的预算检查。
- 工具调用次数统计。
- 模型 token 统计接入预算。
- deadline 和 stop reason。
- Electron 展示预算摘要。

## 不做事项

- 不做真实人民币/美元账单系统。
- 不做多租户预算。
- 不做复杂成本中心。
- 不引入外部计费服务。

## 验收标准

- [x] 每个任务有 `max_steps`、`max_tool_calls`、`max_tokens`、`deadline`。
- [x] 超过任一预算时任务停止，进入 failed 或 cancelled，并写明 `stop_reason`。
- [x] 工具调用前检查剩余工具次数。
- [x] 模型调用后累计 token。
- [x] AgentRun 或 TaskEvent 能看到预算消耗摘要。
- [x] Electron 任务详情能展示 stop reason 和预算摘要。
- [x] 测试覆盖 step 超限、tool 超限、token 超限、deadline 超时。

## 建议设计

新增概念：

```text
RunBudget
├── max_steps
├── max_tool_calls
├── max_input_tokens
├── max_output_tokens
├── max_estimated_cost
├── deadline_at
├── tool_calls_used
├── input_tokens_used
├── output_tokens_used
└── stop_reason
```

执行位置：

- `ControlledLoop.run_step()` 前检查 step/deadline。
- `ToolRegistry.execute()` 前检查工具次数。
- `ModelGateway.chat()` 后回填 token。
- worker 结束时固化预算结果。

## 后续扩展

- repeat detection：连续相同工具调用或相同模型决策达到阈值后停止。
- no progress detection：多轮没有新增 artifact、状态或有效输出时停止。
- cost guard：配置模型单价后计算估算费用。


## 完成状态（2026-07-21）

- 新增 `RunBudget` 与 `BudgetExceededError`，覆盖 step、tool call、input/output token、deadline、stop_reason 和安全 summary。
- `ControlledLoop` 在 step 前检查预算；step 超限保持旧兼容异常 `LoopStepLimitError`，同时记录预算 stop reason。
- ToolRegistry 支持可选 budget，在 handler 前消耗工具调用预算；batch 会整批预扣工具次数。
- AgentGatewayModel 可接收共享 RunBudget，并在模型 usage 返回后累计 token；超限会阻止后续操作并写入模型失败日志。
- 桌面任务事件收到 budget payload 时会展示 stop reason 与预算使用摘要。
- 本阶段不实现真实金额账单、多租户预算或复杂成本中心。
