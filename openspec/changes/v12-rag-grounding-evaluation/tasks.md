## 1. OpenSpec 与 ATDD

- [x] 1.1 固定 grounding/真实检索目标、验收和不做事项
- [x] 1.2 增加 citation 缺失、未知、合法和 abstention 验收测试
- [x] 1.3 增加真实 KnowledgeService 检索评测测试

## 2. Citation grounding

- [x] 2.1 知识搜索结果增加 `citation_token`
- [x] 2.2 增加 untrusted retrieval context formatter
- [x] 2.3 增加 citation reference validator

## 3. 真实检索门禁

- [x] 3.1 增加真实导入/分块/检索 evaluator
- [x] 3.2 覆盖正常、no-answer、冲突、injection、长文档
- [x] 3.3 输出 recall@k、abstention、instruction-risk 指标
- [x] 3.4 接入 V12 governance JSON report

## 4. 文档与质量

- [x] 4.1 更新 README 与 V12-06/07
- [x] 4.2 OpenSpec strict validate
- [x] 4.3 全量 pytest/coverage、Ruff、mypy、uv lock 通过
