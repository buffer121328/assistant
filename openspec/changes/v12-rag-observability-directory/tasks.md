## 1. OpenSpec 与 ATDD

- [x] 1.1 固定 V12-06/07/08 目标、范围、验收和不做事项
- [x] 1.2 增加知识引用、删除、注入权限边界验收测试
- [x] 1.3 增加任务 diagnostics 与 RAG facade 验收测试
- [x] 1.4 增加本地治理评测报告测试

## 2. V12-06 RAG 与记忆治理

- [x] 2.1 知识结果增加 source id、citation、trust boundary、instruction risk
- [x] 2.2 搜索 API 增加显式 no-answer 状态
- [x] 2.3 增加 owner-scoped 文档删除并清理 chunk
- [x] 2.4 复用既有记忆 TTL、来源、置信度、删除与 retrieval trace

## 3. V12-07 本地可观测与评测门禁

- [x] 3.1 TaskResponse 暴露 task-id-based trace_id
- [x] 3.2 diagnostics 聚合 event/model/tool/approval/retrieval/error
- [x] 3.3 增加 RAG、轨迹、质量、安全治理 fixture evaluator
- [x] 3.4 增加保存本地 JSON 报告的治理门禁脚本

## 4. V12-08 目录渐进演进

- [x] 4.1 新增非空 `backend/rag` facade
- [x] 4.2 知识 API/tool 迁移到 facade，旧 `knowledge` 保持兼容
- [x] 4.3 README/docs 说明真实目录状态和后续边界

## 5. 质量验证

- [x] 5.1 定向 acceptance/eval 测试通过
- [x] 5.2 本批 Ruff 与 mypy 定向检查通过
- [x] 5.3 全量 pytest/coverage、Ruff、mypy 与 `uv lock --check` 通过
