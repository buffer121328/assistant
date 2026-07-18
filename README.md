# assistant

个人 Agent 助手系统后端。这个项目的目标不是做一个“简单聊天机器人”，而是做一个**可长期演进、可控、可审计、可接入个人工具链的本机 Agent 助手系统**。

当前产品入口状态：

- **LangBot**：主消息入口和结果回推通道。
- **本地 Agent API**：V7 新桌面端契约，提供 `/local/*` 任务、事件、审批和配置接口，供后续 Electron 桌面端使用。
- **Electron Web 桌面端**：V7 新桌面主线，当前已具备安全隔离工程骨架、三栏任务控制台、任务/事件/审批摘要、运行日志、审批/只读 diff 和验证式设置源码。
- **PySide6 桌面小窗口**：历史本机 GUI，V7 期间保留为 legacy/可选能力，不再作为复杂桌面 UI 的新功能主线。

后端由 FastAPI、PostgreSQL、Redis、Celery 和 LangGraph Agent Runtime 组成。所有模型任务统一进入受控 LangGraph 执行层，工具调用经过 ToolRegistry、风险等级、审批和审计约束。

## 项目介绍

本项目解决的是个人日常 Agent 使用中的几个核心问题：

1. **入口统一**：LangBot 和桌面端都进入同一套任务系统，不让不同入口各自散落调用模型。
2. **执行可控**：模型不能随意调用工具，所有工具必须经过 ToolRegistry、allowed tools、risk level、版本和审批控制。
3. **过程可追踪**：任务状态、事件流、模型日志、工具日志、AgentRun 生命周期都会持久化。
4. **人机协同**：高风险工具、计划确认、结果复核支持人工审批，审批后可从 LangGraph checkpoint 恢复。
5. **个人上下文沉淀**：知识库、显式记忆、候选记忆、会话压缩和混合召回让 Agent 能逐步个性化。
6. **本机优先和安全边界**：账号连接加密保存，真实 SMTP/CalDAV/browser provider 在缺少配置时 fail-closed，不伪造成功。

一句话概括：

> 这是一个以任务为中心、以 LangGraph 为执行核心、以 ToolRegistry 为安全边界、以 Memory/Knowledge 为长期上下文的个人 Agent 后端。

## 四个核心功能

当前项目主要围绕四个核心 Agent 能力展开：

### 1. `/plan`：计划与任务拆解

用于把用户目标拆成可执行计划，并在需要时进入计划审批或人工复核。

典型用途：

- 分解一个复杂任务。
- 生成执行步骤。
- 判断需要哪些工具。
- 高风险步骤先等待用户确认。

特点：

- 使用 Agent Profile 选择规划行为。
- Planning Layer 生成结构化执行计划。
- 可触发 `plan_approval` 和 `review_approval`。
- 执行过程写入 TaskEvent、ToolLog、AgentRun。

### 2. `/learn`：学习与资料理解

用于围绕问题或资料进行搜索、阅读、总结和知识沉淀。

典型用途：

- 搜索某个技术主题。
- 结合个人知识库回答问题。
- 从资料中提炼要点。
- 将有价值内容形成候选记忆。

特点：

- 可使用 `search.web`。
- 可接入个人知识库检索。
- 可结合长期记忆、会话摘要和 Memory blocks。
- 输出可被后续评估、记忆候选和回归测试覆盖。

### 3. `/daily`：日常助理与个人事务

用于处理日常信息、提醒、状态查询和个人事务类任务。

典型用途：

- 整理每日事项。
- 创建提醒。
- 查询近期任务状态。
- 结合记忆给出个性化建议。

特点：

- 和 Reminder / Notification outbox 打通。
- 支持桌面通知 poll / ack。
- 能读取本地任务状态和个人上下文。
- 保持本机私有化和 owner-scoped 访问。

### 4. `/office`：办公内容生成与工具调用

用于处理邮件、日历、文档、表格、浏览器等办公场景。

典型用途：

- 起草邮件或办公内容。
- 生成结构化材料。
- 查询或创建日历事件。
- 使用浏览器读取网页内容。
- 在需要时通过受控工具执行个人操作。

特点：

- 工具调用受 ToolRegistry 控制。
- SMTP、CalDAV、browser 等 provider 需要真实账号连接。
- 高风险工具需要审批。
- 工具输入输出写入审计日志，并做敏感信息脱敏。

## 技术栈

### 后端

- Python 3.12
- FastAPI
- SQLAlchemy Async ORM
- Alembic
- PostgreSQL
- Redis
- Celery worker / Celery Beat
- Pydantic Settings
- httpx

### Agent / AI

