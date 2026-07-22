## Context

现有知识库已经有 owner-scoped 导入、chunk 和关键词搜索；记忆系统已经有 TTL、来源、置信度、删除、混合检索与 retrieval trace；ToolRegistry 已有 schema/allowlist/approval 强制边界。本批复用这些能力，补齐知识引用、删除、任务诊断和本地评测报告，不扩大外部基础设施。

## Decisions

1. **task id 即本地 trace id。** 当前 ModelLog、ToolLog、TaskEvent、Approval、MemoryRetrievalTrace 都已有 task_id 关联；V12 本地单用户阶段不新增独立 trace 列。
2. **知识 source id 采用 `knowledge:{document_id}:chunk:{chunk_id}`。** citation 使用可读的 `source_label#chunk-{ordinal}`，不暴露磁盘路径。
3. **检索内容始终标记为 `untrusted_document`。** instruction-like 标记仅用于诊断，不替代 ToolRegistry allowlist、审批和权限校验。
4. **文档删除采用软删除文档 + 硬删除 chunk。** 保留导入审计和文档状态；重新上传同 checksum 时复用 deleted 文档记录，避免唯一约束冲突。
5. **diagnostics API 返回安全摘要而非完整原始输入。** 统一裁剪并调用既有 `sanitize_text`。
6. **评测门禁保持确定性、本地、可保存。** fixture evaluator 验证数据结构与 pass/fail，CLI 写入 `var/evals`；不把 LLM-as-a-Judge 作为唯一标准。
7. **目录只迁移 RAG 边界。** 新建 `backend/rag` facade，并把知识 API/tool 调用方切到 facade；旧包保留兼容，后续阶段再决定内部搬迁。

## Risks / Trade-offs

- instruction-like 检测是启发式 → 只做风险标记，真正安全边界仍由 registry/approval 执行。
- task id 不是分布式 trace id → 足够支撑当前本地单用户系统，跨服务 trace 留待后续。
- 软删除保留 source_path 元数据 → API 从不返回原始路径，删除时尽力移除受管文件。
- fixture 门禁不能替代真实模型回归 → 与 acceptance pytest、现有 memory eval 和人工证据并行使用。

## Migration Plan

1. 先增加 OpenSpec/ATDD 验收用例。
2. 扩展知识搜索/删除契约并切换到 `rag` facade。
3. 增加 task trace/diagnostics 聚合。
4. 增加治理评测 fixture、evaluator 和本地报告脚本。
5. 更新 README/docs 并运行定向与全量质量检查。
