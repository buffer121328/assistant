## Why

V12-06/07/08 需要把现有知识库、记忆检索、TaskEvent/ToolLog/ModelLog 和离线评测基础串成一个可追踪、可删除、可诊断、可本地门禁的最小闭环。当前知识搜索没有稳定 source id/引用和文档删除 API，任务详情没有统一诊断聚合入口，目录仍由调用方直接依赖 `knowledge` 实现包。

## What Changes

- 知识搜索结果增加稳定 source id、citation、untrusted trust boundary 和 instruction-like 风险标记，并显式返回 no-answer 状态。
- 增加 owner-scoped 文档删除；删除后清理 chunk，保留删除审计，并允许相同内容后续重新导入。
- 任务响应使用 task id 作为本地 trace id；新增 diagnostics API 聚合事件、模型、工具、审批、记忆检索来源和错误摘要。
- 新增 V12 RAG/Agent 治理确定性评测数据和本地报告脚本。
- 新增 `backend/rag` facade，调用方渐进迁移，`backend/knowledge` 暂保留一个兼容阶段。
- 更新 README 与 docs/v12，只声明本批真实完成的轻量边界。

## Non-goals

- 不引入 pgvector、Milvus、OpenSearch、reranker 或 query rewrite。
- 不实现完整答案事实核验或通用 Prompt Injection 分类器。
- 不引入 Prometheus/Grafana、CI/CD 或云端评测平台。
- 不一次性迁移 runtime/tools/models/policies 全部目录。

## Capabilities

### New Capabilities

- `rag-memory-governance`: 本地知识 source/citation、删除、no-answer 和不可信上下文边界。
- `local-observability-gates`: task 关联诊断与可保存的本地治理评测报告。

### Modified Capabilities

- `project-layout`: 通过 `backend/rag` facade 启动单边界渐进迁移。
- `evaluation-regression`: 增加 V12 RAG、轨迹、质量和安全治理数据集。

## Impact

主要影响 `backend/knowledge`、`backend/rag`、知识与任务 API、`backend/evaluation`、`scripts`、`tests/evals`、V12 文档与 README；不新增运行依赖和数据库迁移。
