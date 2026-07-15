# assistant-api

个人 Agent 助手系统。当前产品入口只保留两类：LangBot 作为主消息通道和响应通道，PySide6 原生小窗口作为本机 GUI；FastAPI 提供二者共用的内部 API。旧网页控制台、项目 CLI 和其他直连消息通道已经移除。

项目已完成 MVP 阶段 09、V2、V3 与 V4，并进入 V5-00 工程验收。当前代码已具备本机 API 认证、加密账号连接、真实 SMTP/CalDAV 动作、受限登录态浏览器、个人知识库、持久提醒通知、Compose/CI 和备份恢复入口。真实第三方账号联调与长时间连续运行仍需在用户提供测试账号和本地授权后完成，未配置项不会被标记为通过。

## 项目介绍

- LangBot 接收多平台消息并把结果推回原会话。
- PySide6 GUI 提交任务、查看结果、处理计划/工具/复核审批、管理本地 Skills 并驻留系统托盘。
- FastAPI、PostgreSQL、Redis 与 Celery 组成后端运行层。
- `/plan`、`/learn`、`/daily`、`/office` 使用 Agent Profile、Planning Layer、模型驱动 LangGraph Agent Core 与 ToolRegistry。
- `/memory`、`/status` 使用确定性的本地服务，不调用外部模型。
- LangBot 无斜杠自由文本和 GUI“智能路由”创建 `agent` 任务，由 worker 中的轻量模型从四个已注册 Agent Profile 里选择；固定模式不调用路由模型。
- Capability Registry 统一索引代码、Agent Profile、Skill 和 Tool；目录查询只读取元数据，不加载具体实现。
- Model Gateway 统一承载 DeepSeek 兼容模型调用，Tavily 提供 `search.web`。
- 个人知识库按用户、内容 checksum 和 parser version 去重，重复上传不重复创建文档与 chunks。
- 提醒通知使用持久 outbox、原子发送租约和稳定幂等键；LangBot endpoint 需识别 payload/header 中的同一键以去重不确定重试。
- CalDAV 事件要求带时区时间，provider 会统一转换为 UTC RFC 5545 格式并拒绝无效时间区间。

方案文档见 `docs/个人Agent助手系统完整方案.md`，MVP 文档见 `docs/mvp/index.md`，V2 文档见 `docs/v2/index.md`，V3 文档见 `docs/v3/index.md`，V4 文档见 `docs/v4/index.md`，V5 文档与运维入口见 `docs/v5/index.md`。

## 启动方式

完整配置与初始化步骤见 `docs/mvp-startup-config.md`。

安装依赖：

```bash
uv sync
```

使用 Docker Compose 启动后端：

```bash
cp .env.example .env
# 先填写 LOCAL_API_TOKEN、CREDENTIAL_MASTER_KEY 和需要启用的 provider 配置
docker compose up --build -d
```

Compose 的 `migrate` 一次性服务会在 API、worker 和 Beat 启动前执行 `alembic upgrade head`。API 默认只映射到 `127.0.0.1:8000`。已有数据库中的逾期 pending 任务可能在 worker 启动后被补偿，恢复旧数据后应先检查任务状态再启动 worker。

本地分别启动 API、worker 与单实例 Beat：

```bash
uv run uvicorn --app-dir apps/api assistant_api.main:app --reload
PYTHONPATH=apps/api:. uv run celery -A assistant_api.worker:celery_app worker --loglevel=INFO
PYTHONPATH=apps/api:. uv run celery -A assistant_api.worker:celery_app beat --loglevel=INFO
```

启动 PySide6 桌面小窗口：

```bash
uv run assistant-desktop
```

桌面端不会自动启动后端。使用前必须完成数据库迁移，启动 API、Redis、PostgreSQL 和 `celery-worker`，并在数据库中准备用户。GUI 的 API 地址和用户 ID 保存在 `QSettings`，本机 API token 存入系统 keyring；账号密码、Token 和浏览器 storage state 不回显。

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 如何配置

`.env.example` 只包含占位值。本地可复制为 `.env`；`.env`、Token、Cookie、API Key 和私有 URL 不提交仓库。

主要配置：

