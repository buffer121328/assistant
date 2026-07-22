# V12-06 RAG 与记忆治理

## 阶段目标

把当前知识库和记忆能力从“基础可用”推进到“本地可信、可追踪、可删除、可评测”的状态。

## 本批范围（2026-07-21）

- 知识 chunk 返回稳定 `source_id`、可读 citation 和不可信上下文标记。
- 搜索 API 显式返回 `answerable`，空结果不伪造答案。
- 增加 owner-scoped 文档删除；删除后 chunk 不再参与检索。
- Prompt Injection 文本只作为不可信数据进入结果，不能扩大 ToolRegistry allowlist/审批权限。
- 复用现有记忆 TTL、来源、置信度、用户删除、冲突抑制和 retrieval trace。
- 增加 V12 RAG 治理确定性评测数据。

## 已有基础

知识库已有上传、大小限制、checksum 去重、分块、owner-scoped 查询和 import audit。记忆系统已有多类 memory、TTL/validity、source trust、confidence、candidate/feedback、冲突与 consolidation、用户 forget/delete、semantic index outbox 和 hybrid retrieval trace。

## 本批实现

### 知识来源与引用

`KnowledgeSearchResult` 当前返回：

- `source_id`: `knowledge:{document_id}:chunk:{chunk_id}`。
- `citation`: `{source_label}#chunk-{ordinal}`。
- `trust_boundary`: 固定为 `untrusted_document`。
- `instruction_risk`: 对 instruction-like 文本做启发式风险标记。

API 不返回受管文件的原始 `source_path`。`instruction_risk` 只用于诊断，不能替代 ToolRegistry 的 schema、allowlist、risk/approval 和权限边界。

### 删除与重新导入

`DELETE /api/knowledge/documents/{document_id}?user_id=...` 仅允许 owner 删除。删除会：

1. 清理该文档的 `knowledge_chunks`。
2. 将文档状态设为 `deleted` 且 `chunk_count=0`。
3. 写入 `ImportAudit(status="deleted")`。
4. 尽力移除 knowledge root 内的受管文件。
5. 允许相同 checksum 内容后续重新导入并复用 deleted 文档记录。

### No-answer

`GET /api/knowledge/search` 增加 `answerable`：有匹配 chunk 时为 `true`，无匹配时为 `false`。这只是检索层 abstention 信号，不等同于完整答案事实核验。

## 验收标准

- [x] 每条知识检索结果带 non-path `source_id`；记忆检索来源可通过 task diagnostics 看到 `memory:{memory_id}`。
- [x] 知识 API/tool 返回 citation，任务 diagnostics 可追踪记忆来源。
- [x] 删除文档后相关 chunk 不再被检索，并保留删除状态/审计。
- [x] 记忆支持用户 forget/delete；语义索引失败时已有 outbox 补偿路径。
- [x] 记忆写入已有来源、时间、置信度、source spans/reason code 等治理字段。
- [x] 文档 instruction-like 内容保持 `untrusted_document`，不能绕过 ToolRegistry allowlist/审批调用越权工具。
- [x] V12 RAG fixture 覆盖正常引用、no-answer、冲突来源、恶意指令、删除和超长文档边界。

## 本批不做

- 不引入 pgvector、Milvus 或 OpenSearch。
- 不实现 rerank、query rewrite 或完整答案验证器。
- 不把启发式 `instruction_risk` 描述为通用 Prompt Injection 防御。
- 不做多租户 ACL。

## 下一步

1. 在真实回答生成链路中加入 citation formatter/validator。
2. 增加可插拔 embedding，并在不改变 API 的前提下实现关键词 + 向量混合检索。
3. 用真实检索结果而非静态 fixture 生成 recall@k、MRR、abstention 和 injection 回归报告。

## 第二个轻量切片：Grounding 与真实检索评测

本阶段继续补齐“引用存在”与“引用可校验”之间的差距。

### Citation token

每条知识结果新增：

```text
citation_token = [knowledge:{document_id}:chunk:{chunk_id}]
```

该 token 直接来源于 `source_id`，用于回答中的机器可校验引用。人类可读的 `citation` 仍保留，例如 `notes.txt#chunk-0`。

### 不可信上下文包装

`rag.format_retrieval_context()` 会为检索结果增加统一 envelope，明确说明：

- 文档内容只作为数据；
- 不能作为 system/developer 指令；
- 不能授予权限；
- 不能要求或批准工具调用。

formatter 不删除原始内容，因此审计时仍能看到恶意文本；真正的工具权限仍由 ToolRegistry 控制。

### 引用引用校验

`rag.validate_citation_references()` 当前校验：

- 回答引用的 source id 是否属于本次检索结果；
- 是否引用了不存在的 source id；
- 有可用来源的实质性回答是否缺少 citation；
- 明确 no-answer/insufficient-evidence 的回复允许不带 citation。

该 validator 只验证引用关系，不验证自然语言结论是否被证据语义蕴含。

### 真实检索评测

新增 `rag_retrieval_v12_06.json`。评测器会在临时 SQLite 和 knowledge root 中真实执行：

```text
创建用户 → 写入文档 → KnowledgeService.ingest
→ 实际分块 → KnowledgeService.search → 计算指标
```

覆盖：正常检索、no-answer、冲突来源、恶意指令和长文档。当前指标为：

- mean recall@k；
- abstention accuracy；
- instruction-risk accuracy。

这比静态治理 fixture 更接近真实运行，但仍不是答案级 factuality/entailment 评测。
