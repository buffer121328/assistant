## Why

V12 第一批已经让知识结果带 source id/citation，并提供静态治理 fixture，但 citation 还没有机器可校验格式，评测也没有真正运行 KnowledgeService 的导入、分块和检索链路。项目需要先补齐 grounding 基线，再决定是否引入向量检索。

## What Changes

- 每个知识 chunk 增加稳定 `citation_token`，格式为 `[knowledge:{document_id}:chunk:{chunk_id}]`。
- 新增不可信检索上下文 formatter，将外部内容与系统/权限/工具指令明确隔离。
- 新增 citation reference validator，识别缺失引用和未知 source id；明确不做语义蕴含判断。
- 新增真实 RAG evaluator：在临时 SQLite/knowledge root 中创建用户、导入文档、运行 KnowledgeService 搜索并统计 recall@k、abstention 和 instruction-risk accuracy。
- 将真实检索结果接入 `run_v12_governance_gate.py` 的本地 JSON 报告。

## Non-goals

- 不验证回答中的自然语言结论是否被引用内容语义蕴含。
- 不调用真实 LLM，不引入 embedding、pgvector、reranker 或 query rewrite。
- 不把启发式 instruction-risk 当作完整 Prompt Injection 检测器。

## Impact

影响 `backend/rag`、知识搜索 schema、`backend/evaluation`、V12 gate、acceptance/eval 测试和阶段文档；不新增运行依赖或数据库迁移。