- `LOCAL_API_AUTH_REQUIRED`、`LOCAL_API_TOKEN`：本机受保护 API 的 Bearer token 边界；Compose 默认要求配置。
- `CREDENTIAL_MASTER_KEY`：账号连接密文主密钥，至少 32 个字符；缺失时真实账号能力 fail-closed，不回退明文。
- `DATABASE_URL`：PostgreSQL asyncpg URL，同时供 LangGraph 官方 PostgreSQL checkpoint saver 使用；默认 Agent worker 不对其他数据库伪回退。
- `REDIS_URL`：Celery broker 与 result backend。
- `LANGBOT_WEBHOOK_SECRET`：`POST /api/webhooks/langbot` 的请求校验密钥。
- `LANGBOT_API_BASE_URL`、`LANGBOT_API_KEY`、`LANGBOT_SEND_TIMEOUT_SECONDS`：LangBot 结果回推配置。
- `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、模型别名与网关超时/重试：内部 Model Gateway 配置。
- `TAVILY_BASE_URL`、`TAVILY_API_KEY`、超时与结果上限：`search.web` 配置。
- `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`：可选 Langfuse v4 配置；public/secret key 必须同时存在才启用，未配置或部分配置时使用零网络 No-op。
- `RUNNING_TASK_TIMEOUT_SECONDS`：超时 `running` 任务失败阈值。
- `PENDING_TASK_COMPENSATION_DELAY_SECONDS`：逾期 `pending` 任务补偿阈值。
- `SCHEDULER_MAINTENANCE_INTERVAL_SECONDS`：单实例 Celery Beat 的维护投递周期。
- `MANAGED_SKILLS_ROOT`：托管 Skill 的可写根目录，本地默认 `var/skills`；Compose 容器固定使用持久卷中的 `/app/data/skills`。
- `MANAGED_PROMPTS_ROOT`、`SKILL_PACKAGES_ROOT`：受治理 Prompt 和待审批本地 Skill ZIP 的根目录；后者只接受已校验、无脚本的本地包。
- `ARTIFACTS_ROOT`：按 task 隔离的 EML/ICS/Office 文件根目录。
- `KNOWLEDGE_ROOT`：显式导入文件、索引元数据和用户隔离知识内容的受管根目录。
- `BROWSER_STATE_ROOT`、`BROWSER_ENABLED`：加密浏览器状态根和受限交互开关；需显式安装 Chromium，不读取宿主默认浏览器 profile。
- `SANDBOX_*`：Docker 隔离 Shell 开关、workspace、镜像 allowlist 与超时；默认关闭且不回退宿主执行。
- `SUBAGENT_*`：子 Agent 总数、并发和超时硬上限。
- `MEM0_CONFIG_PATH`、`MEM0_SEARCH_LIMIT`：可选 Mem0/pgvector 本地配置与语义检索上限；无配置使用 SQL。
- `QUALITY_JUDGE_*`：稳定采样、策略版本和低分阈值；采样率默认 0。

真实 LangBot 联调还需要创建平台绑定：`platform = langbot`，`platform_user_id = <adapter>:<sender_id>`。默认占位配置不会在服务启动时连接 LangBot、DeepSeek、Tavily、Langfuse 或 MCP Server。

## 项目目录介绍

```text
assistant/
├── apps/
│   ├── api/assistant_api/       # FastAPI、LangBot、任务与 Celery 入口
│   ├── desktop/assistant_desktop/ # PySide6 GUI
│   └── scheduler/               # V2-04 周期维护编排
├── packages/
│   ├── agent_harness/           # Agent 模型协议、规划与有界 LangGraph 执行边界
│   ├── capabilities/            # V3 统一能力目录与懒解析
│   ├── integrations/            # 加密账号解析、SMTP、CalDAV 与浏览器会话 provider
│   ├── knowledge/               # 文件校验、解析、分块、索引与用户隔离检索
│   ├── model_gateway/           # 模型适配与脱敏
│   ├── notifications/           # 提醒状态机、通知 outbox、重试与投递 adapter
│   ├── observability/           # 框架无关的 Trace/Score 协议与 No-op
│   ├── tools/                   # Agent-facing 工具描述、精确审批和安全 handler
│   ├── memory/                  # SQL 生命周期、Mem0 语义适配与上下文合并
│   ├── quality/                 # Judge 采样、分数、指标和阈值策略
│   └── evaluation/              # 离线评测
├── prompts/skills/              # 只读内置 Skills
├── var/skills/                  # 本地托管 Skills（运行时生成，不提交）
├── migrations/                  # Alembic 迁移
├── openspec/                    # 当前规范与变更归档
├── scripts/ops/                 # Compose smoke、provider smoke、soak、备份与空库恢复
├── docs/                        # MVP、V2、V3、V4、V5 文档与运维手册
├── tests/
│   ├── acceptance/              # 确定性用户行为与安全边界
│   └── integration/             # 本地协议服务器和隔离 PostgreSQL/Redis/Celery 栈
├── Dockerfile
├── Dockerfile.ops
└── docker-compose.yml
```

仓库不保留空目录表达未来规划：无实现的目录直接删除；职责相同才合并；需要保留的 Python 包必须包含真实模块或 `__init__.py`。运行时目录统一放在已忽略的 `var/`，不会提交备份、Cookie、知识文件或报告。

## 核心功能

### 消息与桌面入口

- `POST /api/webhooks/langbot` 校验 `x-langbot-secret`，归一化消息，将已知斜杠命令映射到固定类型、将无斜杠自由文本映射到 `agent`，校验用户绑定，按 `platform + message_id` 去重并轻量投递任务；未知斜杠命令仍拒绝。
- Result Dispatcher 保存 LangBot 的 `adapter`、`conversation_id`、`conversation_type`，对 `success`、`failed`、`cancelled` 和 `waiting_approval` 结果进行幂等回推。
- PySide6 GUI 默认提供“智能路由”，同时保留六类固定任务，支持最近任务、结果查看、三类待审批请求、批准/拒绝、Skill 管理与系统托盘常驻。
- `GET /app` 和旧直连消息路由不再提供；后端 API 是 LangBot 与 GUI 的内部边界，不作为网页产品入口。

### Agent、Skills 与工具路径

- 自由文本路径为：`agent` 任务 → 轻量 Model Router → Registry 中启用且有白名单映射的 Agent Profile → 原有 AgentHarness；模型不能直接选择 Tool、Skill、代码能力或任意 handler。
- 固定命令路径不经过 Model Router：`memory/status` 直接走本地服务，`plan/learn/daily/office` 直接走对应 Agent Profile。
- V2-02 提供 `v2.planner`、`v2.researcher`、`v2.daily`、`v2.office`，执行时按 Profile 加载指定 `prompts/skills/*/SKILL.md`；V3-07 将这些指令注入模型上下文，但不会自动启用工具。
- V2-03 在 V2-02 规划层上实现结构化 Plan、真实 LangGraph `StateGraph`、最大步数/超时限制和 ToolRegistry。未注册或不在 `allowed_tools` 的调用会被拒绝。
- V3-06 将受信任内置工具归一化为严格 `ToolDescriptor`，通过不可变 `ToolCatalogSnapshot` revision、确定性候选选择和有限工具预算生成本轮计划；LangGraph 只构造计划内 Function Calling Schema，ToolRegistry 再校验 revision、版本、来源可用性、白名单和审批。
- V3-07 增加严格 AgentDecision 和真实 `model → tool → model` 循环。模型可生成最多五条展示计划并逐轮选择一个计划内工具；结果来自模型 final 决策，不再由固定模板渲染，展示计划不能扩大 ExecutionPlan 权限。
- 默认 worker 继续只通过内部 Model Gateway 调用模型并写 `model_logs`。生产图使用严格序列化的 `AsyncPostgresSaver`，以 task ID 关联 checkpoint；审批 interrupt 后按同一任务恢复，并由 ToolRegistry 二次校验批准记录。
- V3-08 用 task ID 关联可选的 `agent.task` 根 observation、模型 generation、LangGraph step 和工具调用。所有载荷先递归脱敏和裁剪；Langfuse 初始化、上报、flush 或 shutdown 失败不改变任务结果，数据库审计仍是权威记录。
- V4-00 让 `office` 也进入 Plan-Execute-Review；复杂 WorkPlan 可有界 fan-out 给无工具权限的子 Agent，主 Agent 可请求最多 3 个全量预授权的并行安全工具，候选答案仍必须 Review 后发布。
- `/learn` 通过 `search.web` 获取资料，`/daily` 通过 `search.web` 获取来源，`/office` 默认不执行搜索。
- 计划、工具和复核 gate 都会进入 `waiting_approval`；批准后从相同 task checkpoint 精确恢复，拒绝后任务取消。ToolRegistry 只接受精确 `tool` 类型批准，计划或复核批准不能授予工具权限。
- 当前动态快照接入 `search.web`、本地 EML/ICS/Office artifacts、`knowledge.search`，以及显式启用后的 `browser.read`/`browser.interact`/`browser.save_state` 和 `shell.exec`。只有任务所有者存在活动连接时才发布 `email.send` 与 `calendar.sync_event`。
- 邮件、日历和浏览器外部动作绑定 task、tool、connection、目标、内容摘要与参数指纹，必须经 L3 精确审批；连接存在本身不授予执行权限。
- 完整 MCP Gateway 尚未配置，MCP Server 默认不启用；未配置时零连接，新发现外部工具也不会自动获得权限。
- V4 已完成真实 Office 文件生成；V5 已实现邮件/日历接入与受限的结构化深度浏览，但仍拒绝任意脚本、selector、域名和无审批提交，真实账号兼容性以显式 smoke 结果为准。

### V5 现实行动与个人数据

- SMTP 使用 STARTTLS 或 TLS、超时和安全错误码真实发送；CalDAV 使用稳定 UID，重试不会重复创建同一事件。账号凭据加密存储，支持测试、禁用和撤销。
- 浏览器仅接受有界结构化动作，限制允许域名并阻止私网、保留地址、任意 selector、JavaScript 和下载；交互后的状态只有再次精确批准保存才会覆盖密文。
- 知识库只摄取用户显式授权的 txt、md、pdf、docx、xlsx、pptx，校验真实路径、符号链接、大小和 checksum；解析失败保留旧索引，检索严格按用户隔离并返回来源。
- 提醒通过持久化 outbox 投递桌面或已有 LangBot 目标，成功记录不会因 Beat、worker 或 Redis 重启而重复发送；失败状态和重试结果可在桌面查看。
- 确定性 localhost SMTP/CalDAV 协议测试和隔离 Compose 重启测试可在无私人凭据时运行。真实 provider 只由显式 `SMOKE_*` 配置触发，跳过项保持未验证。

### 运维与恢复

可复制的 Compose smoke、真实 provider smoke、soak、备份与空库恢复命令见 `docs/v5/01-operations-runbook.md`。恢复脚本要求目标 `public` schema 为空，先校验 SHA-256，再比对 Alembic 版本和核心表计数；不会覆盖非空数据库。

### V3 能力目录与扩展边界

- `packages/capabilities/` 使用统一元数据描述 `code`、`agent_profile`、`skill`、`tool` 四类能力，默认目录覆盖 `memory`、`status`、四类 Profile、四个内置 Skill 和 `search.web`。
- `GET /api/capabilities` 可按 `kind`、`enabled` 查询稳定排序的安全元数据；响应不包含 loader、实例、本地路径或外部服务配置。
- Registry 的 `list/get` 不加载实现；只有显式 `resolve` 才调用已注册 loader，并在当前 revision 内缓存。目录可见不等于工具获准执行，最终门禁仍由 ToolRegistry 与审批记录控制。
- V3-02 路由候选只包含 `profile.plan`、`profile.learn`、`profile.daily`、`profile.office`；模型输出必须通过严格 JSON、启用状态和执行映射校验，成功与失败都走脱敏审计边界。
- V3-03 将内置根 `prompts/skills/` 与可写托管根分离。托管 Skill 可由 GUI 按模板创建，或安装恰好包含 `manifest.json` 与 `SKILL.md` 的受限本地 ZIP；创建和安装后默认停用，启停、失败和卸载均写持久审计。
- `GET /api/skills` 与五类变更接口为 GUI 提供生命周期边界。服务端限制包和文件大小，拒绝路径穿越、额外文件、内置覆盖及未知操作者，并以临时目录加原子改名发布。
- 启用托管 Skill 只允许 Registry 在显式 `resolve` 时读取说明，不会自动加入 Model Router、不会安装依赖或脚本，也不会自动获得 Tool 权限。
- 扩展新能力时先选择类型：规则明确的操作写确定性代码，多步推理写 Agent Profile，可复用指令写 Skill，外部动作写 Tool；再定义稳定 capability ID、摘要、风险、审批需求和验收测试。
- 新 Profile 不会仅因被发现就自动参与模型路由，必须显式增加执行映射；新 Tool 必须进入 ToolRegistry 与审批策略；重依赖只能由受控 loader 在调用时加载。
- Skill 包契约、桌面路径和后续扩展边界见 `docs/v3/03-skill-lifecycle-gui.md`。
- V3-04 已移除退役执行集成及其环境变量、客户端和 worker 分支。任务创建的 `model_class` 只接受 `light`、`standard` 或空值；历史未知值会安全失败，不会静默改走其他执行路径。
- V3-05 只同步累计主规范与当前 runtime 的一致性，不改变代码行为；退役能力的负向回归要求和历史归档继续保留。
- V3-06 让 Capability Registry 从一个完整工具快照 revision 投影元数据；目录可见、工具启用、计划允许和最终执行是四个独立边界。系统不扫描或热加载任意 Python 插件，不以全目录工具注入作为兜底。
- V3-07 参考 FinchBot 的 Agent 循环、动态上下文和 checkpoint 机制，但没有迁入其文件工作区、自修改、shell、后台任务或 MCP 自配置能力。详见 `docs/v3/07-agent-core-runtime.md`。
- V3-08 使用项目自有 Observability 协议隔离 Langfuse v4 SDK；默认 No-op，完整配置后才启用。Langfuse 负责运行 Trace、实验和可选评分，pytest 继续负责确定性安全与发布硬门禁。详见 `docs/v3/08-langfuse-observability-evaluation.md`。
- V3-09 增加结构化 WorkPlan、ReviewDecision、有界 retry/replan 和三类 Human-in-the-loop；模型输出始终从属于 Planning Layer 与 ToolRegistry 安全包络。详见 `docs/v3/09-plan-execute-review-hitl.md`。

### 记忆、监控与演进

- `/memory` 支持记住、查看与软删除，SQL 保留用户隔离的审计与生命周期；可选 Mem0 以当前输入做有界语义检索并与 SQL preference 去重合并，失败回退 SQL。
- `/status` 查询本人最近任务或指定任务，不泄露其他用户信息。
- V2-04 使用 `TaskService` 幂等创建周期任务，由单实例 Celery Beat 投递、既有 worker 执行。
- 记忆包含 `access_count`、重要性、过期和归档元数据；行为服务可把建议转为 managed Prompt/Skill proposal，但在精确 `change` 审批前不会自动修改任何文件，批准后才能原子应用并可回滚，且不能修改代码、依赖或工具权限。
- 维护流程包括超时 `running` 任务失败与逾期 `pending` 任务补偿。

### 评测与质量

- V2-05 评测与回归阶段已完成，数据集与基线继续保存在 `tests/evals/datasets/core_commands.json` 和 `tests/evals/baselines/v2-05.json`。V3-08 已移除 Deepeval；确定性关键词、禁词、长度、安全和基线规则现在由普通 Python/pytest 执行。
- 本地或 CI 可运行 `uv run python scripts/run_evaluation.py` 获取机器可读 JSON 报告；失败、缺失基线或回归返回非零退出码。该命令不读取 Langfuse 配置、不调用外部模型、不发送遥测。
- `packages.evaluation.run_langfuse_experiment` 只提供可注入的实验边界，调用方必须传入真实 task callable；静态 golden `actual_output` 不代表真实 Agent 质量。离线评测和 Langfuse 都不替代功能、安全、集成测试及人工发布检查。
- V4-00 提供默认关闭的 Gateway LLM Judge，对成功 Agent 输出做稳定 hash 抽样，将三维分数写入 Langfuse Score 和 Prometheus 指标；远端 Dashboard、Evaluator Rule 与外部告警按 `docs/v4/00-personal-agent-capability-completion.md` 在部署时显式配置。

## 验证

```bash
uv run pytest
uv run pytest --cov
uv run ruff check .
uv run mypy .
uv lock --check
uv run python -m scripts.ops.compose_smoke
uv run python -m scripts.ops.provider_smoke
```

项目遵循 OpenSpec + ATDD：每个 phase 先同步验收标准，再实现和验证，完成后同步主规范并归档变更。
