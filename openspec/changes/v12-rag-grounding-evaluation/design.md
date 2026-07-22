## Context

知识搜索已经返回 non-path source id 和人类可读 citation，但回答层还无法区分“引用了已检索来源”和“编造了不存在的引用”。静态 fixture 能验证治理字段存在，却不能发现真实分块、排序、no-answer 或 instruction-risk 标记的回归。

## Decisions

1. `citation_token` 直接包裹稳定 source id，避免通过 source label 反查。
2. formatter 只构造明确的 untrusted data envelope，不改变原始文档文本。
3. validator 只验证引用引用关系：已知、未知、缺失；不声称支持 entailment/factuality。
4. abstention 文本允许无 citation，避免强迫无答案回复伪造来源。
5. evaluator 使用真实 KnowledgeService、SQLAlchemy model 和 chunking 实现，但全部运行于临时目录和 SQLite。
6. 第一版指标为 mean recall@k、abstention accuracy、instruction-risk accuracy；数据集固定正常、无答案、冲突来源、恶意指令和长文档五类。

## Risks / Trade-offs

- 引用存在不代表结论正确 → 文档明确限制，后续增加 claim/evidence validator。
- instruction-risk 为关键字启发式 → 只作为诊断指标，不影响权限决策。
- 测试数据规模小 → 先固定真实执行基线，后续再扩充真实脱敏语料。

## Migration Plan

1. 增加 citation/真实检索 ATDD。
2. 实现 citation formatter 与 reference validator。
3. 实现真实 RAG evaluator 和数据集。
4. 接入本地治理报告。
5. 更新 README/docs 并运行全量质量检查。
