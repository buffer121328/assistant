# V12-08 目录渐进式演进

## 阶段目标

让项目目录贴近生产级 Agent 平台边界，减少 `backend/agent` 对 runtime、tools、memory、models 等实现包的直接承载。

## 本批范围（2026-07-21）

当前已完成两步迁移：先把 RAG 从 facade 演进为物理实现包，再整体提升 runtime/tools/memory/models 为顶层实现包：

```text
backend/rag/__init__.py     # 对外稳定 RAG contract
backend/rag/service.py      # KnowledgeService、ingest/search/delete 主实现
backend/rag/extractors.py   # 文档解析实现与 parser 常量
backend/rag/citations.py    # citation token/context/validator
backend/knowledge/          # legacy 兼容 shim，仅 re-export rag
backend/runtime/            # Agent runner、LangGraph executor、预算、loop、子 Agent
backend/tools/              # ToolRegistry、工具治理、内置工具、sandbox provider
backend/memory/             # 短期/长期记忆、检索、候选、合并、索引 outbox
backend/models/             # 模型网关、provider、模型池、fallback、脱敏
```

知识 API、`knowledge.search` tool、worker runtime 和评测器已改为从 `rag` 导入；旧 `knowledge` 包保持兼容。旧 `backend/agent/core`、`backend/agent/tool_management`、`backend/agent/memory`、`backend/model_gateway` 已删除，first-party import 改为 `runtime`、`tools`、`memory`、`models`。

## 当前真实目录

```text
backend/
├── agent/                  # planning/modeling/review/governance/skill/prompt
├── runtime/                # runner/langgraph/loop/budget/subagents
├── tools/                  # registry/approval/catalog/builtin tools/sandbox
├── memory/                 # working set/retrieval/semantic/consolidation/outbox
├── models/                 # model gateway/providers/pools/fallback/redaction
├── app/                    # FastAPI 入口与 API
├── domain/                 # 持久化领域模型与服务
├── evaluation/             # 离线评测器
├── rag/                    # RAG/knowledge 主实现包
├── knowledge/              # legacy 兼容导出 shim
└── observability/          # 可观测抽象
```

本批没有创建空的 `policies/evals/deploy/configs` 顶层目录；只移动已有真实职责。

## 分步迁移原则

1. 先加 facade，再按边界物理迁移实现。
2. 每次只迁移一个边界。
3. 保持旧 import 兼容一个阶段。
4. 迁移前写 acceptance test。
5. README 只记录真实已完成的新路径。
6. 物理移动时必须避免长期重复职责；旧路径只做薄兼容 shim。

## 中期方向（未实现）

```text
backend/policies/     # auth、tool_access、approvals、risk、budget（未迁移）
backend/rag/          # ingestion、chunking、retrieval、reranking、citations
backend/runtime/      # graph、checkpoint、interrupt、budget、recovery
backend/tools/        # registry、validators、permissions、mcp、builtin
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

## 下一步建议

下一批若继续目录治理，优先评估 `backend/policies` 是否已有足够真实职责可抽离；`backend/knowledge` 的删除应等历史调用方迁移完成后单独立项。
