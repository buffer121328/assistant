# V12-08 目录渐进式演进

## 阶段目标

让项目目录贴近生产级 Agent 平台边界，减少 `backend/agent` 与 `backend/domain` 对 runtime、tools、memory、model_gateway、应用编排服务等实现职责的直接承载。

## 已完成范围

已完成八步迁移：

1. RAG 从 facade 演进为物理主实现包，并删除旧 `backend/knowledge` 兼容包。
2. runtime/tools/memory/model_gateway 提升为 `backend/` 顶层实现包。
3. 原 `backend/application/` 按业务域拆散：任务归 `backend/tasks/`，会话归 `backend/session/`，用户记忆归 `backend/memory/user_memory/`，账号与通知归 `backend/integrations/`。
4. 可复用状态/审批策略曾迁入 `backend/policies/`，并删除拆分后的聚合 shim。
5. 小顶层目录继续收敛：策略与脱敏沉入 `backend/domain/policies/`，能力注册沉入 `backend/agent/capabilities.py`，可观测抽象沉入 `backend/infrastructure/observability.py`，配置示例沉入 `backend/resources/config/`。
6. `backend/runtime/runner.py` 保留为公共 facade，主 harness、执行边界、默认 executor、事件安全和类型定义拆入 `backend/runtime/runner_*.py`。
7. `backend/tools/builtin/schedule/` 是调度内置工具唯一实现包，承载调度服务、ToolSpec/Descriptor、payload sanitizer 与时间/cron helper。
8. `backend/memory/user_memory/` 承载记忆 CRUD/治理、`/memory` 命令执行、语义索引同步和异常类型。

```text
backend/rag/__init__.py       # 对外稳定 RAG contract
backend/rag/service.py        # KnowledgeService、ingest/search/delete 主实现
backend/rag/extractors.py     # 文档解析实现与 parser 常量
backend/rag/citations.py      # citation token/context/validator
backend/runtime/              # Agent runner facade、runner_*、LangGraph executor、预算、loop、子 Agent
backend/tools/core/           # approval/catalog/registry
backend/tools/builtin/        # search/knowledge/memory/task/workspace/schedule 等内置工具
backend/tools/providers/      # 外部工具 provider 适配
backend/memory/               # 短期/长期记忆、检索、候选、合并、索引 outbox
backend/model_gateway/               # 模型网关、provider、模型池、fallback、streaming helpers
backend/tasks/                # 任务生命周期、TaskEvent、命令、状态、回推
backend/session/              # 会话、上下文压缩和 conversation memory blocks
backend/domain/models/        # SQLAlchemy 实体、状态枚举
backend/domain/policies/      # 状态转换、审批 request、外部动作审批绑定、脱敏
backend/agent/capabilities.py # 能力注册与内置 skill metadata 读取
backend/infrastructure/       # 配置、数据库、认证、日志、观测、仓储
```

知识 API、`knowledge.search` tool、worker runtime 和评测器已改为从 `rag` 导入；旧 `knowledge` 包已删除。旧 `backend/agent/core`、`backend/agent/tool_management`、`backend/agent/memory` 已删除，first-party import 改为 `runtime`、`tools`、`memory`、`model_gateway`。应用编排已按业务域导入 `tasks.*`、`session.*`、`memory.user_memory`、`integrations.*`，旧 `application` 与更早的 `domain.services`、`domain.task_lifecycle`、`domain.task_events`、`domain.conversations` 等物理模块已删除。

## 当前真实目录

```text
backend/
├── app/                    # FastAPI 入口、API schemas、routers、dependencies
├── tasks/                  # task lifecycle/events/commands/status/dispatch
├── session/                # conversations and short-term context memory
├── channels/               # desktop/langbot channel adapters
├── domain/                 # ORM entities、状态枚举、领域策略边界
├── agent/                  # planning/modeling/review/governance/skill/prompt
├── runtime/                # runner facade、runner_* modules、langgraph_* modules、loop/budget/subagents
├── tools/                  # core/builtin/providers/sandbox
├── memory/                 # working set/retrieval/semantic/consolidation/outbox
├── model_gateway/          # model gateway/providers/pools/fallback/streaming
├── features/               # plan/learn/daily/office task definitions
├── evaluation/             # 离线评测器
├── infrastructure/         # config/database/auth/repositories/observability
├── integrations/           # accounts/credentials/external providers
├── rag/                    # RAG/knowledge 主实现包
├── migrations/             # Alembic migrations
├── resources/              # prompts/config examples/skillpacks
└── workers/                # Celery app、runtime、monitoring、heartbeat
```

