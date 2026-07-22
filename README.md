# assistant

个人 Agent 助手系统后端与桌面控制台。项目目标不是做一个简单聊天机器人，而是构建一个**可长期演进、可控、可审计、可接入个人工具链的本机 Agent 助手系统**。

## 项目简介

系统以 FastAPI 后端为核心，统一接收 LangBot 远程入口和 Electron 本地桌面端请求，将用户需求转成可追踪的任务、会话、事件、审批和运行日志。Agent 执行层基于 LangGraph/Agent Harness，工具调用统一经过 ToolRegistry、风险等级、审批、审计和 owner-scoped 边界。

当前主线入口：

- **LangBot**：远程消息入口和结果回推通道，进入任务系统前先做结构化 intent 判定。
- **Electron Web 桌面端**：本地三栏任务控制台，使用 `/local/*` API 管理任务、事件流、审批、设置和远程桥接会话。
- **Celery Worker / Beat**：负责后台任务执行、超时维护、补偿扫描和周期性调度。
- **Agent Runtime**：集中处理计划、学习、日常、办公等任务，并通过受治理工具访问搜索、记忆、工作区和外部能力。

## 核心功能

| 功能 | 当前能力 |
|---|---|
| 任务化 Agent | 将 `/plan`、`/learn`、`/daily`、`/office` 等需求转为有状态任务，支持续写、事件流、日志和结果回推。 |
| 受控工具调用 | 工具由 ToolRegistry 注册，按风险等级执行；高风险动作需要审批，工具调用和结果写入审计事件。 |
| 桌面任务控制台 | Electron + React 展示任务列表、详情、timeline、logs、approvals、changes、settings 和 remote bridge。 |
| 远程桥接账本 | `/api/remote-control/bridge/sessions` 记录 LangBot 入站消息、任务绑定、回推状态和重放信息。 |
| Owner-scoped 本地 API | `/local/*` 请求显式携带 `user_id`，用于任务、会话、审批、记忆和账号连接的 owner 校验。 |
| 记忆与上下文 | 支持 conversation、knowledge、agentic memory、session workspace 和只读 workspace context 工具。 |
| 可演进能力边界 | Skill acquisition、schedule、prompt bootstrap、search provider chain 等能力保持受治理、可测试、可回滚。 |

## 技术栈

### 后端

- Python 3.12
- FastAPI / Uvicorn
- SQLAlchemy Async / Alembic / PostgreSQL
- Redis / Celery / Celery Beat
- Pydantic Settings / structlog

### Agent 与工具治理

- LangGraph / Agent Harness
- ToolRegistry、风险等级、Approval、Audit Event
- Tavily → Brave → DuckDuckGo 搜索 provider chain
- Agentic Memory、Dynamic Prompt Bootstrap、Skill 管理
- Session Workspace、Workspace Context、可选 Docker sandbox

### 桌面端

- Electron
- Vite
- React
- TypeScript

### 测试与质量

- pytest / pytest-asyncio / respx / fakeredis
- ruff / mypy / coverage
- 轻量评测脚本：`scripts/run_evaluation.py`、`scripts/run_memory_baseline.py`、`scripts/run_memory_release_gate.py`、`scripts/run_v12_governance_gate.py`

## 目录说明

