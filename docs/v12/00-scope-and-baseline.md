# V12-00 范围冻结与现状基线

## 阶段目标

把 V12 的推进边界固定下来：当前项目按“本地单用户生产化”推进，不按公司级多租户平台推进。

## 范围

审查和规划覆盖：

- 任务生命周期
- LangGraph Agent Runtime
- ToolRegistry 与审批
- Model Gateway
- Memory 与 Knowledge
- Worker / Scheduler
- Electron 桌面端
- 本地测试、文档和运行手册

## 不做事项

- 不做 CI/CD。
- 不引入多租户。
- 不引入大型分布式基础设施。
- 不改动真实业务运行逻辑。

## 当前基线

### 已具备

- FastAPI 后端入口。
- LangBot 远程入口和 Electron 本地桌面端。
- Celery Worker / Beat 后台执行。
- PostgreSQL / Redis / Alembic。
- LangGraph executor。
- ToolRegistry、风险等级、审批、ToolLog。
- Model Gateway、模型池、搜索 fallback。
- 会话、任务、事件、审批、记忆、知识库、调度模型。
- 可选 Docker sandbox。
- pytest、ruff、mypy、coverage 和轻量评测脚本。

### 未达到生产级标准的核心点

- 长任务可恢复能力不完整。
- 工具参数缺统一强校验。
- Agent 缺统一预算守卫。
- 模型网关缺熔断、限流、健康检查、成本统计。
- RAG 仍是基础关键词检索。
- 可观测性和评测门禁还不够系统化。
- Sandbox 未成为所有执行型任务的强制边界。

## 阶段验收标准

- [x] V12 阶段索引存在并明确“不做 CI/CD”。
- [x] 每个后续阶段都有目标、范围、验收标准和不做事项。
- [x] README 指向 V12 阶段文档入口。


## 完成状态（2026-07-21）

- V12 索引、阶段顺序和不做事项已固定。
- README 已提供 V12 入口，并明确本地单用户生产化与不推进 CI/CD。
- 后续阶段文档均包含目标、范围、验收标准和不做事项；运行行为变更仍按独立 OpenSpec + ATDD 批次推进。
