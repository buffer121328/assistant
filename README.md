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
- 轻量评测脚本：`scripts/run_evaluation.py`、`scripts/run_memory_baseline.py`、`scripts/run_memory_release_gate.py`

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
│   ├── knowledge/                   # 知识库导入、解析和检索
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

### 2. Docker Compose 启动后端主链路

```bash
docker compose up --build api celery-worker celery-beat postgres redis
```

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
uv run ruff check .
uv run mypy .

cd frontend/desktop
npm run typecheck
npm run build
```
