# V12-08 目录渐进式演进

## 阶段目标

让项目目录贴近生产级 Agent 平台边界，减少 `backend/agent` 与 `backend/domain` 对 runtime、tools、memory、models、应用编排服务等实现职责的直接承载。

## 已完成范围

已完成三步迁移：

1. RAG 从 facade 演进为物理主实现包。
2. runtime/tools/memory/models 提升为 `backend/` 顶层实现包。
3. 应用编排服务迁入 `backend/application/`，`backend/domain/` 收敛为实体、状态与领域规则边界。

```text
backend/rag/__init__.py       # 对外稳定 RAG contract
backend/rag/service.py        # KnowledgeService、ingest/search/delete 主实现
backend/rag/extractors.py     # 文档解析实现与 parser 常量
backend/rag/citations.py      # citation token/context/validator
backend/knowledge/            # legacy 兼容 shim，仅 re-export rag
backend/runtime/              # Agent runner、LangGraph executor、预算、loop、子 Agent
backend/tools/core/           # approval/catalog/registry
backend/tools/builtin/        # search/knowledge/memory/task/workspace 等内置工具
backend/tools/providers/      # 外部工具 provider 适配
backend/memory/               # 短期/长期记忆、检索、候选、合并、索引 outbox
backend/models/               # 模型网关、provider、模型池、fallback、streaming helpers
backend/application/          # 任务生命周期、TaskEvent、会话、账号连接、记忆、状态、回推
backend/common/               # 跨包通用能力，如脱敏
backend/policies/             # 状态转换、审批 request、外部动作审批绑定
backend/domain/               # SQLAlchemy 实体、状态枚举和纯领域规则边界
```

知识 API、`knowledge.search` tool、worker runtime 和评测器已改为从 `rag` 导入；旧 `knowledge` 包保持兼容。旧 `backend/agent/core`、`backend/agent/tool_management`、`backend/agent/memory`、`backend/model_gateway` 已删除，first-party import 改为 `runtime`、`tools`、`memory`、`models`。应用编排导入改为 `application.*`，旧 `domain.services`、`domain.task_lifecycle`、`domain.task_events`、`domain.conversations` 等物理模块已删除。

## 当前真实目录

```text
backend/
├── app/                    # FastAPI 入口、API schemas、routers、dependencies
├── application/            # use-case 编排：task lifecycle/events、memory、conversation、dispatch
├── channels/               # desktop/langbot channel adapters
├── common/                 # 跨包通用工具，无业务编排
├── domain/                 # ORM entities、状态枚举、领域边界
├── policies/               # 可复用治理/策略规则
├── agent/                  # planning/modeling/review/governance/skill/prompt
├── runtime/                # runner/langgraph/loop/budget/subagents
├── tools/                  # core/builtin/providers/sandbox
├── memory/                 # working set/retrieval/semantic/consolidation/outbox
├── models/                 # model gateway/providers/pools/fallback/streaming
├── evaluation/             # 离线评测器
├── rag/                    # RAG/knowledge 主实现包
├── knowledge/              # legacy 兼容导出 shim
└── observability/          # 可观测抽象
```

本批没有创建空的 `policies/evals/deploy/configs` 顶层目录；只移动已有真实职责。

## 分步迁移原则

1. 先加 facade 或聚合导出，再按边界物理迁移实现。
2. 每次迁移都要有可回归的布局/行为测试。
3. 新路径必须承载唯一实现；旧路径最多短期做薄兼容 shim。
4. 如果用户明确要求整体大迁移并删除旧包，则迁移后必须全仓检查旧 import 无遗漏，再删除旧物理模块。
5. README 只记录真实已完成的新路径。

## 中期方向（未实现）

```text
backend/policies/     # auth、tool_access、approvals、risk、budget（未迁移）
backend/rag/          # ingestion、chunking、retrieval、reranking、citations
backend/runtime/      # graph、checkpoint、interrupt、budget、recovery
backend/tools/        # core、builtin、providers、sandbox、validators
backend/models/       # gateway、providers、routing、fallback、cost
backend/memory/       # short_term、long_term、policies、cleanup
```

顶层 `evals/`、`deploy/`、`configs/` 仍是可选方向，只有真实职责迁移时才创建。

## 验收标准

- [x] 初始 facade 迁移有 OpenSpec change 与阶段文档：`v12-rag-observability-directory`。
- [x] 物理包迁移有 OpenSpec change：`v12-rag-physical-package-migration`。
- [x] README 与本文档记录真实 `backend/rag` 主实现包和 `backend/knowledge` 兼容边界。
- [x] RAG 实现职责只保留在 `backend/rag`；`backend/knowledge` 仅 re-export 兼容导入。
- [x] runtime/tools/memory/models 已提升到 `backend/` 顶层，旧实现包已删除，first-party import 无旧路径遗漏。
- [x] `backend/application/` 承载任务生命周期、事件、会话、记忆、账号连接、状态与回推等应用编排职责。
- [x] `backend/domain/` 仅保留 `models.py` 与包声明，不再承载依赖仓储/session 的编排服务。
- [x] `backend/policies/` 承载任务状态转换、可回推状态、审批 request normalization 和外部工具审批绑定。

## 下一步建议

下一批若继续目录治理，优先评估 `backend/knowledge` 兼容 shim 是否可以删除；更重的策略拆分应等 auth/tool access/budget 有更多稳定职责后再做。
