# 完整启动与配置手册

适用范围：当前个人 Agent 助手系统，包括 PostgreSQL、Redis、数据库迁移、FastAPI、Celery worker、单实例 Celery Beat、LangBot 通道、模型网关、Electron Web 桌面端和本地 `/local/*` API。

文档分工：本文只说明运行、配置和部署边界；全部 HTTP / WebSocket 接口及其服务、Worker、Agent Runtime 调用链见[《运行与 API 全量上手指南》](运行与API全量上手指南.md)，Electron 可见操作链路见 [frontend-backend-flow.md](frontend-backend-flow.md)。

推荐运行形态：

```text
Docker Compose
├── postgres
├── redis
├── migrate（一次性执行）
├── assistant-api
├── celery-worker
└── celery-beat（只能保留一个实例）

宿主机
└── Electron Web Desktop（V7 主线，frontend/desktop）
```

完整后端优先使用 Compose。桌面端在宿主机运行，通过 `http://127.0.0.1:8000` 访问 API。默认 Compose 不向宿主机暴露 PostgreSQL 和 Redis 端口。

## 1. 启动前准备

需要：

- Python 3.12，版本由 `.python-version` 固定。
- `uv`。
- Docker Desktop，且 Docker daemon 已启动。
- Node.js 和 npm，用于 Electron Web Desktop 开发与打包。

进入仓库根目录：

```bash
cd /Users/cheng/Desktop/assistant
uv sync
cp .env.example .env
```

核心后端默认不安装浏览器自动化、Office 解析/生成和观测 SDK。按需要显式安装可选能力：

```bash
uv sync --extra browser-automation
uv sync --extra office
uv sync --extra observability
```

Electron 桌面端使用 Node 依赖，位于 `frontend/desktop`：

```bash
cd frontend/desktop
npm ci
```

`.env` 只用于本地运行，不提交仓库。`.env.example` 中的占位配置可以运行测试和启动基础服务，但不能完成真实模型、LangBot、Tavily 或外部账号调用。

## 2. 配置完整启动必填项

启动前至少检查 `.env` 中以下配置：

```dotenv
APP_ENV=local
LOG_LEVEL=INFO
SERVICE_NAME=assistant-api

LOCAL_API_AUTH_REQUIRED=true
LOCAL_API_TOKEN=<本机随机 Bearer Token>
CREDENTIAL_MASTER_KEY=<至少 32 个字符的随机主密钥>

DATABASE_URL=postgresql+asyncpg://assistant:assistant@postgres:5432/assistant
REDIS_URL=redis://redis:6379/0
```

可以在本机生成随机值，再手动写入 `.env`：

```bash
openssl rand -hex 32
openssl rand -base64 32
```

要求：

- `LOCAL_API_TOKEN` 为空时，`/api/*`、`/internal/*` 和 `/local/*` 会返回 `503 local_api_auth_unconfigured`；`/health`、`/local/health` 和 LangBot Webhook 不走本机 Bearer Token 校验。
- `CREDENTIAL_MASTER_KEY` 至少 32 个字符。未配置时，SMTP、CalDAV 和浏览器账号连接 fail-closed，不会回退为明文存储。
- Compose 内部数据库主机名必须是 `postgres`，Redis 主机名必须是 `redis`。
- 若修改 `POSTGRES_DB`、`POSTGRES_USER` 或 `POSTGRES_PASSWORD`，必须同步修改 `DATABASE_URL`；不要只改一处。

本地开发默认数据库账号仅适用于本机隔离环境，不应复用于公网或共享部署。

## 3. 配置模型网关

### 3.1 DeepSeek 兼容节点

现有 DeepSeek 配置会生成两个兼容节点：

```dotenv
DEEPSEEK_API_KEY=<真实 API Key>
DEEPSEEK_BASE_URL=<真实 OpenAI-compatible base URL>
DEEPSEEK_LIGHT_MODEL=<Fast Pool 模型标识>
DEEPSEEK_STANDARD_MODEL=<Reasoning Pool 模型标识>
MODELS_TIMEOUT_SECONDS=10
MODELS_RETRY_ATTEMPTS=2
```

> **当前配置名差异**：运行时 `Settings` 读取的是上述 `MODELS_TIMEOUT_SECONDS`、`MODELS_RETRY_ATTEMPTS` 与下文的 `MODELS_NODES_JSON`（见 `backend/infrastructure/settings/config.py`）。当前 `.env.example` 和 `docker-compose.yml` 仍保留旧的 `MODEL_GATEWAY_*` 名称；这些字段会被 Settings 作为未知输入忽略，因此不能依靠它们覆盖默认超时、重试或附加节点配置。在 Compose 配置迁移前，如需覆盖这些运行时值，须向 API 和 Worker 容器传入实际的 `MODELS_*` 字段；不要把旧名当成已兼容的配置。

历史字段映射：

```text
light    → Fast Pool
standard → Reasoning Pool
```

`agent` 智能意图路由使用 Fast Pool 通用模型，不加载微调意图模型。固定类型 `plan`、`learn`、`daily`、`office`、`memory` 和 `status` 不经过意图路由。

### 3.2 GLM、企业接口和 Private Pool

额外 OpenAI-compatible 节点通过单行 JSON 数组配置：

```dotenv
MODELS_NODES_JSON=[]
```

