# V12 生产级 Agent 系统自查摘要与阶段拆分

日期：2026-07-21
参考标准：用户提供的《生产级 Agent 系统完整标准》
推进原则：**不推进 CI/CD**；V12 只关注本地单用户生产化、本地可重复验证和 Agent 运行治理。

## 1. 自查结论摘要

### 项目是否完整？

对“本地个人 Agent 助手核心闭环”而言，当前项目已经较完整：

- FastAPI 后端。
- Electron 本地桌面端。
- LangBot 入口。
- Celery Worker / Beat。
- PostgreSQL / Redis / Alembic。
- LangGraph Runtime。
- ToolRegistry、审批、审计。
- Model Gateway。
- 记忆、知识库、调度、workspace、可选 sandbox。
- pytest、ruff、mypy 和轻量评测脚本。

但对参考文本定义的完整企业级 Agent 平台而言，仍不完整。

### 是否符合企业级？

暂不考虑多用户、只看本地自用：项目具备企业级雏形，但还不能称为企业级完成态。它已经明显超过“LLM + Prompt + Tools”的 Demo，但还缺少可恢复工作流、强工具校验、统一预算、模型网关可靠性、生产级 RAG、可观测和评测门禁。

### 项目目录是否规范？

当前目录对 FastAPI + Electron 单仓库项目基本规范。后续如果向 Agent 平台演进，建议逐步拆出：

- runtime
- tools
- models
- policies
- rag
- memory
- evals
- deploy/configs（真实需要时再建）

短期不建议一次性大搬家。

## 2. 主要欠缺

| 维度 | 当前短板 | 建议阶段 |
|---|---|---|
| 本地质量 | 测试受本地 `.env` 污染；本地验证边界不稳定 | V12-01 |
| 工具治理 | ToolSpec 有 schema，但统一执行入口缺强制参数校验 | V12-02 |
| Agent 循环 | 主要只有 max_steps，缺工具次数、token、deadline、stop_reason | V12-03 |
| 任务恢复 | Worker kill/restart 后步骤级恢复和 dead-letter 不完整 | V12-04 |
| 模型网关 | 缺健康检查、冷却、熔断、限流、成本统计 | V12-05 |
| RAG/记忆 | 知识库仍偏关键词检索；引用、删除、注入防护、TTL 需加强 | V12-06 |
| 可观测/评测 | 缺统一 trace、任务诊断、Agent 轨迹评测、安全评测 | V12-07 |
| 目录规范 | `backend/agent` 职责偏重，平台边界未拆清 | V12-08 |

## 3. 阶段文档入口

完整拆分后的推进文档见：

1. `docs/v12/index.md`：V12 总索引和推进顺序。
2. `docs/v12/00-scope-and-baseline.md`：范围冻结与现状基线。
3. `docs/v12/01-local-quality-and-config-isolation.md`：本地质量与配置隔离。
4. `docs/v12/02-tool-schema-and-permission-hardening.md`：Tool Schema 与权限硬化。
5. `docs/v12/03-agent-budget-and-loop-control.md`：Agent 预算与循环控制。
6. `docs/v12/04-durable-task-recovery.md`：持久任务恢复。
7. `docs/v12/05-model-gateway-hardening.md`：模型网关可靠性增强。
8. `docs/v12/06-rag-memory-governance.md`：RAG 与记忆治理。
9. `docs/v12/07-observability-and-evaluation-gates.md`：本地可观测与评测门禁。
10. `docs/v12/08-directory-evolution.md`：目录渐进式演进。

## 4. 推荐推进顺序

```text
V12-00 范围冻结
  ↓
V12-01 本地质量与配置隔离
  ↓
V12-02 Tool Schema 与权限硬化
  ↓
V12-03 Agent 预算与循环控制
  ↓
V12-04 持久任务恢复
  ↓
V12-05 模型网关可靠性
  ↓
V12-06 RAG 与记忆治理
  ↓
V12-07 可观测与评测门禁
  ↓
V12-08 目录渐进演进
```

## 5. V12 明确不做

- 不推进 CI/CD。
- 不修 GitHub Actions 作为 V12 目标。
- 不做多用户、多租户、SSO、ABAC。
- 不引入 Temporal、Kafka、Kubernetes、MinIO、Milvus、OpenSearch 等重型基础设施作为默认依赖。
- 不默认启用 shell、浏览器自动化、MCP 外部工具或外部账号写操作。
- 不把未实现能力写成已上线。

## 6. 最优先三件事

1. **本地质量与配置隔离**：先让 `uv run pytest` 不受本地 `.env` 污染。
2. **ToolRegistry 强校验**：所有工具调用在 handler 前通过 schema、权限、审批和版本校验。
3. **Agent 预算守卫**：每个任务都有工具次数、token、deadline、stop_reason 和审计记录。

这三项完成后，项目才更接近“本地生产级 Agent 助手系统”。