- LangGraph
- LangGraph PostgreSQL checkpoint saver
- Agent Profile
- Planning Layer
- ToolRegistry
- Capability Registry
- DeepSeek 兼容模型网关
- Tavily 搜索
- 可选 Mem0 语义记忆适配
- Langfuse / Prometheus / Sentry 观测边界

### 桌面端

- V7 新主线：Electron Web 桌面端，源码在 `apps/desktop-web`
- 本地通信：`/local/*` HTTP API + WebSocket 事件流
- Legacy 可选入口：PySide6、QSettings、keyring

### 测试与质量

- pytest
- pytest-asyncio
- pytest-cov
- ruff
- mypy
- GitHub Actions CI
- Docker Compose smoke
- provider smoke
- 离线评测与 Memory release gate

## 项目目录

```text
.
├── apps/
│   ├── api/assistant_api/
│   │   ├── main.py                  # FastAPI 应用创建与运行态初始化
│   │   ├── routes.py                # API 路由聚合入口与 /health
│   │   ├── account_routes.py        # 账号连接
│   │   ├── capability_routes.py     # 能力目录
│   │   ├── channel_routes.py        # LangBot webhook、内部模型网关
│   │   ├── conversation_routes.py   # 会话与消息
│   │   ├── knowledge_routes.py      # 知识库
│   │   ├── memory_routes.py         # Memory Center、policy、retrieval trace
│   │   ├── notification_routes.py   # 提醒和桌面通知
│   │   ├── skill_routes.py          # Skills 生命周期
│   │   ├── task_routes.py           # 任务、事件流、审批
│   │   ├── local_routes.py          # V7 Electron 本地 Agent API 契约
│   │   ├── models.py                # SQLAlchemy ORM 模型
│   │   ├── repositories.py          # 数据访问层
│   │   ├── services.py              # 记忆、状态、分发等服务兼容入口
│   │   ├── task_lifecycle.py        # 任务/审批服务与状态迁移
│   │   ├── worker.py                # Celery app 与任务入队
│   │   ├── worker_runtime.py        # worker 执行编排
│   │   ├── agent_ports.py           # API 层对 Agent Harness ports 的适配器
│   │   └── task_events.py           # 任务事件持久化与事件流记录
│   ├── desktop/assistant_desktop/    # Legacy PySide6 桌面端，可选能力
│   ├── desktop-web/                  # V7 Electron + Vite + React 桌面端源码
│   └── scheduler/                   # 定时维护、监控和心跳入口
├── packages/
│   ├── agent_harness/               # Agent Profile、Planning、Execution、Ports、Compat
│   ├── capabilities/                # Capability Registry
│   ├── evaluation/                  # 离线评测与发布门禁
│   ├── integrations/                # 账号、凭据、SMTP/CalDAV/browser provider
│   ├── knowledge/                   # 知识库导入、解析、检索
│   ├── memory/                      # 记忆安全、召回、候选、consolidation、release
│   ├── model_gateway/               # 模型网关、模型池、脱敏
│   ├── notifications/               # 提醒、通知 outbox、投递租约
│   ├── observability/               # 观测抽象
│   ├── quality/                     # LLM Judge 与质量抽样
│   └── tools/                       # 工具目录、注册、搜索、浏览器、个人工具、沙箱
├── migrations/versions/             # Alembic 迁移
├── prompts/skills/                  # 内置 Skills
├── docs/                            # 方案、MVP/V2/V3/V4/V5/V6 文档
├── scripts/                         # 运维、评测、smoke 脚本
├── tests/                           # acceptance / evals / integration / unit
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── pyproject.toml
└── uv.lock
```

## 项目架构

### 整体架构

![整体架构：入口统一、执行受控、过程可追踪](img/architecture-overview.svg)

### 任务执行时序

![任务执行时序：任务化、可审批、可恢复](img/task-execution-sequence.svg)

### Agent Harness 解耦边界

![Agent Harness 解耦边界：核心依赖 ports，API 提供适配器](img/agent-harness-boundary.svg)

当前生产 worker 路径通过 `agent_ports.py` 注入实现，`compat.py` 只作为未注入 ports 时的旧调用兼容层。

## 项目主要功能

### 四个核心 Agent 场景

| 命令 | 目标 | 典型能力 |
|---|---|---|
| `/plan` | 计划与任务拆解 | 目标理解、步骤拆分、计划审批、复核恢复 |
| `/learn` | 学习与资料理解 | 搜索、知识库检索、总结、候选记忆 |
| `/daily` | 日常助理 | 提醒、状态、个人上下文、日常建议 |
| `/office` | 办公处理 | 邮件、日历、浏览器、文档内容、受控工具调用 |

### 支撑能力