节点字段：

| 字段 | 含义 |
|---|---|
| `id` | 稳定且唯一的节点 ID |
| `pool` | `fast`、`reasoning` 或 `private` |
| `provider` | 日志中的供应商标识，例如 `glm`、`enterprise`、`qwen` |
| `base_url` | OpenAI-compatible API base URL，不包含 `/chat/completions` |
| `model` | 供应商模型标识 |
| `api_key` | 服务端鉴权值，必须非空且只保存在 `.env` |
| `capacity` | 节点最大并发容量，必须大于 0 |
| `cost_advantage` | `0` 到 `1`，越高表示成本优势越高 |
| `latency_target_ms` | 延迟归一化目标值 |
| `enabled` | 只有 `true` 且配置完整时才参与选择 |

仅用于说明结构的禁用占位示例：

```dotenv
MODELS_NODES_JSON=[{"id":"glm-flash-placeholder","pool":"fast","provider":"glm","base_url":"https://provider.invalid/v1","model":"glm-flash-placeholder","api_key":"placeholder-api-key","capacity":4,"cost_advantage":0.8,"latency_target_ms":1500,"enabled":false}]
```

不要把真实 Key、私有 URL 或企业接口配置提交到 Git。本地小模型和自部署 Qwen 当前只有禁用占位边界；在真实服务地址、模型名和鉴权配置完成前必须保持 `enabled=false`。

池内选择使用：

```text
可用容量 × 40%
- 延迟惩罚 × 25%
- 失败率 × 20%
- 成本惩罚 × 15%
```

同池节点失败时执行有界故障转移；Private Pool 不自动把请求发送到公共池。当前运行指标是单进程内状态，不代表跨 Celery 进程的全局负载。

## 4. 配置可选外部能力

不使用的能力保持占位或关闭即可。

### LangBot

```dotenv
LANGBOT_WEBHOOK_SECRET=<Webhook 校验密钥>
LANGBOT_API_BASE_URL=<真实 LangBot API base URL>
LANGBOT_API_KEY=<真实 API Key>
LANGBOT_SEND_TIMEOUT_SECONDS=10
```

### 搜索 provider chain（V10）

`search.web` 保持同一个工具名，但运行时会按 provider chain 降级。默认顺序是 Tavily → Brave → DuckDuckGo：

```dotenv
TAVILY_BASE_URL=<真实 Tavily base URL>
TAVILY_API_KEY=<真实 API Key>
TAVILY_TIMEOUT_SECONDS=10
TAVILY_MAX_RESULTS=5
SEARCH_PROVIDER_ORDER=tavily,brave,duckduckgo
BRAVE_SEARCH_API_KEY=
BRAVE_SEARCH_BASE_URL=https://api.search.brave.com/res/v1/web/search
DUCKDUCKGO_SEARCH_ENABLED=false
DUCKDUCKGO_SEARCH_BASE_URL=https://api.duckduckgo.com/
SEARCH_FALLBACK_ON_EMPTY=true
SEARCH_PROVIDER_TIMEOUT_SECONDS=
```

说明：

- Tavily 有 API key 时优先使用。
- Brave 只有配置 `BRAVE_SEARCH_API_KEY` 后才会加入可用 provider。
- DuckDuckGo 必须显式 `DUCKDUCKGO_SEARCH_ENABLED=true` 才会作为零 key 兜底；默认关闭，避免无意外部访问。
- `SEARCH_PROVIDER_TIMEOUT_SECONDS` 为空时复用 `TAVILY_TIMEOUT_SECONDS`。
- provider 失败、空结果降级和最终 provider 会写入脱敏 ToolLog；不会把真实 API key 写入日志。

### Langfuse

```dotenv
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=
```

public/secret key 必须同时存在才启用。未配置或只配置一项时使用零网络 No-op，不影响任务执行。

本地确定性评测不需要 Langfuse：

```bash
uv run python scripts/run_evaluation.py
```

需要把同一批 core command cases 作为 Langfuse experiment 上报时，再显式开启：

```bash
uv run python scripts/run_evaluation.py --langfuse
```

`--langfuse` 不替代本地回归判断；它只在配置完整时额外创建 experiment，缺少 key 时命令返回配置错误而不会写入真实外部服务。

### Workspace / Session Workspace

```dotenv
MANAGED_SKILLS_ROOT=var/skills
MANAGED_PROMPTS_ROOT=var/prompts
SKILL_PACKAGES_ROOT=var/skill-packages
ARTIFACTS_ROOT=var/artifacts
SESSION_WORKSPACE_ROOT=var/workspace/sessions
WORKSPACE_CONTEXT_ROOT=.
WORKSPACE_CONTEXT_ENABLED=true
WORKSPACE_CONTEXT_DENY_GLOBS=.env,.env.*,**/.env,**/.env.*,.git/**,**/.git/**,node_modules/**,**/node_modules/**,__pycache__/**,**/__pycache__/**,*.pem,**/*.pem,*.key,**/*.key,*.p12,**/*.p12,*.sqlite,**/*.sqlite,*.db,**/*.db
WORKSPACE_CONTEXT_MAX_FILE_BYTES=200000
WORKSPACE_CONTEXT_MAX_RESULTS=50
READONLY_SHELL_ENABLED=false
READONLY_SHELL_TIMEOUT_SECONDS=10
READONLY_SHELL_MAX_OUTPUT_CHARS=50000
KNOWLEDGE_ROOT=var/knowledge
```

