# V12-08 目录渐进式演进

## 阶段目标

在不一次性大搬家的前提下，让项目目录逐步贴近生产级 Agent 平台边界，减少 `backend/agent` 和实现包被调用方直接耦合的问题。

## 本批范围（2026-07-21）

本批只迁移一个边界：RAG facade。

```text
backend/rag/__init__.py     # 对外稳定 RAG contract
backend/knowledge/          # 当前 ingestion/retrieval 实现，兼容保留
```

知识 API 和 `knowledge.search` tool 已改为从 `rag` facade 导入；旧 `knowledge` 包保持兼容，避免一次性修改所有历史测试和调用方。

## 当前真实目录

```text
backend/
├── agent/                  # runtime/planning/review/tool/memory 等现有实现
├── app/                    # FastAPI 入口与 API
├── domain/                 # 持久化领域模型与服务
├── evaluation/             # 离线评测器
├── knowledge/              # 当前 RAG 实现包
├── rag/                    # V12 新增稳定 facade
├── model_gateway/          # 模型网关
└── observability/          # 可观测抽象
```

本批没有创建空的 `runtime/tools/models/policies/evals` 顶层目录，也没有搬迁 `backend/agent` 内部文件。

## 分步迁移原则

1. 先加 facade，不直接搬内部实现。
2. 每次只迁移一个边界。
3. 保持旧 import 兼容一个阶段。
4. 迁移前写 acceptance test。
5. README 只记录真实已完成的新路径。
6. 下一阶段若开始物理移动，必须删除长期重复职责或明确 deprecation 时间点。

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

- [x] 本批迁移有 OpenSpec change 与阶段文档：`v12-rag-observability-directory`。
- [x] 全量 pytest 与 coverage 本地通过（503 passed、11 skipped，coverage 81.90%）。
- [x] README 与本文档记录真实 `backend/rag` facade 和兼容边界。
- [x] 没有复制 RAG 实现；`backend/rag` 只导出 `backend/knowledge` 的稳定 contract。

## 下一步建议

下一批优先选择 `backend/runtime` 或 `backend/tools` 中的一个，不应并行大搬家。迁移前先列出 import graph、稳定 public API、兼容周期和删除旧路径的条件。
