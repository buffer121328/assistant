# 运行与 API 全量上手指南

> **面向对象**：第一次接手本仓库、需要启动系统、调用接口或追踪一次请求到 Worker / Agent Runtime 的开发者。
> **依据**：本文以当前代码为准，路由聚合见 `backend/app/api/router.py`，应用工厂见 `backend/app/main.py`。接口的实时机器可读契约也可在启动后查看 FastAPI `/docs`、`/redoc` 和 `/openapi.json`。
> **边界**：本文只描述仓库已注册的 HTTP / WebSocket 接口与实际调用链；模型、LangBot、搜索、浏览器、Office、Sandbox 等外部能力须完成相应配置才会真正执行。

---

## 目录

1. [一分钟认识系统](#1-一分钟认识系统)
2. [最快运行路径](#2-最快运行路径)
3. [配置、安全与通用约定](#3-配置安全与通用约定)
4. [请求从 API 到 Agent 的完整链路](#4-请求从-api-到-agent-的完整链路)
5. [所有接口总索引](#5-所有接口总索引)
6. [接口参考：通用任务 API](#6-接口参考通用任务-api)
7. [接口参考：Electron 本地 API](#7-接口参考electron-本地-api)
8. [接口参考：会话、记忆、知识与能力](#8-接口参考会话记忆知识与能力)
9. [接口参考：通知、账号、Skill 与远程桥接](#9-接口参考通知账号skill-与远程桥接)
10. [接口参考：模型网关与 LangBot](#10-接口参考模型网关与-langbot)
11. [核心类、函数与数据模型地图](#11-核心类函数与数据模型地图)
12. [调试、测试与常见故障](#12-调试测试与常见故障)

---

## 1. 一分钟认识系统

这是一个以 **任务（`Task`）** 为中心的个人 Agent 系统：

```text
Electron 本地控制台 ─┐
LangBot webhook ────┼→ FastAPI 路由 → 领域服务 / 仓储 → PostgreSQL
其他 HTTP 客户端 ───┘                                      │
                                                             └→ Redis / Celery → Worker
                                                                              → Agent Harness / LangGraph
                                                                              → 模型、工具、审批、审计
                                                                              → TaskEvent / Task 结果 / LangBot 回推
```

### 关键入口与职责

| 位置 | 关键对象 / 函数 | 作用 |
|---|---|---|
| `backend/app/main.py` | `create_app()` | 创建 FastAPI，加载 `Settings`、日志、可观测性、SQLAlchemy sessionmaker、Skill/Capability Registry，安装认证和异常处理器。 |
| `backend/app/api/router.py` | `router` | 汇总所有 `/api/*`、`/internal/*`、`/local/*`、webhook 和 `/health` 路由。 |
| `backend/infrastructure/security/auth.py` | `LocalApiAuthMiddleware` | 可选 Bearer 鉴权；保护 `/api/`、`/internal/`、`/local/` 前缀。 |
| `backend/tasks/lifecycle.py` | `TaskService`、`ApprovalService` | 任务创建、owner 校验、状态迁移、审批决策和会话消息写入。 |
| `backend/workers/worker.py` | `enqueue_task_execution()`、Celery `execute_task` | 将任务投递 Redis/Celery；Redis 为占位地址时返回 `queued: false`。 |
| `backend/workers/runtime.py` | `execute_task_by_id()` | Worker 主编排：创建 `AgentRun`，组装运行依赖，执行 Agent，持久化状态/事件，必要时回推 LangBot。 |
| `backend/runtime/runner_harness.py` | `AgentHarness` | Agent 执行边界：读取任务、上下文/记忆、调用 LangGraph、按结果写成功/失败/待审批。 |
| `backend/runtime/langgraph_executor.py` | `LangGraphExecutor` | Agent loop：模型调用、受治理工具调用、checkpoint 与审批请求。 |

### 三种入口的区别

| 入口 | 适合谁 | 创建任务后的行为 |
|---|---|---|
| `/api/tasks` | 其他服务或脚本 | **只落库**，不投递 Worker。 |
| `/api/tasks/submit` | 通用 API 客户端 | 落库后调用 `enqueue_task_execution()`，返回是否成功投递。 |
| `/local/tasks` | Electron | 自动创建 desktop conversation 与 session workspace，再创建并尽力投递任务。 |
| `/api/webhooks/langbot` | LangBot | 校验 webhook、去重、绑定用户、解析意图，创建 LangBot conversation/task 后尽力投递。 |

---

## 2. 最快运行路径

### 2.1 前置条件

- Python **3.12**（`.python-version` 固定）；
- `uv`；
- Docker Desktop（完整后端推荐）；
- Node.js + npm（Electron 桌面端）。

### 2.2 推荐：Compose 启动完整后端

```bash
cp .env.example .env
uv sync
docker compose up --build assistant-api celery-worker celery-beat postgres redis
```

Compose 会依次运行：`postgres` → 一次性 `migrate`（Alembic）与 `runtime-init`（初始化 volume 权限）→ `assistant-api`、`celery-worker`、单实例 `celery-beat`、`redis`。API 仅绑定本机 `127.0.0.1:8000`。

启动后先检查：

```bash
curl http://127.0.0.1:8000/health
# {"service_name":"assistant-api","status":"ok"}
```

若 `.env` 中 `LOCAL_API_AUTH_REQUIRED=true`，除三个公开端点外均带：

```bash
export API=http://127.0.0.1:8000
export TOKEN='<只写入本机 .env 的 Local API Token>'
curl -H "Authorization: Bearer $TOKEN" "$API/local/config"
```

### 2.3 本地分进程调试

本地 PostgreSQL/Redis 已可达、且迁移已完成时：

```bash
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload --app-dir backend
uv run celery -A workers.worker:celery_app worker --loglevel=info
uv run celery -A workers.worker:celery_app beat --loglevel=info
```

应用以 `create_app` 工厂运行；`get_session()` 每个请求从 `app.state.db_sessionmaker` 创建一个 `AsyncSession`。不要将 session 在请求外复用。

### 2.4 启动桌面端

```bash
cd frontend/desktop
npm install
npm run dev
```

Electron 的 `frontend/desktop/src/main/index.ts` 创建窗口；preload 仅通过 `contextBridge` 暴露设置/打开路径等有限 IPC；renderer 的 `LocalApiClient` 调用 `/local/*`。在 Settings 中填写：

- **Local API URL**：例如 `http://127.0.0.1:8000`；
- **User ID**：必须是数据库中已有 `users.id`；
- **Workdir**：可选，必须是已存在目录；
- 若开启本地 API 鉴权，还须填写/保存对应 token 的客户端配置。

### 2.5 可选依赖与真实外部调用

核心安装不默认安装重能力：

```bash
uv sync --extra browser-automation
uv sync --extra office
uv sync --extra observability
```

占位模型、LangBot、Tavily 配置只能让基础服务启动，不能完成真实外部调用。完整变量解释见现有 [`mvp-startup-config.md`](mvp-startup-config.md)。

---

## 3. 配置、安全与通用约定

### 3.1 配置读取

`Settings` 位于 `backend/infrastructure/settings/config.py`，使用 Pydantic Settings 从 `.env` / 环境变量加载。示例只含占位值；真实 Token、Cookie、API Key、私有 URL、认证头只能保留在未提交的 `.env`。

| 配置组 | 必要字段 / 行为 |
|---|---|
| 本机 API | `LOCAL_API_AUTH_REQUIRED`、`LOCAL_API_TOKEN`；启用但 token 为空时受保护接口返回 `503 local_api_auth_unconfigured`。 |
| 数据与队列 | `DATABASE_URL`（异步 SQLAlchemy URL）、`REDIS_URL`；Worker 以 Redis 作 broker/result backend。 |
| 凭据 | `CREDENTIAL_MASTER_KEY`；账号连接用 `CredentialCipher` 加密凭据。未配置时账号连接 fail-closed。 |
| 模型 | `DEEPSEEK_*`、`MODELS_TIMEOUT_SECONDS`、`MODELS_RETRY_ATTEMPTS`、`MODELS_NODES_JSON`。实际字段以 `config.py` 为准；当前 `.env.example` / Compose 中遗留的 `MODEL_GATEWAY_*` 名称会被 Settings 忽略，不应作为有效覆盖项。 |
| 外部能力 | `LANGBOT_*`、`TAVILY_*` / Brave / DuckDuckGo、`BROWSER_*`、`SANDBOX_*`、`LANGFUSE_*`。默认保持关闭或占位。 |
| 可变目录 | `MANAGED_SKILLS_ROOT`、`ARTIFACTS_ROOT`、`SESSION_WORKSPACE_ROOT`、`KNOWLEDGE_ROOT` 等默认在 `var/`，不要和随源码发布的 `backend/resources/` 混用。 |

### 3.2 认证规则

`LocalApiAuthMiddleware` 的规则如下：

| 分类 | 路径 | 是否 Bearer 认证 |
|---|---|---|
| 公开 | `/health`、`/local/health`、`/api/webhooks/langbot` | 否；LangBot 另校验 `X-LangBot-Secret`。 |
| 受保护 | 所有 `/api/*`、`/internal/*`、`/local/*` | 仅当 `LOCAL_API_AUTH_REQUIRED=true` 时需要 `Authorization: Bearer <LOCAL_API_TOKEN>`。 |
| 非上述前缀 | 如 FastAPI `/docs`、`/openapi.json` | 中间件不要求该 Bearer token。部署时仍应由网络边界限制访问。 |

认证不替代 **owner-scoped** 数据边界：绝大多数资源仍要求 `user_id`，服务/查询会将资源限制为该用户。它是单机个人助手的 owner 隔离，不是完整多租户 RBAC。

### 3.3 统一错误和时间格式

业务异常统一为：

```json
{
  "error": {
    "code": "task_not_found",
    "message": "Task operation failed."
  }
}
```

- 请求模型校验失败：`422`，`code: validation_error`；
- 未匹配路由：`404`，`code: not_found`；
- 中间件认证失败：`401 local_api_auth_failed`；
- 认证启用但未设置 token：`503 local_api_auth_unconfigured`；
- 路由/服务的 `AppError` 以其实际 `code` 与 HTTP 状态返回；
- Pydantic `datetime` 字段使用 ISO 8601；`created_at` / `updated_at` 为持久化时间；
- `user_id`、路径中的 ID 均为字符串 ID，不应假定为整数。

### 3.4 最小可用任务示例

先确保已有用户（项目当前没有创建 User 的公开 API，测试/初始化脚本或数据库迁移后的初始化流程须准备它），然后：

```bash
curl -X POST "$API/api/tasks/submit" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "user_id":"<existing-user-id>",
    "platform":"script",
    "task_type":"plan",
    "input_text":"为本仓库拟定一次无副作用的代码阅读计划",
    "model_class":"light"
  }'
```

`201` 中 `queued: true` 仅表示 Celery 消息已投递，不等于模型执行已经成功；后续读取任务、事件或 diagnostics。

---

## 4. 请求从 API 到 Agent 的完整链路

### 4.1 任务提交与执行

```text
POST /api/tasks/submit 或 POST /local/tasks
→ 路由解析 Pydantic payload，并通过 get_session() 注入 AsyncSession
→ TaskService.create_task()
  → TaskRepository.user_exists() 验证 User
  → 若给 conversation_id，ConversationService.get_owned() 验证 owner 和未归档
  → TaskRepository.create_task() 插入 Task(status=pending)
  → 有会话时 ConversationService.append_message(role=user)
  → commit / refresh
→ enqueue_task_execution(task_id)
  → Redis 非占位时 Celery execute_task.delay(task_id)
→ workers.execute_task
→ workers.runtime.execute_task_by_id()
  → 建立 AgentRun / Observability
  → 组装工具、模型、检索、Skill、审批、checkpoint 等依赖
  → AgentHarness 执行 LangGraph
  → TaskService.save_success() / save_failure() / save_waiting_approval()
  → TaskEventPublisher 发布 task.status.changed
  → LangBot 任务在可回推状态时 ResultDispatcher.dispatch_task()
```

`Task` 状态机由 `domain/policies/task_status.py` 定义：

```text
pending → running | cancelled
running → success | failed | cancelled | waiting_approval
waiting_approval → pending | cancelled
```

审批通过会将等待中的任务恢复为 `pending` 并重新投递；拒绝不会投递。`success`、`failed`、`cancelled` 是终态。

### 4.2 Electron 特有链路

`POST /local/tasks` 先由 `ConversationService.create(channel="desktop", commit=False)` 创建会话（或使用 payload 的 `conversation_id`），再由 `SessionWorkspaceStore.create()` 建立该会话的 `input/`、`work/`、`output/`、`audit/` 工作区，最后创建任务。任务事件由 WebSocket 拉取，工具日志与事件内容均经本地 payload sanitizer 脱敏。

### 4.3 LangBot 特有链路

`handle_langbot_webhook()` 依次执行：

1. `verify_langbot_secret()` 比较 `X-LangBot-Secret`；
2. `normalize_message()` 统一 adapter、发送者、会话与消息；
3. `MessageRepository.get_processed_message()` 按 `(platform, adapter, message_id)` 去重；
4. 读取 `PlatformAccount` 将 `adapter:sender_id` 映射到本地 User；
5. `parse_task_type()` 或 `classify_langbot_intent()` 确定任务类型；
6. `ConversationService.resolve_external()` 取得/创建外部会话；
7. 写 `ProcessedMessage` 桥接账本、`Task` 与用户消息，并尽力投递 Worker；
8. Worker 完成或进入待审批后，`ResultDispatcher` 使用 `LangBotResultClient` 尝试回推。

---

## 5. 所有接口总索引

所有下表路径均已由总路由注册。`🔒` 表示在 `LOCAL_API_AUTH_REQUIRED=true` 时需要本机 Bearer token；`◇` 表示 WebSocket。

| 域 | 方法 | 路径 | 用途 |
|---|---:|---|---|
| 健康 | GET | `/health` | 通用健康检查（公开）。 |
| 任务 | POST | `/api/tasks` | 仅创建任务。 |
| 任务 | POST | `/api/tasks/submit` | 创建并尽力投递任务。 |
| 任务 | GET | `/api/tasks` | 按 owner 列表。 |
| 任务 | GET | `/api/tasks/{task_id}` | owner 获取任务。 |
| 任务 | GET | `/api/tasks/{task_id}/diagnostics` | 聚合事件/模型/工具/审批/记忆诊断。 |
| 任务 | GET | `/api/tasks/{task_id}/events/stream` | NDJSON 增量事件流。 |
| 任务 | GET | `/api/tasks/{task_id}/approvals` | 任务审批列表。 |
| 任务 | POST | `/api/tasks/{task_id}/approvals/{approval_id}/decision` | 通用审批决策。 |
| 本地 | GET | `/local/health` | Electron 连通性检查（公开）。 |
| 本地 | GET | `/local/config` | 非敏感运行配置。 |
| 本地 | POST | `/local/settings/validate` | 校验本地客户端设置。 |
| 本地 | GET | `/local/tasks` | Electron 任务列表。 |
| 本地 | POST | `/local/tasks` | Electron 创建并尽力投递任务。 |
| 本地 | GET | `/local/tasks/{task_id}` | 获取 owner 任务。 |
| 本地 | POST | `/local/tasks/{task_id}/messages` | 在同一会话创建后续任务。 |
| 本地 | GET | `/local/conversations/{conversation_id}/token-stats` | 会话 token 估算统计。 |
| 本地 | GET | `/local/tasks/{task_id}/events` | 事件列表。 |
| 本地 | ◇ | `/local/tasks/{task_id}/events/stream` | WebSocket 事件流。 |
| 本地 | GET | `/local/tasks/{task_id}/logs` | 工具日志。 |
| 本地 | GET | `/local/tasks/{task_id}/approvals` | 审批列表。 |
| 本地 | POST | `/local/tasks/{task_id}/approvals/{approval_id}` | 审批决定。 |
| 会话 | POST | `/api/conversations` | 创建会话。 |
| 会话 | GET | `/api/conversations` | 列出活跃会话。 |
| 会话 | GET | `/api/conversations/{conversation_id}/messages` | 读取会话消息与摘要状态。 |
| 会话 | POST | `/api/conversations/{conversation_id}/archive` | 归档会话。 |
| 记忆 | GET | `/api/memories/overview` | 记忆状态总览与待索引数。 |
| 记忆 | GET | `/api/memories` | 列表记忆。 |
| 记忆 | POST | `/api/memories` | 显式创建记忆。 |
| 记忆 | GET | `/api/memories/{memory_id}` | 详情、链接、反馈、使用记录。 |
| 记忆 | POST | `/api/memories/{memory_id}/actions/{action}` | 确认、修正、归档等动作。 |
| 记忆 | GET | `/api/memory/policies` | 记忆策略列表。 |
| 记忆 | PUT | `/api/memory/policies/{policy_key}` | 更新一项记忆策略。 |
| 记忆 | GET | `/api/memory/consolidation-digests` | 合并摘要。 |
| 记忆 | GET | `/api/tasks/{task_id}/memory-retrieval` | 某任务的记忆检索轨迹。 |
| 知识 | POST | `/api/knowledge/import` | 上传、解析、分块知识文件。 |
| 知识 | GET | `/api/knowledge/documents` | 知识文档列表。 |
| 知识 | DELETE | `/api/knowledge/documents/{document_id}` | 删除一个知识文档。 |
| 知识 | GET | `/api/knowledge/search` | 检索知识块。 |
| 能力 | GET | `/api/capabilities` | 当前能力 Registry 目录。 |
| Skill | GET | `/api/skills` | 列出可用 Skill。 |
| Skill | POST | `/api/skills` | 创建受管 Skill。 |
| Skill | POST | `/api/skills/install` | 上传安装包。 |
| Skill | POST | `/api/skills/{name}/enable` | 启用 Skill。 |
| Skill | POST | `/api/skills/{name}/disable` | 停用 Skill。 |
| Skill | DELETE | `/api/skills/{name}` | 卸载受管 Skill。 |
| 通知 | POST | `/api/reminders` | 创建提醒。 |
| 通知 | GET | `/api/reminders` | 列出提醒。 |
| 通知 | POST | `/api/reminders/{reminder_id}/cancel` | 取消提醒。 |
| 通知 | GET | `/api/notifications/poll` | 获取桌面待展示通知。 |
| 通知 | POST | `/api/notifications/{outbox_id}/ack` | 确认已展示。 |
| 账号 | GET | `/api/connections` | 列出账号连接。 |
| 账号 | POST | `/api/connections` | 加密创建账号连接。 |
| 账号 | POST | `/api/connections/{connection_id}/test` | 测试连接。 |
| 账号 | POST | `/api/connections/{connection_id}/disable` | 停用连接。 |
| 账号 | DELETE | `/api/connections/{connection_id}` | 撤销连接。 |
| 桥接 | GET | `/api/remote-control/bridge/sessions` | 列出 LangBot 入站/回推账本。 |
| 桥接 | GET | `/api/remote-control/bridge/sessions/{message_id}` | 查看一条桥接账本。 |
| 桥接 | POST | `/api/remote-control/bridge/sessions/{message_id}/replay` | 重放已绑定任务的结果回推。 |
| 模型 | POST | `/internal/models/chat` | 内部模型网关聊天入口。 |
| LangBot | POST | `/api/webhooks/langbot` | LangBot 入站 webhook（公开但专用 secret 校验）。 |

---

## 6. 接口参考：通用任务 API

除 `/health` 外，本节均为受保护前缀。公共响应 `TaskResponse` 的核心字段是：`task_id`（同时作为 `trace_id`）、`user_id`、`platform`、`task_type`、`input_text`、`status`、`workflow_key`、`model_class`、`conversation_id`、`result_text`、`error_message`、`created_at`、`updated_at`。

| 方法与路径 | 请求 | 响应 / 行为 | 实现调用链 |
|---|---|---|---|
| `GET /health` | 无 | `{service_name,status:"ok"}`。 | `health_check()` → `request.app.state.settings`。 |
| `POST /api/tasks` | JSON：`user_id`、`platform`、`task_type`、`input_text` 必填；可选 `workflow_key`、`model_class: light\|standard`、`conversation_id`。 | `201 TaskResponse`；**不会入队**。user/conversation 不存在分别为 404。 | `create_task()` → `TaskService.create_task()` → `TaskRepository`；有会话则 `ConversationService.append_message()`。 |
| `POST /api/tasks/submit` | 同上。 | `201 {task: TaskResponse, queued: boolean}`；`queued=false` 表示投递未发生，不回滚已创建任务。 | `submit_task()` → `TaskService.create_task()` → `_enqueue_task_execution()` → Celery。 |
| `GET /api/tasks?user_id=...` | `user_id` 必填。 | `{items:[TaskResponse]}`。 | `list_tasks()` → `TaskService.list_tasks()` → `TaskRepository.list_tasks_by_user()`。 |
| `GET /api/tasks/{task_id}?user_id=...` | 路径 `task_id`，查询 `user_id`。 | `TaskResponse`；非 owner 也表现为 `task_not_found`。 | `get_task()` → `TaskService.get_task_by_user()`。 |
| `GET /api/tasks/{task_id}/diagnostics?user_id=...` | 同上。 | `trace_id`、任务快照、`events`、`model_calls`、`tool_calls`、`approvals`、最新 `retrieval`、`error_summary`；文本经 `sanitize_text()` 后截断至 1000 字符。 | `get_task_diagnostics()` 直接查询 `Task`、`TaskEventRepository`、`ModelLog`、`ToolLog`、`Approval`、`MemoryRetrievalTrace`。 |
| `GET /api/tasks/{task_id}/events/stream?user_id=...&after=0` | `after≥0`，可选。 | `application/x-ndjson`；每行是 `{sequence,type,payload,created_at}`。每 0.2 秒轮询，任务到终态/待审批且无新事件即结束。 | `stream_task_events()` → `TaskEventRepository.list_after()`。 |
| `GET /api/tasks/{task_id}/approvals?user_id=...` | owner 查询。 | `{items:[ApprovalResponse]}`。 | `list_task_approvals()` → `ApprovalService.list_for_owner()`。 |
| `POST /api/tasks/{task_id}/approvals/{approval_id}/decision` | JSON：`user_id`、`decision: "approved"\|"rejected"`。 | `{approval,task,queued}`。只有第一次有效决策且为 `approved` 才重新投递。 | `decide_task_approval()` → `ApprovalService.decide()` → `_enqueue_task_execution()`。 |

`ApprovalResponse`：`approval_id`、`task_id`、`tool_name`、`approval_type`（`tool` / `plan` / `review`）、`subject`、`request_summary`、`status`、决策人/时间、创建/更新时间。

### 任务事件

`TaskEventRepository.append()` 对同一 Task 分配递增 `sequence`，payload 先过滤敏感 key（`token`、`secret`、`api_key`、`cookie` 等）并脱敏，序列化后上限 16,000 字符。已定义的主事件名包括：

- `task.status.changed`：Worker 收尾时发布；payload 通常含 `{status}`；
- `task.message.delta`：`TaskEventPublisher.publish_text()` 分块输出；payload 含 `{text}`；
- `plan`：计划相关事件；
- 运行期还可能出现工具、审批和诊断事件，客户端应以 `type` 分支并容忍新增字段。

---

## 7. 接口参考：Electron 本地 API

前缀固定为 `/local`，由 `channels/desktop/local/router.py` 聚合。除 `/local/health` 外遵循本机 Bearer 规则。所有读取任务/事件/日志/审批的接口会按 `user_id` 做 owner 校验。

| 方法与路径 | 请求 | 响应 / 行为 | 实现调用链 |
|---|---|---|---|
| `GET /local/health` | 无 | `{service_name,status:"ok"}`；公开。 | `local_health()`。 |
| `GET /local/config` | 无 | `service_name`、`app_env`、`local_api_auth_required`、features（browser、sandbox、shell、subagent）。绝不返回 secret。 | `local_config()` → `Settings`。 |
| `POST /local/settings/validate` | JSON：`api_base_url`、`approval_policy: ask\|require_high_risk\|read_only` 必填；可选 `default_workdir`、`default_model_class`。 | `{ok:true,settings:{...正规化后值}}`。URL 只能是 localhost / `127.0.0.1` / `::1`，无用户名/密码/路径；workdir 必须存在且是目录。 | `local_validate_settings()` → `validated_local_api_base_url()`、`validated_workdir()`。 |
| `GET /local/tasks?user_id=...` | owner 查询。 | `{items:[TaskResponse]}`。 | `local_list_tasks()` → `TaskService.list_tasks()`。 |
| `POST /local/tasks` | JSON：`user_id`、`task_type`、`input_text`；可选 `workflow_key`、`model_class`、`conversation_id`。 | `201 {task,queued}`。无 conversation ID 时新建 `channel="desktop"` 会话和工作区。 | `local_create_task()` → `ConversationService.create()` → `SessionWorkspaceStore.create()` → `TaskService.create_task()` → Celery。 |
| `GET /local/tasks/{task_id}?user_id=...` | owner 查询。 | `TaskResponse`。 | `local_get_task()` → `get_owned_task()`。 |
| `POST /local/tasks/{task_id}/messages` | JSON：`user_id`、`content`。 | `{task,queued}`；不是给原 Task 直接追加文本，而是在其 conversation 中创建一个继承 task_type/workflow/model 的**新 Task**。 | `local_append_task_message()` → `get_owned_task()` → `TaskService.create_task()` → Celery。 |
| `GET /local/conversations/{conversation_id}/token-stats?user_id=...` | owner 查询。 | `conversation_id`、用户/助手/总消息数、各自估算 token、`token_limit`、`usage_ratio`、`status: ok\|warning\|full`。 | `local_conversation_token_stats()` → `ConversationService.token_stats()`。 |
| `GET /local/tasks/{task_id}/events?user_id=...&after_event_id=...` | 可选事件 ID 游标。 | `{items:[{event_id,task_id,type,created_at,sequence,payload}]}`；payload 已脱敏。 | `local_list_task_events()` → `sequence_after_event_id()` → `TaskEventRepository.list_after()`。 |
| `WS /local/tasks/{task_id}/events/stream?user_id=...&after_event_id=...` | WebSocket 查询参数。 | accept 后顺序发送上述 event JSON；每 0.2 秒轮询；终态或 `waiting_approval` 且无新事件时主动 close。 | `local_stream_task_events()` → owner 校验 → `TaskEventRepository`。 |
| `GET /local/tasks/{task_id}/logs?user_id=...` | owner 查询。 | 与事件相同 envelope 的 `{items: [...]}`，每项由 `ToolLog` 转换而来。 | `local_list_task_logs()` → 查询 `ToolLog` → `local_tool_log_response()`。 |
| `GET /local/tasks/{task_id}/approvals?user_id=...` | owner 查询。 | `{items:[ApprovalResponse]}`。 | `local_list_task_approvals()` → `ApprovalService.list_for_owner()`。 |
| `POST /local/tasks/{task_id}/approvals/{approval_id}` | JSON：`user_id`、`decision: "approve"\|"reject"`，可选 `reason≤1000`。 | `{approval,task,queued}`；`reason` 目前仅在请求 schema 中校验，当前 `ApprovalService.decide()` 调用不持久化它。 | `local_decide_task_approval()` → `ApprovalService.decide()` → `safe_enqueue_task_execution()`。 |

**注意**：桌面端的 `approval_policy` 是前端设置，不能绕过后端 ToolRegistry、风险等级和 `ApprovalService`。

---

## 8. 接口参考：会话、记忆、知识与能力

### 8.1 Conversation API

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `POST /api/conversations` | JSON：`user_id`，可选 `title≤255`。 | `201 ConversationResponse`；`create_conversation()` → `ConversationService.create()`。 |
| `GET /api/conversations?user_id=...` | owner 查询。 | `{items:[ConversationResponse]}`，仅活跃会话；`ConversationService.list_active()`。 |
| `GET /api/conversations/{id}/messages?user_id=...&limit=100` | `limit=1..200`。 | `{items,compacted,summary_updated_at,summary_version}`；`ConversationService.list_messages()` + `ConversationMemoryService.get_active_summary()`。 |
| `POST /api/conversations/{id}/archive` | JSON：`{user_id}`。 | `ConversationResponse`；`ConversationService.archive()` 设置归档状态，后续不能作为活跃会话创建任务。 |

`Conversation` 记录 owner、title、channel、可选 external key；`ConversationMessage` 保存 role（`user` / `assistant`）、内容和可选 task ID。每次 `TaskService.create_task()`（有 conversation）写入用户消息；`save_success()` / `save_failure()` / `save_waiting_approval()` 写助手消息。

### 8.2 Memory API

Memory 路由保留自由 JSON payload，因此调用方必须按下表提供字段；它不像其他域使用严格 Pydantic body。敏感级别为 `forbidden` 的内容不会经列表/详情暴露，`sensitive` 内容在 `_memory_payload()` 中显示为 `[SENSITIVE]`。

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `GET /api/memories/overview?user_id=...` | owner 查询。 | `{counts:{status:number},pending_index_count}`；直接聚合 `Memory` / `MemoryIndexOutbox`，先确认 `User`。 |
| `POST /api/memories` | JSON：`user_id`、`content`；可选 `memory_type`（默认 `preference`）、`scope_kind`（默认 `user/global`）、`scope_id`。 | `201 {memory}`；`MemoryService.create_memory()`，必要时 `change_memory_scope()`，随后 commit。 |
| `GET /api/memories?user_id=...` | 可选 `status`、`memory_type`、`scope_kind`、`sensitivity`、`limit=1..200`（默认 50）、`offset≥0`。 | `{items,limit,offset}`，按更新时间倒序，排除 forbidden。 |
| `GET /api/memories/{memory_id}?user_id=...` | owner 查询。 | `{memory,links,feedback,usage}`；查询 `MemoryLink`、`MemoryFeedback`、`MemoryRetrievalTraceItem`，所有关联数据均再按 owner 过滤。 |
| `POST /api/memories/{memory_id}/actions/{action}` | 所有动作 JSON 均需 `user_id`。 | `{memory}`；见下一表。 |
| `GET /api/memory/policies?user_id=...` | owner 查询。 | `{items:[policy_key,scope_kind,scope_id,enabled,value]}`。 |
| `PUT /api/memory/policies/{policy_key}` | JSON：`user_id`；可选 `scope_kind`、`scope_id`、`enabled`。 | 仅支持 `never_remember:<episode|fact|preference|constraint|procedure|reflection>`；`MemoryPolicyService.set_never_remember()`。 |
| `GET /api/memory/consolidation-digests?user_id=...&limit=20` | `limit=1..100`。 | `{items:[digest_id,digest_type,window_start,window_end,content,created_at]}`。 |
| `GET /api/tasks/{task_id}/memory-retrieval?user_id=...` | owner 任务查询。 | 最新 `{trace,items}`，没有追踪时 `{trace:null,items:[]}`。 |

`action` 的准确语义：

| action | 额外字段 | 调用的 `MemoryService` / 行为 |
|---|---|---|
| `confirm` | 无 | `confirm_memory()`。 |
| `reject` | 无 | `reject_memory()`。 |
| `correct` | `content` | `correct_memory(..., confirm=True)`；产生/确认更正后的记忆。 |
| `pin` / `unpin` | 无 | `set_memory_pinned()`。 |
| `scope` | `scope_kind`，可选 `scope_id` | `change_memory_scope()`。 |
| `archive` | 无 | `archive_memory()`。 |
| `forget` | 无 | `forget_memory()`。 |
| `validity` | 可选 ISO 日期 `valid_from`、`valid_to` | 直接修改有效期；起始不得晚于或等于结束，否则 `400 memory_validity_invalid`。 |
| `rebuild-index` | 无 | `MemoryRepository.queue_index_operation(operation="rebuild")`。 |

`memory` 载荷包含 ID、owner、类型、状态、内容、scope、sensitivity、confidence/importance、确认/有效期/来源、pin/access 统计、时间戳等字段，完整字段以 `memories.py::_memory_payload()` 为准。

### 8.3 Knowledge API

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `POST /api/knowledge/import` | `multipart/form-data`：`user_id` 文本字段、`document` 文件字段。 | `201 {document_id,source_label,status,chunk_count,unchanged}`；先最多读 `MAX_IMPORT_BYTES + 1`，再 `KnowledgeService.store_upload()`。 |
| `GET /api/knowledge/documents?user_id=...` | owner 查询。 | `{items:[document_id,source_label,media_type,status,chunk_count,last_error_code]}`；`KnowledgeService.list_documents()`。 |
| `DELETE /api/knowledge/documents/{document_id}?user_id=...` | owner 查询。 | `{document_id,status,chunk_count}`；`KnowledgeService.delete_document()`，删除后 chunk 不再可检索。 |
| `GET /api/knowledge/search?user_id=...&query=...&limit=5` | `query` 长 1..200，`limit=1..20`。 | `{items,answerable}`；`KnowledgeService.search()`。每项有 `source_id`、`citation`、机器可校验 `citation_token`、内容、score、`trust_boundary`、`instruction_risk`。 |

知识内容是不可信检索上下文，不能把文档中的指令当作系统授权；Agent 的 grounding 仍须使用当前检索返回的 citation token。

### 8.4 Capability API

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `GET /api/capabilities?kind=...&enabled=...` | `kind`、`enabled` 均可选。 | `{revision,items}`；`list_capabilities()` 读取 `app.state.capability_registry`，调用 `CapabilityRegistry.list()`。项目启动时 `build_default_registry()` 构建；Skill 生命周期变更后会刷新。 |

每项：`id`、`kind`、展示名、摘要、source、enabled、风险 `L0..L4`、`requires_approval`。此接口描述可发现的能力，不等同于调用它就能绕过工具审批。

---

## 9. 接口参考：通知、账号、Skill 与远程桥接

### 9.1 提醒与桌面通知

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `POST /api/reminders` | JSON：`user_id`、`title≤255`、`message≤10000`、ISO `due_at`、`channel: desktop\|langbot`。 | `201 ReminderResponse`；`ReminderService.create()`。 |
| `GET /api/reminders?user_id=...` | owner 查询。 | `{items}`；`ReminderService.list()`，再读取最新 `NotificationOutbox` 填充 `delivery_status` / `last_error_code`。 |
| `POST /api/reminders/{id}/cancel` | JSON：`{user_id}`。 | `ReminderResponse`；`ReminderService.cancel()`。 |
| `GET /api/notifications/poll?user_id=...` | owner 查询。 | `{items:[outbox_id,reminder_id,title,message,due_at]}`；`ReminderService.poll_desktop()`。 |
| `POST /api/notifications/{outbox_id}/ack` | JSON：`{user_id}`。 | `204 No Content`；`ReminderService.acknowledge_desktop()`。 |

`NotificationError` 以 not-found 结尾时映射 404，否则映射 409。

### 9.2 外部账号连接

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `GET /api/connections?user_id=...` | owner 查询。 | `{items:[connection_id,user_id,provider,display_name,status,last_checked_at,last_error_code]}`。 |
| `POST /api/connections` | JSON：`user_id`、`provider: smtp\|caldav\|browser`、`display_name`、`credentials: {string:string}`。 | `201 AccountConnectionResponse`；`account_service()` 建立 `CredentialCipher` 和 `AccountConnectionService.create()`，只保存密文。 |
| `POST /api/connections/{id}/test` | JSON：`{user_id}`。 | 更新后的连接；`AccountConnectionService.test()` 调用已注入的 `DefaultConnectionTester`。 |
| `POST /api/connections/{id}/disable` | JSON：`{user_id}`。 | 更新后的连接；`set_status(...,"disabled")`。 |
| `DELETE /api/connections/{id}?user_id=...` | owner 查询。 | 更新后的连接；逻辑撤销为 `status="revoked"`，不是物理删除。 |

若 `CREDENTIAL_MASTER_KEY` 不可用，`account_service()` 返回 `503 credential_master_key_unavailable`；凭据绝不会出现在响应中。

### 9.3 Managed Skill

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `GET /api/skills` | 无业务 body。 | `{items:[name,display_name,summary,version,source,enabled,manageable]}`；`SkillLifecycleService.list_skills()`。 |
| `POST /api/skills` | JSON：`user_id`、小写 kebab-case `name`（≤128）、`display_name`、`summary`、`instructions`（≤131072）。 | `201 SkillResponse`；`SkillLifecycleService.create()`。 |
| `POST /api/skills/install` | `multipart/form-data`：`user_id`、`package`。 | `201 SkillResponse`；最多读 `MAX_ARCHIVE_BYTES+1`，`SkillLifecycleService.install()` 验证/安装。 |
| `POST /api/skills/{name}/enable` | JSON：`{user_id}`。 | SkillResponse；`set_enabled(..., True)`。 |
| `POST /api/skills/{name}/disable` | JSON：`{user_id}`。 | SkillResponse；`set_enabled(..., False)`。 |
| `DELETE /api/skills/{name}?user_id=...` | owner 查询。 | `204 No Content`；`SkillLifecycleService.uninstall()`。 |

`lifecycle_service()` 使用 `app.state.managed_skill_store`。创建、安装、启用、禁用、卸载后通过闭包重新调用 `build_default_registry()`，所以 `/api/capabilities` 反映新 revision。内置 Skill 与 managed Skill 的可管理性不同，响应的 `manageable` 是最终判断依据。

### 9.4 LangBot Remote Control Bridge

此域以 `ProcessedMessage` 作为 LangBot 入站和回推的审计账本；当前列表接口没有 `user_id` 过滤，访问控制依赖本机 API Bearer 与本机部署边界。

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `GET /api/remote-control/bridge/sessions?limit=20&conversation_id=...` | `limit=1..100`，会话 ID 可选。 | `{items:[BridgeSession]}`，只查 `platform="langbot"`，按创建时间倒序并左连接 Task 状态。 |
| `GET /api/remote-control/bridge/sessions/{message_id}` | 路径 message ID。 | `BridgeSession`；不存在 `404 bridge_session_not_found`。 |
| `POST /api/remote-control/bridge/sessions/{message_id}/replay` | 无 body。 | `{dispatch_status,message,session}`；必须已有 `task_id`，否则 `409 bridge_session_not_replayable`；`ResultDispatcher.dispatch_task()` 再发送一次结果。 |

`BridgeSession` 包含原始 message / sender / conversation、意图结果、关联 task 状态、解析出的 response target、delivery 状态/尝试次数/错误摘要/响应 JSON 和时间。重放是**结果回推重试**，不是重新执行 Agent task。

---

## 10. 接口参考：模型网关与 LangBot

### 10.1 Internal Model Chat

| 方法与路径 | 请求 | 响应 / 调用链 |
|---|---|---|
| `POST /internal/models/chat` | JSON：`user_id`、`task_id`、`task_type`、`messages`（至少 1 个 `{role: system\|user\|assistant, content}`）；可选 `model_class`、`temperature=0..2`（默认 .3）、`max_tokens=1..32000`（默认 4000）。 | `{provider,model,content,usage:{input_tokens,output_tokens},latency_ms,status:"succeeded"}`。 |

`chat_with_model()` 把 body 映射成 `GatewayRequest` / `GatewayMessage`，调用 `model_gateway.chat_service.handle_model_chat()`。模型网关负责节点选择、容量/冷却/fallback、超时、重试和 `ModelLog`；`ModelGatewayError` 通过 `_app_error()` 脱敏（包括 DeepSeek key）后返回。它是内部接口，通常由运行时使用，不应直接暴露给不可信公网客户端。

### 10.2 LangBot Webhook

```http
POST /api/webhooks/langbot
X-LangBot-Secret: <LANGBOT_WEBHOOK_SECRET>
Content-Type: application/json
```

```json
{
  "message_id": "unique-provider-message-id",
  "adapter": "adapter-name",
  "conversation": {"id": "conversation-id", "type": "private"},
  "sender": {"id": "sender-id"},
  "message": {"type": "text", "text": "/plan 帮我分析这个仓库"}
}
```

路由本身公开，以便第三方 webhook 到达；但 `handle_langbot_webhook()` 强制 `X-LangBot-Secret`（大小写兼容）与配置值一致，否则 `401 langbot_invalid_secret`。可能的成功 ACK：

```json
{
  "ok": true,
  "reason": "task_created",
  "message": {"platform":"langbot","adapter":"...","sender_id":"...","conversation_id":"...","conversation_type":"...","text":"...","message_id":"..."},
  "task_id": "...",
  "task_type": "plan",
  "task_status": "pending"
}
```

不创建任务的成功 ACK 也返回 `ok: true`，常见 `reason`：

| reason | 含义 |
|---|---|
| `duplicate_message` | `(platform, adapter, message_id)` 已处理，幂等返回。 |
| `unknown_command` | 空文本、`/` 或意图无法归类。 |
| `unbound_user` | 未将 `adapter:sender_id` 绑定到 `PlatformAccount` / User。 |
| 其他 intent outcome | 分类器认为是命令响应/非任务等，不进入 Worker。 |

---

## 11. 核心类、函数与数据模型地图

### 11.1 分层职责

| 层 | 主要目录 | 应在何时阅读 |
|---|---|---|
| API / Channel | `backend/app/api/routers/`、`backend/channels/` | 需要知道某路径如何解析、鉴权和返回。 |
| Schema | `backend/app/api/schemas/`、`backend/channels/desktop/local/schemas.py` | 需要精确 JSON / multipart 字段及限制。 |
| Application orchestration | `backend/tasks/`、`backend/session/`、`backend/integrations/`、`backend/memory/`、`backend/rag/`、`backend/agent/` | 需要理解“业务动作”以及 owner 校验、事务边界。 |
| Domain | `backend/domain/models/`、`backend/domain/policies/` | 需要了解实体、状态机、审批规范化、脱敏等纯规则。 |
| Infrastructure | `backend/infrastructure/` | 需要了解数据库、仓储、认证、配置、遥测和 runtime port adapter。 |
| Runtime / Worker | `backend/workers/`、`backend/runtime/`、`backend/model_gateway/`、`backend/tools/` | 需要调试任务执行、模型、工具、审批、事件与后台维护。 |

### 11.2 主服务与函数的精确职责

| 名称 | 文件 | 解释 |
|---|---|---|
| `create_app(settings=None, observability=None)` | `app/main.py` | 唯一应用装配入口。将 Settings、logger、observability、DB sessionmaker、连接测试器、Skill store 和 capability registry 放入 `app.state`；注册中间件、总路由、三类异常处理。测试通常显式传入 `Settings(_env_file=None)`，避免读个人 `.env`。 |
| `get_session(request)` | `infrastructure/persistence/database.py` | FastAPI dependency。为单个请求 `async with sessionmaker()`，yield `AsyncSession`；路由不能缓存它。 |
| `TaskService.create_task()` | `tasks/lifecycle.py` | 校验 user；校验可选活跃 owner conversation；创建 pending Task；有会话时追加 user message；默认提交事务。 |
| `TaskService.save_success/save_failure/save_waiting_approval()` | 同上 | 受状态机保护地写 Task 结果/错误/等待审批，追加 assistant message；后者创建规范化的 `Approval` 记录。 |
| `ApprovalService.decide()` | `tasks/lifecycle.py` | 验证 task、approval 和 user owner，防止重复决策冲突；改变审批和 task 状态。路由决定是否重新投递。 |
| `ConversationService` | `session/conversations.py` | 创建、外部会话解析、owner 获取、消息追加/列出、归档、token 统计。它是 Task 与聊天上下文的桥梁。 |
| `MemoryService` | `memory/user_memory/` | 用户长期记忆创建、owner 读取、确认/修正/忘记/范围/索引队列；API 只做输入路由和事务提交。 |
| `KnowledgeService` | `rag/` | 文件安全导入、解析、分块、owner 列表/删除、检索与 citation 生成。`backend/rag` 是唯一实现边界。 |
| `SkillLifecycleService` | `agent/skill_management/lifecycle.py` | managed Skill 的创建、包安装、状态切换、卸载；文件存储和数据库/能力刷新在该处协调。 |
| `AccountConnectionService` | `integrations/accounts.py` | 外部账号连接密文创建、owner 列表、测试和状态变更；API 永不返还 credentials。 |
| `ReminderService` | `integrations/notifications.py` | reminder 生命周期、notification outbox、桌面 poll/ack。 |
| `TaskEventRepository` / `TaskEventPublisher` | `tasks/events.py` | 前者保证每个 Task 的递增事件序列并安全保存；后者在独立 session 中 best-effort 发布，失败只记 warning，不推翻任务结果。 |
| `enqueue_task_execution()` | `workers/worker.py` | Redis URL 非空且不含 `placeholder` 时设置 broker/backend 并 `execute_task.delay()`；否则 `false`。 |
| `execute_task_by_id()` | `workers/runtime.py` | 一个任务执行 attempt 的总编排：AgentRun、runtime 依赖、异常清理、质量抽样、LangBot dispatch、状态事件、收尾遥测。 |
| `AgentHarness` / `LangGraphExecutor` | `runtime/runner_harness.py` / `runtime/langgraph_executor.py` | 前者定义运行数据/状态持久化边界；后者执行模型与工具循环。新增 Agent 场景应沿此链路扩展，不应从 API 直接绕过 ToolRegistry。 |
| `ToolRegistry` | `tools/` | 工具注册、schema/allowlist/source/snapshot 校验、风险和高风险审批、输入/输出审计脱敏。 |
| `ResultDispatcher` | `tasks/dispatch.py` | 将可回推 LangBot 任务的最终/等待审批状态发送给 `LangBotResultClient` 并更新桥接投递账本。 |

### 11.3 关键持久化实体

| 实体 | 表 | 保存什么 | 被谁主要使用 |
|---|---|---|---|
| `User` | `users` | owner 身份。 | 所有 owner-scoped Service。 |
| `PlatformAccount` | `platform_accounts` | 外部 `platform_user_id` → User 绑定。 | LangBot webhook。 |
| `Task` | `tasks` | 任务输入、平台、类型、状态、会话、模型、结果/错误。 | Task API、Worker、桌面端。 |
| `AgentRun` | `agent_runs` | 同一 Task 的执行 attempt、profile、graph/checkpoint、开始/结束和错误。 | Worker / observability。 |
| `TaskEvent` | `task_events` | 有序可展示事件与脱敏 payload。 | NDJSON、WebSocket、timeline。 |
| `Approval` | `approvals` | 工具/计划/评审审批请求和决定。 | Agent、审批 API。 |
| `Conversation` / `ConversationMessage` | `conversations` / `conversation_messages` | 多轮上下文与与 Task 关联的用户/助手消息。 | ConversationService、桌面端。 |
| `ProcessedMessage` | `processed_messages` | LangBot 去重、任务绑定、回推目标和投递账本。 | webhook、remote bridge、dispatcher。 |
| `ToolLog` / `ModelLog` | 对应日志表 | 工具/模型调用审计。 | diagnostics、local logs。 |
| `Memory*` | `memory_*` | 记忆本体、链接、反馈、检索追踪、策略、索引队列、合并摘要。 | Memory API、runtime。 |
| `Knowledge*` | knowledge 相关表 | 文档、chunk、检索元数据。 | KnowledgeService。 |

---

## 12. 调试、测试与常见故障

### 12.1 从外到内排查任务

1. **API 是否活着**：`GET /health`，再用 token 调用 `GET /local/config`。
2. **任务是否创建**：`GET /api/tasks/{task_id}?user_id=...`，先看 `status`、`error_message`、`conversation_id`。
3. **是否真正入队**：创建响应的 `queued`；若为 false，检查 `REDIS_URL` 是否仍含 `placeholder`、Worker 是否启动。
4. **执行过程**：`GET /api/tasks/{task_id}/events/stream?...` 或 Electron WebSocket。
5. **完整审计**：`GET /api/tasks/{task_id}/diagnostics?...`，查看脱敏后的 model/tool/approval/retrieval 摘要。
6. **审批阻塞**：Task 为 `waiting_approval` 时读 approval 列表；用对应 API 决策。批准后观察新 `queued` 和状态事件。
7. **LangBot 无回复**：查看 `/api/remote-control/bridge/sessions/{message_id}` 的 `delivery_status`、错误摘要和 attempts；只有已绑定 Task 的记录可 replay。

### 12.2 常见现象

| 现象 | 原因与处理 |
|---|---|
| `401 local_api_auth_failed` | `LOCAL_API_AUTH_REQUIRED=true`，缺失/错误 `Authorization: Bearer`。 |
| `503 local_api_auth_unconfigured` | 启用了认证但 `LOCAL_API_TOKEN` 为空；设置本机 token 后重启对应服务。 |
| `404 user_not_found` | `user_id` 不是已存在 User。当前没有公开用户创建接口；先走初始化/测试 fixture/受控数据库流程。 |
| 创建成功但 `queued=false` | Redis URL 是空/placeholder，或未能投递；任务仍可保留，修复队列后按受控流程重投。 |
| 任务卡在 `waiting_approval` | 这是受治理的预期状态；列出审批后明确批准或拒绝。 |
| `/local/settings/validate` 返回 400 | API URL 非 localhost、携带路径/账号密码，或 workdir 不存在/不是目录。 |
| 调用账号连接返回 503 | 未配置可用 `CREDENTIAL_MASTER_KEY`；不要降低为明文。 |
| LangBot 返回 `unbound_user` | 需先为 `adapter:sender_id` 建立 `PlatformAccount` 到 User 的绑定。 |
| 文档/API 字段不确定 | 运行服务后以 `/openapi.json` 为字段真相，再对照 `backend/app/api/schemas/` 和本文调用链。 |

### 12.3 验证命令

```bash
# Python
uv run pytest
uv run pytest --cov
uv run ruff check .
uv run mypy .
uv lock --check

# Electron（在 frontend/desktop）
npm run typecheck
npm run build

# Compose 集成 smoke（需要 Docker）
uv run python -m scripts.ops.compose_smoke
```

测试涉及默认 Settings 或测试 App 时，应显式创建测试 `Settings`（必要时 `_env_file=None`），不得偶然读取开发者个人 `.env`。现有 API 行为验收主要在 `tests/acceptance/`，可从测试中的 URL/断言反查边界场景。

### 12.4 变更接口时应同步更新什么

1. 路由和 `app/api/schemas/`（行为先补 acceptance test）；
2. Electron API client 或 LangBot adapter（如果对应入口受影响）；
3. 本文的总索引、接口条目和调用链；
4. [`frontend-backend-flow.md`](frontend-backend-flow.md)（若影响桌面操作）；
5. 根 `README.md` 的当前运行状态说明；
6. 对应 OpenSpec 阶段文档与测试。

这样可保持“路由—契约—服务—Worker—文档”可追踪，而不会把尚未实现的外部能力误写成生产可用功能。