`MANAGED_SKILLS_ROOT` 保存经过治理安装的 managed Skill；`SKILL_PACKAGES_ROOT` 保存待审批 Skill package；`MANAGED_PROMPTS_ROOT` 保存动态 Prompt managed override 和版本元数据。`ARTIFACTS_ROOT` 保存最终可见产物，`KNOWLEDGE_ROOT` 保存知识库数据，`SESSION_WORKSPACE_ROOT` 保存单个会话的 `input/`、`work/`、`output/` 和 `audit/` 工作目录。Session Workspace 是普通任务材料和中间产物边界，不等同于 Sandbox；`input/` 为 session 共享输入区，推荐任务中间/输出/审计文件写入 `work/{task_id}/`、`output/{task_id}/`、`audit/{task_id}/`；不再提供旧 flat `work/<filename>`、`output/<filename>`、`audit/<filename>` reserve API。`WORKSPACE_CONTEXT_ROOT` 是 Agent 读取本地项目上下文的只读根；`workspace.list`、`workspace.read_file`、`workspace.search_text`、`workspace.find_files` 和 `workspace.read_doc` 只能在该根内工作，并按 deny globs、大小限制和文本类型限制 fail-closed。`READONLY_SHELL_ENABLED=false` 表示默认不暴露 `shell.readonly_exec`；如显式开启，它也只允许固定只读 argv 命令，不替代 `shell.exec`。

### 浏览器

```dotenv
BROWSER_ENABLED=false
BROWSER_TIMEOUT_SECONDS=20
BROWSER_MAX_TEXT_CHARS=50000
```

启用前必须显式安装可用 Chromium。系统不读取宿主机默认浏览器 Profile；登录状态按用户加密保存在数据库中，不使用独立浏览器状态目录。

### Sandbox provider

```dotenv
SANDBOX_PROVIDER=none
SHELL_EXEC_ENABLED=false
SANDBOX_WORKSPACE_ROOT=var/sandbox
SANDBOX_DOCKER_IMAGE=
SANDBOX_DOCKER_ALLOWED_IMAGES=
SANDBOX_TIMEOUT_SECONDS=30
```

本地 Agent 默认使用 `SANDBOX_PROVIDER=none`，并且不暴露 `shell.exec`。Docker 只是高风险 shell 执行的可选 provider；只有同时设置 `SANDBOX_PROVIDER=docker`、`SHELL_EXEC_ENABLED=true`、`SANDBOX_DOCKER_IMAGE` 和 `SANDBOX_DOCKER_ALLOWED_IMAGES` 后，`shell.exec` 才会进入工具目录。失败不会回退到宿主机 Shell。

兼容说明：旧的 `SANDBOX_ENABLED=true`、`SANDBOX_IMAGE` 和 `SANDBOX_ALLOWED_IMAGES` 仍可读取，并会解析为 Docker provider + shell 执行启用；新配置应优先使用 provider 命名。

### Task / Scheduler / V10 自主工具

```dotenv
RUNNING_TASK_TIMEOUT_SECONDS=300
PENDING_TASK_COMPENSATION_DELAY_SECONDS=120
SCHEDULER_MAINTENANCE_INTERVAL_SECONDS=300
```

这些配置约束后台任务和心跳维护：

- `task.start_background` 只创建 owned task，不直接在工具 handler 内执行 worker。
- `task.check_status`、`task.get_result` 和 `task.cancel` 只能操作同一 user 的 task，并返回有界事件/结果摘要。
- `schedule.create` 支持 `at`、`every` 和最小 `cron` 模式；heartbeat 到期后幂等 materialize pending task。
- `RUNNING_TASK_TIMEOUT_SECONDS` 控制运行中任务超时补偿；`PENDING_TASK_COMPENSATION_DELAY_SECONDS` 控制 pending 补偿延迟；`SCHEDULER_MAINTENANCE_INTERVAL_SECONDS` 应与唯一 Celery Beat 周期保持一致。

### Dynamic Prompt / Skills acquisition（V10）

```dotenv
MANAGED_SKILLS_ROOT=var/skills
MANAGED_PROMPTS_ROOT=var/prompts
SKILL_PACKAGES_ROOT=var/skill-packages
```

V10 的 Skills 和 Prompt 都是 managed artifact，不允许 Agent 直接写源码目录：

- `skills.install_candidate` 通过 SkillLifecycleService 安装 managed Skill，默认 `enabled=false`；启用/禁用继续走治理工具。
- `skills.propose_create` 创建 `EvolutionChange` / Approval 候选，不直接写 managed root。
- 默认 Prompt 模块位于 `backend/resources/prompts/defaults/`，只读；`MANAGED_PROMPTS_ROOT` 只保存 approved managed override。
- `prompt.propose_change` 只创建变更提案和审批；批准并 apply 后下一次模型请求才使用 override。
- `prompt.rollback` 恢复上一版本；prompt 候选会拒绝 secret-like 内容、超大内容、未知模块、路径逃逸和试图关闭审批/降低风险的文本。

### Mem0、子 Agent 和质量 Judge

