# V12-07 本地可观测与评测门禁

## 阶段目标

让每个任务都能回答“发生了什么、为什么这么做、失败在哪里、花了多少资源、是否通过评测”，并建立本地发布前的 Agent 评测门禁。

## 本批范围（2026-07-21）

- 复用 task id 作为本地 trace id，不新增数据库列。
- 聚合 TaskEvent、ModelLog、ToolLog、Approval、MemoryRetrievalTrace 和错误摘要。
- 增加 RAG/Agent 治理确定性 fixture 与可保存 JSON 报告的本地命令。
- 保持 Langfuse 可选，不引入云端监控强依赖。

## 本批实现

### Task trace 与 diagnostics

`TaskResponse.trace_id == TaskResponse.task_id`。当前日志表已经以 `task_id` 关联，因此本地单用户阶段无需新增独立 trace 表。

新增：

```text
GET /api/tasks/{task_id}/diagnostics?user_id=...
```

owner 可看到：

- task 基础状态与 trace id；
- TaskEvent；
- 模型调用摘要；
- 工具调用状态与摘要；
- 审批类型、对象和状态；
- 最新记忆 retrieval trace 与 `memory:{memory_id}` source id；
- 安全裁剪后的错误摘要。

诊断接口不返回完整原始凭据或无限长度日志；文本继续复用 `sanitize_text`。

### 本地评测门禁

新增数据集：

```text
tests/evals/datasets/rag_governance_v12_06.json
tests/evals/datasets/agent_governance_v12_07.json
```

新增入口：

```bash
uv run python scripts/run_v12_governance_gate.py
```

默认报告：

```text
var/evals/v12-governance-report.json
```

脚本返回码：通过为 `0`，用例失败为 `1`，fixture 无效为 `2`。报告覆盖 citation、abstention、deletion、injection、trace、trajectory、quality 和 security 类别。它是确定性本地门禁，不替代 pytest、真实模型回归或人工安全验证。

## 验收标准

- [x] 每个任务响应有 `trace_id`，当前与 task id 等价。
- [x] diagnostics 能看到模型、工具、审批、检索来源、事件和错误摘要。
- [x] Agent 轨迹 fixture 覆盖工具权限拒绝、审批、预算停止和 trace 关联。
- [x] 输出质量 fixture 覆盖 citation、no-answer 和检索来源。
- [x] 安全 fixture 覆盖 prompt injection、secret-safe summary 和越权工具拒绝。
- [x] 本地升级前可运行脚本并保存机器可读评测报告。

## 本批不做

- 不做 CI/CD。
- 不引入 Prometheus/Grafana 生产栈。
- 不把 LLM-as-a-Judge 作为唯一标准。
- 不新增分布式 trace backend。

## 后续深化

- 让 ModelLog/ToolLog 增加 token、latency、estimated cost 等结构化字段，而不只依赖摘要。
- 将线上失败任务脱敏后转成待审核 eval candidate，避免自动把用户原文写入数据集。
- 增加真实 Agent trajectory replay，并把预算、审批、工具选择作为程序化 evaluator。

## 真实 RAG 执行门禁补充

`run_v12_governance_gate.py` 现在除静态治理 fixture 外，还会运行真实 RAG evaluator：

```text
tests/evals/datasets/rag_retrieval_v12_06.json
```

报告新增 `rag_retrieval`，包含：

- `mean_recall_at_k`
- `abstention_accuracy`
- `instruction_risk_accuracy`
- 每个 case 的实际来源、answerable 状态和风险标记

该 evaluator 使用生产 `KnowledgeService` 的导入、分块和搜索代码，不调用真实模型或外部服务。
