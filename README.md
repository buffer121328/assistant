# assistant-api

个人 Agent 助手系统后端。项目以飞书 / 企业微信作为手机端入口，后端负责消息接入、任务管理、Agent 调度、模型网关、工具调用、结果推送和审计。

当前仓库已完成 MVP 阶段 03 Feishu Webhook：在 Persistence & Task Service 能力之上新增飞书 Webhook 接入、请求校验、消息归一化、用户绑定查询、消息去重和命令入库。后续按 OpenSpec + ATDD 的 phase-by-phase 范式继续推进。

## 项目介绍

目标是构建一个个人 Agent 助手系统：

- 通过飞书 / 企业微信接收用户指令。
- 使用 FastAPI 作为后端 API 中枢。
- 使用 PostgreSQL 保存用户、任务、记忆、模型日志和工具日志。
- 使用 Redis + Celery 处理异步任务。
- 通过 Model Gateway 统一调用 DeepSeek 等模型。
- 通过 Dify Workflow 执行 V1/MVP 固定工作流。
- 通过 Tavily 等工具支持搜索和资料整理。

完整方案见 `docs/个人Agent助手系统完整方案.md`。MVP 阶段开发文档入口见 `docs/mvp/index.md`。

## 启动方式

安装依赖：

```bash
uv sync
```

启动本地开发服务：