```dotenv
MEM0_CONFIG_PATH=
MEM0_SEARCH_LIMIT=5

SUBAGENT_ENABLED=true
SUBAGENT_MAX_COUNT=3
SUBAGENT_CONCURRENCY=2
SUBAGENT_TIMEOUT_SECONDS=30

QUALITY_JUDGE_SAMPLE_RATE=0
QUALITY_JUDGE_POLICY_VERSION=judge-v1
QUALITY_JUDGE_THRESHOLD=0.6
```

- Mem0 未配置时使用 SQL 记忆路径。
- 子 Agent 仍受总数、并发、超时、ToolRegistry 和审批边界控制。
- Judge 采样率默认 `0`，不会调用远端 Judge。

## 5. 完整启动后端

启动默认完整栈：

```bash
docker compose up --build -d
```

默认启动：

- `postgres`
- `redis`
- `migrate`
- `runtime-init`
- `assistant-api`
- `celery-worker`
- `celery-beat`

`migrate` 是一次性服务，会在 API、worker 和 Beat 启动前执行：

```bash
alembic upgrade head
```

当前迁移包含任务事件表，GUI 的计划先返和流式内容依赖该迁移。不要跳过迁移直接启动旧数据库。

`runtime-init` 是一次性服务。它为 named volumes 创建运行时目录并分配容器内应用用户的写权限；API、worker 和 Beat 仅在它成功退出后启动。该初始化不配置模型、搜索、LangBot 或账号 Provider，模板中的占位配置只能验证基础本地服务。

检查服务：

```bash
docker compose ps -a
docker compose logs migrate
docker compose logs --tail=100 assistant-api celery-worker celery-beat
```

预期：

- `migrate` 状态为成功退出。
- `runtime-init` 状态为成功退出。
- `assistant-api`、`celery-worker`、`celery-beat`、`postgres`、`redis` 为运行或健康状态。
- 同一套数据库和 Redis 只能运行一个 `celery-beat`，否则周期维护任务会重复投递。

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

预期：

```json
{"service_name":"assistant-api","status":"ok"}
```

API 只映射到宿主机 `127.0.0.1:8000`；PostgreSQL 和 Redis 默认只在 Compose 网络内访问。

## 6. 初始化第一个用户

当前系统不会自动创建用户。进入 PostgreSQL：

```bash
docker compose exec postgres psql -U assistant -d assistant
```

创建本地 GUI 用户：

```sql
insert into users (id, display_name, created_at, updated_at)
values (
  '00000000-0000-0000-0000-000000000001',
  'Local User',
  now(),
  now()
)
on conflict (id) do nothing;
```

退出：

```text
\q
```

示例 UUID 只适合本地初始化。已有用户时直接使用真实的现有用户 ID，不重复创建。

## 7. Electron Web Desktop 绑定与配置

Electron 是 V7 新桌面主线。当前采用 **external installed mode**：

```text
Electron Desktop → 127.0.0.1:8000 /local/* API
Python API / worker / Beat / PostgreSQL / Redis 由用户单独启动
```

Electron 安装包或开发窗口不内置 Python runtime、`.venv`、PostgreSQL、Redis、Playwright、Office 依赖、历史 Qt 桌面依赖或本地模型。后端不可达时，Electron 应显示未连接诊断状态，而不是空白窗口。

### 7.1 启动 Electron 开发窗口

先按前文启动后端，再启动桌面端：

```bash
cd frontend/desktop
npm ci
npm run dev
```

首次打开后，在 Settings 中填写：

| 字段 | 对应含义 | 示例或说明 |
|---|---|---|
| Local API URL | 本地 Agent Server 地址 | `http://127.0.0.1:8000` |
| User ID | 数据库 `users.id` | 必须是已经存在的用户 ID |
| Default workdir | 默认工作目录 | 必须是本机已存在目录 |
| Model | 默认模型档位 | `standard` 或 `light` |
| Approval policy | 默认审批策略偏好 | 只能作为前端偏好，不能绕过后端审批策略 |

点击保存时，前端会调用：

```text
POST /local/settings/validate
```

后端会校验：

- Local API URL 必须指向 `localhost`、`127.0.0.1` 或 `::1`；
- URL 不能包含用户名、密码或额外 path；
- 默认工作目录必须存在且是目录；
- 模型和审批策略必须是允许值。

设置由 Electron 主进程写入用户数据目录的 `desktop-settings.json`。当前 Electron 桌面端不保存 API Token；如果后端启用 `LOCAL_API_AUTH_REQUIRED=true`，后续需要补充受控 token 保存和请求头注入，不能把 token 暴露给 renderer。

### 7.2 Electron 使用的本地 API

Electron 使用 V7 `/local/*` 契约，不直接 import Python 模块。当前 `frontend/desktop/src/renderer/api.ts` 会把 Settings 中的 `userId` 显式传给 owner-scoped 接口：

```text
GET  /local/health
GET  /local/config
POST /local/settings/validate
GET  /local/tasks?user_id=<user_id>
POST /local/tasks
GET  /local/tasks/{task_id}?user_id=<user_id>
POST /local/tasks/{task_id}/messages
GET  /local/conversations/{conversation_id}/token-stats?user_id=<user_id>
GET  /local/tasks/{task_id}/events?user_id=<user_id>&after_event_id=<event_id>
WS   /local/tasks/{task_id}/events/stream?user_id=<user_id>&after_event_id=<event_id>
GET  /local/tasks/{task_id}/logs?user_id=<user_id>
POST /local/tasks/{task_id}/approvals/{approval_id}
```