- **任务系统**：任务创建、提交、列表、详情、状态流转。
- **审批系统**：工具审批、计划审批、人工复核，审批后可恢复执行。
- **事件系统**：任务事件持久化，支持事件流续读和终态退出。
- **AgentRun**：记录每次 worker 执行尝试，便于审计和排障。
- **模型网关**：统一 DeepSeek 兼容模型调用、脱敏、模型日志和模型池。
- **工具系统**：ToolRegistry 统一管理工具 schema、版本、风险、审批、并行安全。
- **记忆系统**：显式记忆、候选记忆、短期/长期记忆、混合召回、consolidation、policy rollout/rollback。
- **知识库**：文件上传、解析、分块、去重、搜索。
- **账号连接**：加密保存账号凭据，支持 SMTP、CalDAV、browser provider。
- **提醒通知**：提醒创建、取消、通知 outbox、桌面 poll/ack、LangBot 投递。
- **本地桌面契约**：`/local/*` 支持任务创建、任务快照、事件游标、WebSocket 事件流、日志和审批决策。
- **Electron 控制台**：三栏任务队列、活动线程和检查器，支持任务/事件/审批摘要、继续对话、运行日志、工具审批、文件引用、只读 diff、命令输出、空态和设置验证。
- **Legacy 桌面端**：PySide6 入口仍可选保留，用于任务、审批、账号、知识库、提醒、Skills、Memory Center。

## 后续如何扩展新功能

这个项目的优势是：新增能力不需要直接把逻辑塞进 worker 或模型提示词，而是沿着固定扩展点演进。

### 扩展一个新的 Agent 场景

例如在 `/plan`、`/learn`、`/daily`、`/office` 之外新增 `/travel`：

![扩展新 Agent 场景：不要塞进 worker，走固定扩展点](img/new-agent-scenario-flow.svg)

建议步骤：

1. 新增或调整 Agent Profile。
2. 明确输入命令、任务类型和 workflow key。
3. 声明该场景允许使用哪些工具。
4. 如需要新知识，新增 Skill。
5. 如需要新动作，新增 ToolSpec 并注册到 ToolRegistry。
6. 补 acceptance 测试，覆盖用户可见行为。
7. 更新 README 或对应 docs。

### 扩展一个新工具

![扩展新工具：从实现到审计的受控链路](img/new-tool-flow.svg)

新工具必须明确：

- 工具名
- 输入 schema
- 风险等级
- 是否需要审批
- 是否可并行
- 是否记录自己的日志
- 失败时如何脱敏

### 扩展一个新外部账号能力

例如新增一个新的邮件、日历或文档 provider：

1. 在 `packages/integrations` 中实现 provider。
2. 通过 `AccountConnectionService` 加密保存凭据。
3. 在工具层暴露受控动作。
4. 在 ToolRegistry 中设置风险等级和审批策略。
5. 补 provider smoke 或集成测试。

### 扩展一组新 API

新增 API 时优先按领域创建新的 `*_routes.py`，不要继续膨胀 `routes.py`。

推荐结构：

```text
apps/api/assistant_api/new_feature_routes.py
packages/new_feature/
tests/acceptance/test_new_feature.py
```

## 项目优势

### 1. 不是简单聊天，而是任务化 Agent 系统

所有入口都落到 Task，任务有状态、有事件、有 AgentRun、有日志，可以排查、恢复和审计。

### 2. 模型和工具之间有安全边界

模型不能直接执行任意动作。工具必须通过 ToolRegistry，受 allowed tools、risk level、approval、version、source availability 控制。

### 3. 支持人工审批和 checkpoint 恢复

高风险工具、计划和复核可以中断等待用户确认，确认后通过 LangGraph checkpoint 恢复，而不是重跑或丢上下文。

### 4. 个人上下文是长期资产

知识库、记忆、会话压缩、候选记忆、混合召回和 policy rollout 让系统能逐步沉淀个人偏好和事实，而不只是单轮问答。

### 5. 本机优先，敏感能力 fail-closed

账号、浏览器、邮件、日历等能力默认不伪造成功；缺少密钥或账号配置时直接 fail-closed，避免误导用户。

### 6. 高内聚、低耦合方向明确

API 路由已按领域拆分，Agent Harness 通过 ports 依赖抽象，API 层提供适配器，后续新增能力可以走固定扩展点。

### 7. 测试和质量门禁完整

当前有 acceptance、integration、evals、unit 测试，配合 ruff、mypy、coverage、CI 和 smoke，适合持续演进。

## 如何部署启动

更完整的配置说明见 [`docs/mvp-startup-config.md`](docs/mvp-startup-config.md)。如果想从桌面端按钮一路追到后端 API、服务函数和 Agent 执行层，见 [`docs/frontend-backend-flow.md`](docs/frontend-backend-flow.md)。