```text
.
├── backend/
│   ├── app/                         # FastAPI 应用壳、路由、schema、依赖和支持模块
│   ├── channels/
│   │   ├── desktop/                 # `/local/*` API、WebSocket 事件流、审批桥接
│   │   └── langbot/                 # LangBot webhook、intent 路由、结果回推
│   ├── domain/                      # SQLAlchemy model、服务层、任务生命周期
│   ├── infrastructure/              # 配置、数据库、认证、日志、观测基础设施
│   ├── agent/                       # Agent Harness、LangGraph executor、治理、记忆、规划、工具管理
│   ├── capabilities/                # 能力注册与发现
│   ├── evaluation/                  # 离线评测与发布门禁
│   ├── features/                    # plan / learn / daily / office 四类任务入口
│   ├── integrations/                # 账号、凭据和外部 provider 适配
│   ├── knowledge/                   # 知识库导入、解析和检索实现
│   ├── rag/                         # 渐进式 RAG facade（source/citation/delete contract）
│   ├── model_gateway/               # 模型网关、模型池、脱敏
│   ├── notifications/               # 通知 outbox 和投递租约
│   ├── resources/                   # prompt 模板和内置 skillpacks
│   ├── scheduler/                   # 定时维护、监控和心跳入口
│   └── workers/                     # Celery app 和后台任务入口
├── frontend/
│   └── desktop/                     # Electron + Vite + React 桌面端源码
├── legacy/
│   └── desktop-qt/                  # 历史 Qt 桌面端源码，仅保留参考和旧测试
├── docs/                            # 启动配置、阶段文档、前后端链路和设计说明
├── img/                             # README 架构图 SVG
├── openspec/                        # OpenSpec 变更与规范资料
├── scripts/                         # 运维、评测、smoke 脚本
├── tests/                           # acceptance / evals / integration / unit
├── docker-compose.yml               # 后端、PostgreSQL、Redis、Celery、Beat 编排
├── Dockerfile                       # API 镜像
├── Dockerfile.ops                   # 运维/辅助镜像
├── pyproject.toml                   # Python 依赖、测试、lint、类型配置
└── uv.lock                          # uv 生成的锁文件
```

## 项目架构

### 整体架构

![整体架构：LangBot 与 Electron 统一进入受控任务系统](img/architecture-overview.svg)

### 任务执行时序

![任务执行时序：任务化、可审批、可恢复](img/task-execution-sequence.svg)

### Agent Harness 解耦边界

![Agent Harness 解耦边界：核心依赖 ports，API 提供适配器](img/agent-harness-boundary.svg)

### 扩展新 Agent 场景

![扩展新 Agent 场景：沿 feature 和 profile 扩展，不绕过任务系统](img/new-agent-scenario-flow.svg)

### 扩展新工具

![扩展新工具：实现、注册、风险、审批、审计一条链路](img/new-tool-flow.svg)

## 如何启动

### 1. 准备环境

```bash
cp .env.example .env
uv sync
```

根据本地情况补齐 `.env`。敏感值只放在本地 `.env`，不要提交真实 Token、Cookie、API Key 或私有 URL。

`backend/resources/` 保存随源码发布的内置源资源（Prompt 和 Skill）；`var/` 是可变运行时根目录，保存 managed Skills、Prompt 覆盖、任务产物、知识库和会话工作区，不随源码目录迁移。

### 2. Docker Compose 启动后端主链路

```bash
docker compose up --build assistant-api celery-worker celery-beat postgres redis
```

首次启动会先运行一次 `runtime-init`，为 named volumes 分配容器内应用用户的写权限；它完成后 API、Worker 和 Beat 才会启动。默认占位模型、搜索和 LangBot 配置仅支持基础服务启动，不能完成真实外部调用。

常用服务：

- API：`http://127.0.0.1:8000`
- 健康检查：`GET /health`
- 本地桌面端配置：`GET /local/config`
- LangBot webhook：`POST /api/webhooks/langbot`

### 3. 本地分进程启动

适合调试 API、Worker 或任务执行链路：

```bash
uv run uvicorn app.main:create_app --factory --reload --app-dir backend
uv run celery -A workers.worker:celery_app worker --loglevel=info
uv run celery -A workers.worker:celery_app beat --loglevel=info
```

如需数据库迁移：

```bash
uv run alembic upgrade head
```

### 4. 启动 Electron 桌面端

```bash
cd frontend/desktop
npm install
npm run dev
```

桌面端 Settings 中需要配置：

- Local API URL，例如 `http://127.0.0.1:8000`
- Workdir，本地任务工作目录
- User ID，用于 `/local/*` owner-scoped 接口校验

### 5. 常用验证命令

```bash
uv run pytest
uv run pytest --cov
uv run ruff check .
uv run mypy .
uv lock --check

cd frontend/desktop
npm run typecheck
npm run build
```

后端验收测试中，凡是断言默认配置或创建测试 App 的场景，都应显式传入测试 `Settings`（必要时使用 `_env_file=None`），不得依赖仓库根目录的个人 `.env`。生产运行仍通过现有 `load_settings()` 读取本地配置；真实 Token、Cookie、API Key、私有 URL 和认证头只保存在未提交的本地 `.env`，测试和文档只使用占位值。

## V11 运行时完整性与阶段兼容说明

V11 第一阶段依据 `docs/v11/01-feature-implementation-audit.md` 与 `docs/v11/02-task-schedule-runtime-audit.md` 收敛运行时主链路：普通任务详情和本地审批列表执行 owner-scoped 校验；Electron Web 桌面端从持久化审批 API 恢复 approval ID；`ToolLog` 投影到 logs 面板；消息事件使用 `task.message.delta` / `task.message.completed`；任务终态继续发布 `task.completed`、`task.failed` 和 `task.status.changed`；`TaskService` 统一写入 conversation assistant 结果，worker 不再重复追加；`task.start_background` 与到期 schedule 会返回或记录真实的 `queued` / `enqueue_failed` 状态。任务进入 `waiting_approval` 时，事件包含审批数量、请求工具和用户可读摘要。

为保持历史验收契约可追踪，以下短说明保留阶段关键词，但不替代 `openspec/` 与 `docs/` 中的完整设计：

- **LangBot** 是真实 LangBot 主消息入口和结果回推通道；Electron Web 桌面端通过 `/local/*` 使用同一任务与审批边界。
- **MVP 阶段 09**：Docker Compose 同时运行 API、`celery-worker` 与 Celery Beat；heartbeat 负责超时 `running` 任务失败和 `pending` 任务补偿，真实 LangBot 负责结果回推。
- 搜索命令使用 `TAVILY_BASE_URL` 与 `TAVILY_API_KEY` 本地配置；`/learn` 通过 `search.web` 检索资料，`/daily` 通过 `search.web` 获取需要的公开信息。
- **V2-02**：`v2.planner` 与 `v2.researcher` 读取 `backend/resources/skillpacks/*/SKILL.md`；发现的 Skill 不会自动启用。
- **V2-03 在 V2-02 规划层上**接入 LangGraph 与 ToolRegistry；外部 MCP Server 默认不启用。V3-08 已移除 Deepeval，当前回归入口由 V2-05 评测与回归阶段维护。
- **V2-04**：Celery Beat 按单实例部署；TaskService 保持状态写入边界，记忆 `access_count` 只由明确读取更新，演进建议不会自动修改运行配置；审批态保持 `waiting_approval`。
- **V2-05**：`scripts/run_evaluation.py` 读取 `core_commands.json` 并生成 `v2-05.json`；离线评测不替代 pytest、ruff 和 mypy。
- **V6-00**：`scripts/run_memory_baseline.py` 使用 `adaptive_memory_v6_00.json` 保存基线；该阶段记录中的 adaptive candidate 尚未上线，后续状态以对应 OpenSpec change 为准。
- 任务类型扩展约定写作 `backend/features/<task_type>`；当前实际入口位于 `backend/features/`。通道实现位于 `backend/channels/langbot` 与 `backend/channels/desktop`，worker 入口为 `workers.worker:celery_app`。
- `/office` 默认不执行搜索，只有任务计划和受治理工具明确需要时才解析对应能力。
- 历史阶段边界：当前不承诺完整 MCP Gateway、深度浏览、真实 Office 文件生成或邮件/日历接入；这些能力必须经过对应可选集成、审批和运行时治理后再启用。
- **V11 数据治理基础阶段**：`processed_messages` 的幂等边界使用 `platform + adapter + message_id`；`model_logs` 增加可空 `agent_run_id`，主 worker 模型调用按 AgentRun 归属，直接聊天/子 Agent 等无 run 上下文的日志保持 NULL。该阶段不删除 legacy 调度表、不拆分消息账本、不实现 memory outbox consumer。
- **V11 调度治理阶段**：`agent_schedules` 与 `agent_schedule_runs` 是唯一运行时调度主线；旧 `scheduled_task_runs`、`CronScheduler` 和 `ScheduledTaskRunRepository` 已移除，迁移支持回滚恢复旧表结构。
- **V11 记忆索引 Outbox 阶段**：maintenance heartbeat 有界消费 `memory_index_outbox`，支持 `add`、`rebuild`、`delete`，状态按 `pending/retry -> processing -> succeeded/failed` 流转；失败最多重试三次，超时 processing lease 可恢复，未配置语义索引时明确进入 failed 而不会永久占用 pending count。

## V12 生产级 Agent 系统自查

V12 文档入口见 `docs/v12/index.md`，自查摘要见 `docs/v12/01-production-agent-system-self-audit.md`。V12 不推进 CI/CD，重点按阶段补强本地单用户生产化能力：本地质量与配置隔离、Tool Schema 强校验、Agent 预算守卫、持久任务恢复、模型网关可靠性、RAG/记忆治理、本地可观测与评测门禁、目录渐进演进。

首批 V12-00/01/02 已完成：范围基线固定为本地单用户生产化；默认配置验收使用显式测试 Settings 隔离个人 `.env`；ToolRegistry 在 handler 前强制校验 JSON Schema、allowlist、source、snapshot/version 和高风险审批，batch 会在调度任一 handler 前完成全量预检，Registry ToolLog 对输入、输出和错误统一脱敏并限制长度。timeout、retry、idempotency、dry-run、compensation 和 required permissions 当前仅作为 ToolSpec 治理元数据，尚未实现完整执行语义。第二批 V12-03/04/05 已完成轻量本地实现：Agent 运行预算提供 step/tool/token/deadline stop reason 与安全摘要；heartbeat 基于现有 TaskEvent/ToolLog 写入 stale running dead-letter 和 waiting approval recovery 诊断；ToolRegistry 拒绝缺少 idempotency key 的高风险非幂等重复执行；模型池支持节点 cooldown、同池 fallback、本地 RPM/TPM 跳过和估算成本诊断；桌面事件可展示预算和恢复状态。后续阶段仍需深化完整 workflow step attempt 表、跨进程持久熔断历史、完整 RAG、可观测性和 Agent 评测体系。 第三批 V12-06/07/08 已完成轻量切片：知识结果返回 source id、citation、no-answer 与 `untrusted_document` 标记，owner 删除文档后 chunk 立即不可检索；任务以 task id 作为本地 trace id，并提供聚合 event/model/tool/approval/retrieval/error 的 diagnostics API；新增 V12 治理 fixture 与 `scripts/run_v12_governance_gate.py` 本地 JSON 报告；目录只新增 `backend/rag` facade，旧 `backend/knowledge` 暂保留兼容。pgvector、rerank/query rewrite、完整答案验证、分布式 trace 和 runtime/tools/models/policies 的物理迁移仍未实现。 后续 grounding 切片增加了机器可校验 `citation_token`、untrusted retrieval context formatter 和 citation reference validator；治理门禁现在会在临时 SQLite/knowledge root 中真实执行 KnowledgeService 导入、分块与检索，并输出 recall@k、abstention 和 instruction-risk 指标。当前 validator 只验证引用是否来自本次检索，不声称完成语义蕴含或事实正确性判断。

## 后端源码注释覆盖

当前后端 `backend/` Python 源码中的类、同步函数和异步函数均已补充简短 docstring；复杂签名包含 `Args:` 参数说明。该工作只补充代码说明，不改变运行逻辑或外部 API 行为。

验证命令：

```bash
python3 - <<'PY'
import ast
import pathlib
import py_compile

missing = []
compile_errors = []
for path in sorted(pathlib.Path("backend").rglob("*.py")):
    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as exc:
        compile_errors.append((str(path), str(exc)))
        continue
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node) is None:
                missing.append((str(path), node.lineno, type(node).__name__, node.name))
print(f"missing={len(missing)} compile_errors={len(compile_errors)}")
PY
uv run ruff check backend
```