远程桥接面板还会读取普通 `/api/remote-control/*` 路由，用于展示和重放 LangBot 入站消息账本：

```text
GET  /api/remote-control/bridge/sessions?limit=20
GET  /api/remote-control/bridge/sessions/{message_id}
POST /api/remote-control/bridge/sessions/{message_id}/replay
```

事件流使用稳定 `event_id` 游标恢复。桌面端断线后先读取任务快照和游标之后的事件，再恢复 WebSocket 订阅。审批提交复用后端 `ApprovalService`，重复批准不会重复恢复工具调用。

如果需要按按钮追踪完整实现链路，阅读 [frontend-backend-flow.md](frontend-backend-flow.md)：它逐项列出 Electron UI 操作、renderer 函数、HTTP/WS/IPC 请求、FastAPI 路由、Domain Service、Worker/Agent 执行层和数据表。完整 API 契约和后端调用链见[《运行与 API 全量上手指南》](运行与API全量上手指南.md)。

### 7.3 Electron 开发者体验配置

任务控制台源码覆盖：

- 启动时通过 `GET /local/health`、`GET /local/config`、`GET /local/tasks` 建立连接状态和任务列表；
- 创建任务、续写任务、刷新任务快照、会话 token stats；
- `task.message.delta`、`task.message.completed`、`task.log.appended`、`task.tool.requested`、`task.failed`、`task.completed` 等事件展示；
- timeline、logs、approvals、changes、settings、remote bridge 六个 Inspector tab；
- approval approve/reject 到后端 `ApprovalService`，批准后由后端决定是否重新入队；
- 文件引用、只读 diff、命令 stdout/stderr/exit code 由事件 payload 派生展示；
- Settings 页通过后端校验 Local API URL 和 workdir，再由主进程写入 `desktop-settings.json`。

安全边界：

- renderer 不直接访问 Node.js；
- 主进程通过 preload 只暴露设置读取/保存、打开路径、打开外链；
- 外部链接使用系统浏览器打开；
- 前端只展示和提交用户决策，审批和权限以后端为准。

### 7.4 Electron 打包配置

打包配置位于：

```text
frontend/desktop/electron-builder.json
frontend/desktop/RELEASE.md
scripts/ops/desktop_web_release_check.py
```

可重复检查：

```bash
uv run python scripts/ops/desktop_web_release_check.py
```

构建目录包：

```bash
cd frontend/desktop
npm ci
npm run build
npm run dist:dir
```

构建平台安装包：

```bash
npm run dist
```

当前配置排除 `.venv`、`.git`、mypy/pytest/ruff 缓存、Vite 缓存、测试目录、node module 缓存和 source map。包体、冷启动耗时和空闲内存必须在真实构建和本机启动后记录；未测量前保持 `not measured`，不能声明生产自动更新、签名或跨平台发布已完成。

## 8. LangBot 绑定与配置

LangBot 集成分为两个独立方向：

```text
入站：LangBot → POST /api/webhooks/langbot → 创建任务
出站：worker → LANGBOT_API_BASE_URL → 回推结果或提醒
```

只配置入站 Webhook 不代表结果回推可用；只配置出站 API 也不能建立发送者与本地用户的绑定。

### 8.1 配置入站 Webhook

后端配置：

```dotenv
LANGBOT_WEBHOOK_SECRET=<Webhook 共享密钥>
```

在 LangBot 侧把目标配置为当前 API 的：

```text
POST <可被 LangBot 访问的 API 地址>/api/webhooks/langbot
```

请求必须携带：

```text
X-LangBot-Secret: <与 LANGBOT_WEBHOOK_SECRET 相同的值>
Content-Type: application/json
```

注意：Compose 默认只把 API 映射到 `127.0.0.1:8000`。如果 LangBot 不在同一台主机上，它无法直接访问该回环地址。需要由部署者提供受保护的反向代理或私有网络入口；不要未经鉴权直接把内部 API 暴露到公网。

当前后端接受的 Webhook JSON 契约：

```json
{
  "message_id": "message-unique-id",
  "adapter": "discord",
  "conversation": {
    "id": "conversation-id",
    "type": "channel"
  },
  "sender": {
    "id": "sender-id"
  },
  "message": {
    "type": "text",
    "text": "/status"
  }
}
```

字段要求：

- `message_id` 必须稳定且唯一，用于去重。
- `adapter` 和 `sender.id` 共同决定用户绑定键。
- `conversation.id`、`conversation.type` 会保存为结果回推目标。
- 当前只接受 `message.type = text`。
- 已知命令为 `/plan`、`/learn`、`/daily`、`/office`、`/memory`、`/status`。
- 不以 `/` 开头的自由文本会先进入结构化 intent 判定；能稳定落到四个核心意图就创建对应任务，否则返回 `needs_confirmation` 或 `needs_new_capability`，不再直接创建 `agent` 任务。
- 未知斜杠命令通常返回 `needs_new_capability`；空消息或格式错误仍返回 `unknown_command`。

### 8.2 绑定 LangBot 发送者到本地用户

绑定键固定为：

```text
platform_user_id = <adapter>:<sender.id>
```