### 1. 准备环境

Python 版本由 `.python-version` 固定为 3.12，依赖使用 `uv` 管理。

```bash
uv sync
```

核心后端默认不安装 PySide6、Playwright、Office 解析/生成和观测 SDK。按能力安装可选依赖：

```bash
uv sync --extra desktop-pyside
uv sync --extra browser-automation
uv sync --extra office
uv sync --extra observability
```

常用组合示例：

```bash
uv sync --extra office --extra browser-automation
```

复制示例配置：

```bash
cp .env.example .env
```

至少需要填写：

```text
LOCAL_API_TOKEN=
CREDENTIAL_MASTER_KEY=
DATABASE_URL=
REDIS_URL=
```

按需配置外部能力：

```text
LANGBOT_WEBHOOK_SECRET=
LANGBOT_API_BASE_URL=
LANGBOT_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=
TAVILY_API_KEY=
SMTP / CALDAV / Browser / Langfuse / Sentry 等配置
```

`.env`、Token、Cookie、API Key、私有 URL 不提交仓库。

### 2. Docker Compose 启动后端

```bash
docker compose up --build -d
```

Compose 中的 `migrate` 一次性服务会在 API、worker、Beat 启动前执行：

```bash
alembic upgrade head
```

API 默认映射到：

```text
127.0.0.1:8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

### 3. 本地分进程启动

如果不使用 Compose，可以分别启动 API、worker、Beat。

先运行数据库迁移：

```bash
uv run alembic upgrade head
```

启动 API：

```bash
uv run uvicorn --app-dir apps/api assistant_api.main:app --reload
```

启动 Celery worker：

```bash
PYTHONPATH=apps/api:. uv run celery -A assistant_api.worker:celery_app worker --loglevel=INFO
```

启动单实例 Beat：

```bash
PYTHONPATH=apps/api:. uv run celery -A assistant_api.worker:celery_app beat --loglevel=INFO
```

### 4. 启动 Electron 桌面端

V7 的新桌面主线是 Electron Web 桌面端。当前工程位于 `apps/desktop-web`，开发模式需要先安装 Node 依赖，并确保 Python API 已按前文启动。

```bash
cd apps/desktop-web
npm ci
npm run dev
```

当前 Electron 源码覆盖：

- 主窗口、菜单、托盘占位和安全隔离基线。
- 本地 API 连接状态、API 地址和用户设置。
- 三栏任务控制台：任务队列、活动线程和检查器。
- 任务数量、运行中任务、待审批任务、已完成任务、事件数和变更数摘要。
- 任务详情、继续对话、WebSocket 事件流恢复、空态和刷新入口。
- 运行日志、审批面板、审批原因、风险等级、文件引用、只读 diff、命令输出和验证式设置保存。

### 5. 打包 Electron 桌面端

V7-06 采用 **external installed mode**：Electron 安装包只包含桌面壳和 Web UI，不内置 Python runtime、`.venv`、PostgreSQL、Redis、PySide6、Playwright、Office 依赖或本地模型。用户需要单独启动 Python Agent Server。

```bash
cd apps/desktop-web
npm ci
npm run build
npm run dist:dir
```

生成平台安装包：

```bash
npm run dist
```

发布边界检查：

```bash
uv run python scripts/ops/desktop_web_release_check.py
```

打包配置见 `apps/desktop-web/electron-builder.json`，发布记录见 `apps/desktop-web/RELEASE.md`。当前尚未在本工作区实际生成安装包，因此包体、冷启动耗时和空闲内存仍记录为 `not measured`，不能声明生产自动更新或跨平台签名已完成。

### 6. 启动 Legacy PySide6 桌面端

旧 PySide6 入口保留为可选能力。使用前需安装：

```bash
uv sync --extra desktop-pyside
```

```bash
uv run assistant-desktop
```

桌面端不会自动启动后端。使用桌面端前需要确保：

- PostgreSQL 已启动。
- Redis 已启动。
- Alembic 迁移已完成。
- FastAPI 已启动。
- Celery worker 已启动。
- 数据库中已准备用户。
- 本机 API token 已配置。

### 7. 常用验证命令

```bash
uv run pytest
uv run pytest --cov
uv run ruff check .
uv run mypy .
uv lock --check
uv run python scripts/ops/desktop_web_release_check.py
```

可选 smoke：

```bash
uv run python -m scripts.ops.compose_smoke
uv run python -m scripts.ops.provider_smoke
```

注意：部分集成测试会绑定本地 `127.0.0.1` 临时端口；在受限 sandbox 中运行可能需要放开本地端口绑定权限。