```bash
uv run uvicorn --app-dir apps/api assistant_api.main:app --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

初始化数据库 schema：

```bash
DATABASE_URL="postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>" uv run alembic upgrade head
```

当前阶段提供 `GET /health`、最小 Task API 和飞书 Webhook 接入。Task API 需要可连接的 PostgreSQL `DATABASE_URL` 和已存在的用户记录；飞书 Webhook 创建任务前还需要已存在的 `platform_accounts` 绑定，其中 `platform = feishu`，`platform_user_id` 为飞书 `open_id`。尚未实现模型网关、Dify、Tavily、Redis 调度或 Celery worker。

## 如何配置

配置从安全默认值和环境变量加载。本地 `.env` 可用于开发环境，但 `.env` 和 `.env.*` 不提交仓库；示例配置只能放在 `.env.example`，且只能包含占位值。

当前阶段支持的配置项：

- `APP_ENV`：应用环境，默认 `local`。
- `LOG_LEVEL`：日志级别，默认 `INFO`。
- `SERVICE_NAME`：API 服务名称，默认 `assistant-api`。
- `DATABASE_URL`：PostgreSQL asyncpg URL。默认值是占位值；运行迁移或 Task API 前必须通过环境变量或本地 `.env` 提供真实可连接地址。
- `REDIS_URL`：Redis URL，占位配置；本阶段不连接 Redis。
- `SENTRY_DSN`：Sentry DSN，可为空；本阶段不初始化 Sentry 连接。
- `FEISHU_WEBHOOK_VERIFICATION_TOKEN`：飞书 Webhook verification token，默认是占位值；用于校验请求 `token`。
- `FEISHU_WEBHOOK_SIGNING_SECRET`：飞书 Webhook signing secret，默认是占位值；用于校验请求签名。

本阶段不会读取或要求 DeepSeek、Dify、Tavily 等真实密钥，也不会连接这些外部服务。飞书 Webhook 配置只用于请求校验；真实值仅放在本地 `.env` 或运行环境变量中，不提交仓库。

## 项目目录介绍

目录规则：

- `apps/` 放可启动进程入口；FastAPI API 服务代码放在 `apps/api/assistant_api/`，后续 worker 和 scheduler 分别放在 `apps/worker/`、`apps/scheduler/`。
- `packages/` 放可被多个进程复用的内部模块；例如模型网关、Agent Harness、工作流、工具、记忆、schemas 和通用能力。
- `configs/` 放占位配置、模型、工作流、工具和环境相关模板；不得写入真实密钥。
- `infra/` 放 Docker、CI 和部署相关文件。
- `migrations/` 放 Alembic 数据库迁移文件。
- `scripts/` 放迁移、运维等本地脚本。
- `prompts/` 放系统提示词、命令提示词和工作流提示词。
- `tests/acceptance/` 放用户可观察行为验收测试，`tests/integration/` 放跨模块集成测试，`tests/unit/` 放纯单元测试，`tests/fixtures/` 放测试夹具。
- 根目录只放项目元数据、文档入口和工具配置；不要新增根层业务包。

```text
.
├── AGENTS.md
├── README.md
├── alembic.ini
├── apps/
│   ├── api/
│   │   └── assistant_api/
│   │       ├── __init__.py
│   │       ├── config.py
│   │       ├── database.py
│   │       ├── errors.py
│   │       ├── feishu.py
│   │       ├── logging.py
│   │       ├── main.py
│   │       ├── models.py
│   │       ├── repositories.py
│   │       ├── routes.py
│   │       ├── schemas.py
│   │       └── services.py
│   ├── scheduler/
│   └── worker/
├── configs/
│   ├── environments/
│   ├── models/
│   ├── tools/
│   └── workflows/
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── 个人Agent助手系统完整方案.md
│   ├── runbooks/
│   └── mvp/
│       ├── index.md
│       ├── 00-mvp-scope.md
│       ├── 01-foundation.md
│       ├── 02-persistence-task-service.md
│       ├── 03-feishu-webhook.md
│       ├── 04-model-gateway.md
│       ├── 05-dify-agent-harness.md
│       ├── 06-search-content-commands.md
│       ├── 07-memory-status-dispatcher.md
│       └── 08-mvp-acceptance-release.md
├── infra/
│   ├── ci/
│   └── docker/
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 202606200001_create_mvp_tables.py
│       └── 202606210001_create_processed_messages.py
├── openspec/
│   └── changes/
│       ├── feishu-webhook/
│       └── persistence-task-service/
├── packages/
│   ├── agent_harness/
│   ├── common/
│   ├── memory/
│   ├── model_gateway/
│   ├── runtime/
│   ├── schemas/
│   ├── tools/
│   └── workflows/
├── prompts/
│   ├── commands/
│   ├── system/
│   └── workflows/
├── scripts/
│   ├── migrate/
│   └── ops/
├── tests/
│   ├── acceptance/
│   │   ├── test_feishu_webhook.py
│   │   ├── test_foundation.py
│   │   └── test_persistence_task_service.py
│   ├── fixtures/
│   ├── integration/
│   └── unit/
├── pyproject.toml
├── pyrightconfig.json
├── uv.lock
└── .python-version
```

- `AGENTS.md`：协作和开发规则。
- `README.md`：项目说明、启动、配置、目录和功能说明；每个阶段完成后更新。
- `alembic.ini`：Alembic 配置入口。
- `apps/api/assistant_api/`：当前 FastAPI API 服务应用包。
- `apps/api/assistant_api/main.py`：FastAPI 应用入口，暴露 `assistant_api.main:app`。
- `apps/api/assistant_api/config.py`：基础配置加载。
- `apps/api/assistant_api/database.py`：SQLAlchemy 异步数据库引擎、sessionmaker 和 FastAPI session 依赖。
- `apps/api/assistant_api/models.py`：MVP 阶段数据库模型。
- `apps/api/assistant_api/feishu.py`：飞书 Webhook 请求校验、消息归一化、命令映射、绑定查询、去重和任务入库逻辑。
- `apps/api/assistant_api/repositories.py`：Task 和 Feishu Webhook 持久化读写封装。
- `apps/api/assistant_api/routes.py`：当前包含 `GET /health`、最小 Task API 和 `POST /api/webhooks/feishu`。
- `apps/api/assistant_api/schemas.py`：Task API 请求和响应 schema。
- `apps/api/assistant_api/services.py`：Task Service 创建、查询、状态流转和结果记录逻辑。
- `apps/api/assistant_api/errors.py`：统一 JSON 错误响应。
- `apps/api/assistant_api/logging.py`：结构化日志初始化和敏感字段过滤。
- `apps/worker/`：后续 Celery worker 进程入口。
- `apps/scheduler/`：后续调度进程入口。
- `packages/`：后续内部共享模块。
- `configs/`：占位配置和模板目录，不写真实密钥。
- `infra/`：基础设施、Docker 和 CI 目录。
- `migrations/`：Alembic 迁移入口和 MVP 初始表结构迁移。
- `scripts/`：本地迁移和运维脚本目录。
- `prompts/`：提示词资产目录。
- `docs/`：方案文档和后续项目文档。
- `docs/architecture/`：架构说明目录。
- `docs/decisions/`：技术决策记录目录。
- `docs/mvp/index.md`：MVP 阶段开发文档索引，用于后续逐阶段生成和完善 OpenSpec。
- `docs/runbooks/`：运行手册目录。
- `openspec/changes/persistence-task-service/`：MVP 阶段 02 Persistence & Task Service 的 OpenSpec change。
- `openspec/changes/feishu-webhook/`：MVP 阶段 03 Feishu Webhook 的 OpenSpec change。
- `tests/`：自动化测试，按 acceptance、integration、unit 分层。
- `pyproject.toml`：Python 项目元数据和依赖声明。
- `pyrightconfig.json`：Python 类型检查相关配置。
- `uv.lock`：uv 锁文件，不手写。
- `.python-version`：项目 Python 版本，当前为 `3.12`。

## 核心功能

V1/MVP 计划支持：

- `/plan`：问题拆解和阶段计划。
- `/learn`：搜索资料并生成学习文档。
- `/daily`：生成主题日报。
- `/office`：整理纪要、邮件草稿、周报、PPT 大纲等 Office 文本。
- `/memory`：写入、查看、删除用户偏好记忆。
- `/status`：查询任务状态。

当前核心服务模块规划：

- Message Gateway
- Task Service
- Agent Harness
- Workflow Executor
- Model Gateway
- Tool Gateway
- Memory Service
- Result Dispatcher

当前已实现的能力：

- `GET /health`：返回服务名称和健康状态。
- 基础配置加载：支持默认值和环境变量覆盖。
- 结构化日志：输出 JSON 日志并过滤常见敏感字段。
- 统一错误响应：未知路由和应用异常返回稳定 JSON 格式。
- Alembic 初始迁移：创建 `users`、`platform_accounts`、`tasks`、`memories`、`model_logs`、`tool_logs`、`approvals` 表。
- Alembic 飞书去重迁移：创建 `processed_messages` 表，并通过 `platform + message_id` 防止重复处理。
- `POST /api/tasks`：为已存在用户创建 `pending` 任务。
- `GET /api/tasks/{task_id}`：查询单个任务。
- `GET /api/tasks?user_id=...`：按用户查询任务列表，按创建时间倒序返回。
- Task Service：支持任务创建、合法状态流转、成功结果记录和失败错误记录。
- `POST /api/webhooks/feishu`：处理飞书 URL verification 和 `im.message.receive_v1` 文本事件，校验签名与 token，归一化文本消息，按首个空白分隔 token 映射 `/plan`、`/learn`、`/daily`、`/office`、`/memory`、`/status`，仅为已绑定飞书用户创建 `pending` 任务。

当前尚未实现：

- 企业微信 Webhook。
- Redis、Celery worker 和异步调度。
- Model Gateway、Dify Workflow、Tavily 工具调用和 Agent Harness。
- 任务取消、审批处理和结果推送。

## 开发范式

项目采用 OpenSpec + ATDD，并按阶段推进。

MVP 阶段先从 `docs/mvp/index.md` 进入，再按 `01` 到 `08` 的阶段文档逐步推进。

每个阶段开始前需要明确：

- 阶段目标
- 范围和不做事项
- 验收标准
- 需要新增或更新的测试

每个阶段完成后需要更新：

- README 当前状态
- 启动方式
- 配置说明
- 目录说明
- 已完成核心功能

## 常用命令

```bash
uv sync
uv lock --check
uv run pytest
uv run ruff check .
uv run mypy .
```