例如 Webhook 中：

```json
{"adapter":"discord","sender":{"id":"demo-user"}}
```

对应：

```text
discord:demo-user
```

进入 PostgreSQL：

```bash
docker compose exec postgres psql -U assistant -d assistant
```

创建绑定：

```sql
insert into platform_accounts (
  id, user_id, platform, platform_user_id, created_at, updated_at
)
values (
  '00000000-0000-0000-0000-000000000101',
  '00000000-0000-0000-0000-000000000001',
  'langbot',
  'discord:demo-user',
  now(),
  now()
)
on conflict (platform, platform_user_id) do nothing;
```

必须使用 Webhook 实际提供的 `adapter` 和 `sender.id`，不要根据显示昵称猜测。一个 LangBot 发送者只能通过唯一 `(platform, platform_user_id)` 绑定到一个本地用户。

绑定不存在时，Webhook 返回 `ok=true`、`reason=unbound_user`，但不会创建任务。

查询现有 LangBot 绑定：

```sql
select id, user_id, platform_user_id, created_at
from platform_accounts
where platform = 'langbot'
order by created_at;
```

如果 `<adapter>:<sender.id>` 已经绑定到了错误用户，前面的 `ON CONFLICT DO NOTHING` 不会自动改绑。确认目标用户后显式更新：

```sql
update platform_accounts
set user_id = '00000000-0000-0000-0000-000000000001',
    updated_at = now()
where platform = 'langbot'
  and platform_user_id = 'discord:demo-user';
```

重新绑定只影响后续新消息；已经创建的任务仍归属于创建时解析出的原用户。不要通过直接修改历史任务来迁移用户。

### 8.3 配置出站结果回推

后端配置：

```dotenv
LANGBOT_API_BASE_URL=<接收消息发送请求的完整 LangBot API 地址>
LANGBOT_API_KEY=<Bearer Token>
LANGBOT_SEND_TIMEOUT_SECONDS=10
```

当前实现会直接向 `LANGBOT_API_BASE_URL` 发起 POST，不会自动追加固定路径。因此这里必须填写真实的完整接收端点，而不是只填写域名。

出站请求包含：

```json
{
  "adapter": "discord",
  "conversation_id": "conversation-id",
  "conversation_type": "channel",
  "text": "任务结果",
  "idempotency_key": "bounded-idempotency-key"
}
```

并携带：

```text
Authorization: Bearer <LANGBOT_API_KEY>
Idempotency-Key: <同一个幂等键>
```

任务结果使用创建该任务的原始 Webhook conversation target，不会只根据 `platform_accounts` 猜测会话。LangBot 提醒使用该用户最近一次成功创建任务时保存的 conversation target。

### 8.4 远程桥接账本与回放

每条 LangBot 入站消息会写入远程桥接账本，保存：

- `message_id`、`adapter`、`sender_id`、`conversation.id`、`conversation.type`；
- 规范化后的消息正文和结构化 intent 结果；
- 创建出的任务 ID；
- 结果回推状态、attempt 次数、脱敏后的失败摘要和最近一次回推响应。

查询最近远程会话：

```bash
curl 'http://127.0.0.1:8000/api/remote-control/bridge/sessions?limit=20'
```

按 conversation 过滤：

```bash
curl 'http://127.0.0.1:8000/api/remote-control/bridge/sessions?conversation_id=local-test-conversation'
```

查询单条消息：

```bash
curl 'http://127.0.0.1:8000/api/remote-control/bridge/sessions/local-test-message-1'
```

对已绑定任务但回推失败或待重试的消息执行回放：

```bash
curl -X POST 'http://127.0.0.1:8000/api/remote-control/bridge/sessions/local-test-message-1/replay'
```

回放只重新执行结果派发，不会重新创建任务，也不会绕过 LangBot 出站幂等键。Electron 的 `bridge` 面板读取同一组接口，用于查看会话、刷新状态、跳转任务和触发失败回放。

### 8.5 本地验证 Webhook 和绑定

先确保测试 payload 中的 `adapter`、`sender.id` 已写入 `platform_accounts`。然后从能够访问 API 的环境执行：

```bash
curl -X POST http://127.0.0.1:8000/api/webhooks/langbot \
  -H 'Content-Type: application/json' \
  -H 'X-LangBot-Secret: <LANGBOT_WEBHOOK_SECRET>' \
  -d '{
    "message_id":"local-test-message-1",
    "adapter":"discord",
    "conversation":{"id":"local-test-conversation","type":"channel"},
    "sender":{"id":"demo-user"},
    "message":{"type":"text","text":"/status"}
  }'
```

可能的 `reason`：

| reason | 含义 |
|---|---|
| `task_created` | 绑定存在，任务已创建并尝试投递 |
| `duplicate_message` | 相同 `message_id` 已处理 |
| `unbound_user` | `<adapter>:<sender.id>` 没有用户绑定 |
| `unknown_command` | 空消息或只有 `/` 这类格式错误 |
| `needs_confirmation` | 自由文本无法稳定映射到现有核心意图 |
| `needs_new_capability` | 请求超出当前核心能力集合 |

每次重新测试应更换 `message_id`，否则会命中去重。

### 8.6 LangBot 绑定排查顺序