`backend/tools/builtin/schedule/` 当前承载 schedule service、descriptor/spec、payload 与 time helper，旧 `schedule_tools.py` 已删除。`backend/memory/user_memory/` 当前承载 service、commands、semantic sync 与 errors。

本批没有创建空的 `evals/deploy/configs` 顶层目录；只移动已有真实职责。`backend/common`、`backend/policies`、`backend/capabilities`、`backend/observability` 和 `backend/config` 已删除。

## 分步迁移原则

1. 先加 facade 或聚合导出，再按边界物理迁移实现。
2. 每次迁移都要有可回归的布局/行为测试。
3. 新路径必须承载唯一实现；旧路径最多短期做薄兼容 shim。
4. 如果用户明确要求整体大迁移并删除旧包，则迁移后必须全仓检查旧 import 无遗漏，再删除旧物理模块。
5. README 只记录真实已完成的新路径。

## 中期方向（未实现）

```text
backend/domain/policies/ # auth、tool_access、approvals、risk、budget（部分未迁移）
backend/rag/          # ingestion、chunking、retrieval、reranking、citations
backend/runtime/      # graph、checkpoint、interrupt、budget、recovery
backend/tools/        # core、builtin、providers、sandbox、validators
backend/model_gateway/       # gateway、providers、routing、fallback、cost
backend/memory/       # short_term、long_term、policies、cleanup
```

顶层 `evals/`、`deploy/`、`configs/` 仍是可选方向，只有真实职责迁移时才创建。

## 验收标准

- [x] 初始 facade 迁移有 OpenSpec change 与阶段文档：`v12-rag-observability-directory`。
- [x] 物理包迁移有 OpenSpec change：`v12-rag-physical-package-migration`。
- [x] README 与本文档记录真实 `backend/rag` 主实现包，并确认旧 `backend/knowledge` 已删除。
- [x] RAG 实现职责只保留在 `backend/rag`；旧 `backend/knowledge` 兼容包已删除。
- [x] runtime/tools/memory/model_gateway 已提升到 `backend/` 顶层，旧实现包已删除，first-party import 无旧路径遗漏。
- [x] 原 `backend/application/` 已按业务域拆散到 `tasks/`、`session/`、`memory/` 和 `integrations/`。
- [x] `backend/domain/` 仅保留模型与纯策略规则，不再承载依赖仓储/session 的编排服务。
- [x] `backend/domain/policies/` 承载任务状态转换、可回推状态、审批 request normalization、外部工具审批绑定和通用脱敏。
- [x] `backend/agent/capabilities.py` 承载能力注册与发现，旧 `backend/capabilities` 已删除。
- [x] `backend/infrastructure/observability.py` 承载 Observability 协议、No-op 实现和 Langfuse 适配，旧 `backend/observability` 已删除。
- [x] `backend/runtime/langgraph_executor/` 已扁平为 `backend/runtime/langgraph_*.py`，公共导入入口保持 `runtime.langgraph_executor`。
- [x] `backend/runtime/runner.py` 已拆为 `backend/runtime/runner_*.py`，公共导入入口保持 `runtime.runner`，`agent` re-export 改为惰性解析以避免循环 import。
- [x] `backend/tools/builtin/schedule_tools.py` 已拆为 `backend/tools/builtin/schedule/` 子包，旧入口已删除。
- [x] `backend/application/memory_service.py` 已拆迁为 `backend/memory/user_memory/` 包。

## 下一步建议

后续目录治理可继续处理其他剩余长文件；更重的策略拆分应等 auth/tool access/budget 有更多稳定职责后再做。
