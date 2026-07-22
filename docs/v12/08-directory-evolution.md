# V12-08 目录渐进式演进

## 阶段目标

在不一次性大搬家的前提下，让项目目录逐步贴近生产级 Agent 平台边界，减少 `backend/agent` 和实现包被调用方直接耦合的问题。

## 本批范围（2026-07-21）

本批先迁移一个边界：RAG package。第一步已从 facade 演进为物理实现包：

```text
backend/rag/__init__.py     # 对外稳定 RAG contract
backend/rag/service.py      # KnowledgeService、ingest/search/delete 主实现
backend/rag/extractors.py   # 文档解析实现与 parser 常量
backend/rag/citations.py    # citation token/context/validator
backend/knowledge/          # legacy 兼容 shim，仅 re-export rag
```

知识 API、`knowledge.search` tool、worker runtime 和评测器已改为从 `rag` 导入；旧 `knowledge` 包保持兼容，避免破坏历史调用方。

## 当前真实目录

```text
backend/
├── agent/                  # runtime/planning/review/tool/memory 等现有实现
├── app/                    # FastAPI 入口与 API
├── domain/                 # 持久化领域模型与服务
├── evaluation/             # 离线评测器
├── rag/                    # RAG/knowledge 主实现包
├── knowledge/              # legacy 兼容导出 shim
├── model_gateway/          # 模型网关
└── observability/          # 可观测抽象
```

本批没有创建空的 `runtime/tools/models/policies/evals` 顶层目录，也没有搬迁 `backend/agent` 内部文件。

## 分步迁移原则

1. 先加 facade，再按边界物理迁移实现。
2. 每次只迁移一个边界。
3. 保持旧 import 兼容一个阶段。
4. 迁移前写 acceptance test。
5. README 只记录真实已完成的新路径。
6. 物理移动时必须避免长期重复职责；旧路径只做薄兼容 shim。

## 中期方向（未实现）

```text
backend/runtime/      # graph、checkpoint、interrupt、budget、recovery
backend/tools/        # registry、validators、permissions、mcp、builtin
backend/models/       # gateway、providers、routing、fallback、cost
backend/policies/     # auth、tool_access、approvals、risk、budget
backend/rag/          # ingestion、chunking、retrieval、reranking、citations
backend/memory/       # short_term、long_term、policies、cleanup
```

顶层 `evals/`、`deploy/`、`configs/` 仍是可选方向，只有真实职责迁移时才创建。

## 验收标准

- [x] 初始 facade 迁移有 OpenSpec change 与阶段文档：`v12-rag-observability-directory`。
- [x] 物理包迁移有 OpenSpec change：`v12-rag-physical-package-migration`。
- [x] README 与本文档记录真实 `backend/rag` 主实现包和 `backend/knowledge` 兼容边界。
- [x] RAG 实现职责只保留在 `backend/rag`；`backend/knowledge` 仅 re-export 兼容导入。

## 下一步建议

下一批优先选择 `backend/runtime` 或 `backend/tools` 中的一个，不应并行大搬家。迁移前先列出 import graph、稳定 public API、兼容周期和删除旧路径的条件。`backend/knowledge` 的删除应等历史调用方迁移完成后单独立项。