1. `curl /health` 确认 API 可用。
2. 检查请求头是否与 `LANGBOT_WEBHOOK_SECRET` 完全一致。
3. 从实际 Webhook 日志读取 `adapter` 和 `sender.id`。
4. 查询 `platform_accounts` 是否存在完全一致的 `<adapter>:<sender.id>`。
5. 检查 Webhook ACK 的 `reason`。
6. 若任务已创建但无结果，检查 Redis、worker 和任务状态。
7. 若任务成功但没有回推，检查原始 conversation target、`LANGBOT_API_BASE_URL`、API Key 和 worker 日志。
8. 若 LangBot 提醒失败，先确认该用户曾通过 LangBot 成功创建过至少一个任务。

## 9. 本地进程开发模式

只有 PostgreSQL 和 Redis 已通过宿主机可访问地址运行时，才使用本地进程模式。默认 Compose 没有映射它们的宿主机端口，因此不能直接把默认 Compose 的 `postgres`、`redis` 主机名用于宿主机进程。

准备一个不会提交的本地 `.env`，将地址改为实际可达地址，例如：

```dotenv
DATABASE_URL=postgresql+asyncpg://<user>:<password>@127.0.0.1:<port>/<database>
REDIS_URL=redis://127.0.0.1:<port>/0
```

先迁移：

```bash
uv run alembic upgrade head
```

V6-01 迁移 revision 为 `202607150004`。它兼容扩展现有 `memories` 表，并创建 `memory_links`、`memory_feedback`、`memory_index_outbox`；不会迁移或更换已有 SQL memory ID。升级前仍应先备份 PostgreSQL。

V6-02 迁移 revision 为 `202607150005`，新增 `conversation_summaries` 和 `memory_blocks`。原始 `conversation_messages` 不会因摘要或压缩被删除；回滚 V6-02 只移除 summary/block 表。

V6-03 迁移 revision 为 `202607160001`，扩展 Memory candidate evidence 并新增 `memory_policies`。当前没有新增环境变量；未注入 candidate extractor 时不会调用模型或自动生成候选。

V6-04 迁移 revision 为 `202607160002`，新增 `memory_retrieval_traces` 和 `memory_retrieval_trace_items`。trace 不保存 query/content 原文；备份 manifest 已包含两表。召回权重通过代码中的版本化 `RetrievalWeights` 集中管理，不新增密钥。

V6-05 迁移 revision 为 `202607160003`，为 Memory 增加 `event_time`、`observed_at`，并新增 `memory_consolidation_runs`、`memory_consolidation_digests`、`memory_consolidation_decisions`。heartbeat 会对前一个完整 UTC 日和前一个完整周执行幂等、有界 consolidation；重复运行不会重复 digest/link。

V6-06 不新增 migration、环境变量或外部依赖。Memory Center API 和只读导出复用 V6-01 至 V6-05 的 SQL 表；PostgreSQL 仍是唯一事实源。

V6-07 迁移 revision 为 `202607160004`，新增 `memory_release_reports`、`memory_retrieval_policy_versions`、`memory_effectiveness` 和 `memory_effectiveness_events`。effectiveness 先校验 Memory owner，并以 evidence key 幂等；retrieval policy 先以 shadow 保存，只有同 owner/scope/version 的通过报告和显式 approval 才能激活，rollback 只恢复父版本，不删除候选、报告或新 Memory 数据。

分别启动：

```bash
uv run uvicorn --app-dir backend app.main:app --reload
PYTHONPATH=backend:. uv run celery -A workers.worker:celery_app worker --loglevel=INFO
PYTHONPATH=backend:. uv run celery -A workers.worker:celery_app beat --loglevel=INFO
cd frontend/desktop && npm ci && npm run dev
```

不要同时运行本地 Beat 和 Compose Beat。

### 9.1 V6 Memory 发布门禁

运行自动门禁：

```bash
uv run python scripts/run_memory_release_gate.py
```

默认脱敏 fixture 的自动指标通过，但会返回 `manual_evidence_pending` 和退出码 1。真实本机试用不得提交原始对话；复制 `docs/v6/v6-release-evidence.example.json` 到被 Git 忽略的 `var/v6-release-evidence.json`，完成知识更新、用户纠正、遗忘和长会话压缩后，只填写脱敏 evidence ID 与时间：

```bash
uv run python scripts/run_memory_release_gate.py \
  --manual-evidence var/v6-release-evidence.json
```

只有自动 hard gates、quality thresholds 和四类本机 evidence 同时通过时才返回 0。cross-user leak 或 forbidden write 任何一次均硬失败；shadow policy 不影响生产 Context Pack。

V6 收口验证已在合成用户和本机临时数据库上完成四类真实操作，并通过隔离 Compose smoke；`var/v6-release-evidence.json` 和试用数据库均被 Git 忽略，不作为其他环境的发布凭据。每个部署环境必须重新完成自己的 evidence manifest 和 release gate。

## 10. 日常启停与查看日志

启动或应用配置变更：

```bash
docker compose up --build -d
```

查看状态：

```bash
docker compose ps -a
```

持续查看日志：

```bash
docker compose logs -f assistant-api celery-worker celery-beat
```

只重启某个服务：

```bash
docker compose restart assistant-api
docker compose restart celery-worker
```

停止但保留数据库和运行数据：

```bash
docker compose down
```

不要在需要保留数据时执行 `docker compose down -v`；`-v` 会删除 PostgreSQL、Skills、知识库、浏览器状态和其他命名卷。

## 11. 升级代码和数据库

更新代码后：

```bash
uv sync
docker compose up --build -d
```

Compose 会重新运行 `migrate` 并等待迁移成功后启动 API、worker 和 Beat。检查：

```bash
docker compose logs migrate
docker compose ps -a
curl http://127.0.0.1:8000/health
```

恢复旧数据库后，应先检查是否存在逾期 `pending` 或 `running` 任务，再启动 worker；维护任务可能补偿 pending 任务或终止超时 running 任务。

## 12. 备份、恢复和运行验证

创建 PostgreSQL 备份：

```bash
docker compose --profile ops build ops
docker compose --profile ops run --rm ops \
  scripts.ops.backup --output-dir /backups
```

备份写入宿主机：

```text
var/backups/
```

备份 manifest 的表计数包含 `memories`、links/feedback/index outbox、conversation summaries/blocks、policies、retrieval traces/items、consolidation runs/digests/decisions，以及 V6-07 release reports/policy versions/effectiveness/events。restore 会比较完整 table-count 字典和 migration version；Memory Center、Obsidian 导出和 release evidence manifest 都不会替代 PostgreSQL 备份。

恢复只允许写入明确确认的空数据库，并要求校验 manifest：

```bash
docker compose --profile ops run --rm ops \
  scripts.ops.restore \
  --manifest /backups/<manifest-file> \
  --confirm-empty
```

隔离 Compose smoke：

```bash
uv run python -m scripts.ops.compose_smoke
```

限定时长 soak 示例：

```bash
uv run python -m scripts.ops.soak \
  --duration-seconds 300 \
  --interval-seconds 10 \
  --api-base-url http://127.0.0.1:8000 \
  --output var/soak-report.json
```

## 13. 常见问题

### `/health` 正常，但桌面端请求返回 503

检查：

```dotenv
LOCAL_API_AUTH_REQUIRED=true
LOCAL_API_TOKEN=<非空值>
```

Electron V7 当前 renderer 不保存或注入 API Token；如果启用 `LOCAL_API_AUTH_REQUIRED=true`，`/local/*` 中除 `/local/health` 外的接口会返回认证错误，直到补充受控 token 保存和请求头注入。当前本地桌面开发建议先在受控本机环境关闭该开关，或只使用无需 token 的健康检查确认后端连通性。

修改 `.env` 后执行：

```bash
docker compose up -d --force-recreate assistant-api
```

### 任务创建成功但 `queued=false`

说明 API 已创建 pending 任务，但 Redis/Celery 投递不可用。检查：

```bash
docker compose ps redis celery-worker
docker compose logs --tail=200 celery-worker redis
```

不要把该任务显示为 running。恢复 worker 后由补偿路径重新投递，或创建新任务验证。

### 任务一直是 pending

检查 worker 是否健康、API 与 worker 的 `REDIS_URL` 是否一致，以及迁移是否成功：

```bash
docker compose logs migrate
docker compose logs --tail=200 celery-worker
```

### 模型请求访问占位地址

`.env.example` 的 DeepSeek URL、模型名和 Key 都是占位值。填写真实配置并重建 API/worker：

```bash
docker compose up --build -d assistant-api celery-worker
```

额外节点必须同时满足：字段完整、`capacity > 0`、`api_key` 非空、`enabled=true`。

### GUI 有计划但暂时没有回答增量

这可能是正常行为：

- provider 尚未生成可展示的 `answer` 字段；
- 任务处于工具执行阶段；
- `learn`、`daily`、`office` 正在等待 Review；
- 事件流断开后 GUI 已回退状态轮询。

检查 API、worker 日志和任务最终状态，不要仅凭首批内容时间判断任务失败。

### 账号连接返回主密钥不可用

配置至少 32 字符的 `CREDENTIAL_MASTER_KEY`，然后同时重建 API 和 worker。不要在已有加密账号数据后随意更换主密钥，否则旧密文无法解密。

### Beat 任务重复

确认只有一个 Beat：

```bash
docker compose ps celery-beat
```

不要同时运行 Compose Beat、本地 Beat 或第二套指向同一数据库/Redis 的 Beat。

## 14. 安全边界

- 不提交 `.env`、真实密钥、Token、Cookie、浏览器状态、私有 URL、知识文件、备份或运行报告。
- API 默认只监听宿主机回环地址；不要未经鉴权直接映射到公网。
- Model Gateway 日志和任务事件只保存脱敏摘要，不保存 provider 鉴权头。
- Memory 写入在 SQL、Mem0 和 outbox 之前执行确定性敏感扫描；Authorization、Cookie、Token、密码、私钥和恢复码等 forbidden 内容会被拒绝，错误只返回安全原因。
- 外部网页、邮件、文档、Tool 和子 Agent 内容标记为 `untrusted_external`；其中的“请记住”不会被当作用户授权。候选 extractor 失败发生在任务成功提交之后，不会反转任务状态。
- GUI 只展示安全执行计划和最终回答内容，不展示工具控制 JSON 或隐藏推理。
- 高风险 Tool 仍需要精确审批；模型池选择不能扩大 Tool 权限。
- 本地小模型和 Private Qwen 占位不会因被声明就自动启用。
