# V12 生产级 Agent 系统自查与分阶段推进索引

日期：2026-07-21
定位：本地单用户 Agent 助手系统的生产化补强路线。
说明：本阶段**不推进 CI/CD**，不把 GitHub Actions 或远端发布流水线作为目标；只保留本地可重复验证命令和本机运行质量门禁。

## 总体判断

当前项目已经具备本地个人 Agent 助手的核心闭环：FastAPI 后端、Electron 桌面端、LangBot 入口、Celery Worker/Beat、PostgreSQL/Redis、LangGraph Runtime、ToolRegistry、审批、审计、记忆、知识库、调度和测试基础。

但按照《生产级 Agent 系统完整标准》，它还不是完整企业级 Agent 平台。后续 V12 不追求一次性堆齐所有企业级基础设施，而是按“本地生产级优先”的顺序补短板：先稳住工具边界、预算、任务恢复和本地可观测，再逐步增强模型网关、RAG、评测和目录边界。

## V12 分阶段推进

| 阶段 | 文档 | 目标 | 优先级 | 是否改运行行为 |
|---|---|---|---|---|
| V12-00 | `00-scope-and-baseline.md` | 固定 V12 范围、现状基线、验收原则和不做事项 | P0 | 否 |
| V12-01 | `01-local-quality-and-config-isolation.md` | 修复本地测试受 `.env` 污染的问题，建立无 CI 的本地质量门禁 | P0 | 小 |
| V12-02 | `02-tool-schema-and-permission-hardening.md` | 强化 ToolRegistry：参数强校验、风险等级、审批、审计、幂等元数据 | P0 | 是 |
| V12-03 | `03-agent-budget-and-loop-control.md` | 补齐 Agent 预算守卫：工具次数、token、deadline、stop_reason、无进展停止 | P0 | 是 |
| V12-04 | `04-durable-task-recovery.md` | 强化任务可恢复：worker kill/restart、步骤状态、失败恢复、dead-letter | P1 | 是 |
| V12-05 | `05-model-gateway-hardening.md` | 增强模型网关：健康检查、冷却、熔断、fallback、限流、成本统计 | P1 | 是 |
| V12-06 | `06-rag-memory-governance.md` | 已完成轻量切片：source/citation、no-answer、删除、untrusted 边界与治理 fixture；向量/rerank 待后续 | P1 | 是 |
| V12-07 | `07-observability-and-evaluation-gates.md` | 已完成轻量切片：task trace、diagnostics 聚合、治理数据集与本地 JSON 报告 | P1 | 是 |
| V12-08 | `08-directory-evolution.md` | 已完成首个 facade：`backend/rag`；其余目录仅保留中期方向 | P2 | 中 |

## 推荐推进顺序

```text
V12-00 范围冻结
  ↓
V12-01 本地质量与配置隔离
  ↓
V12-02 Tool Schema 与权限硬化
  ↓
V12-03 Agent 预算与循环控制
  ↓
V12-04 持久任务恢复
  ↓
V12-05 模型网关可靠性
  ↓
V12-06 RAG 与记忆治理
  ↓
V12-07 可观测与评测门禁
  ↓
V12-08 目录渐进演进
```

## 第三批进展（V12-06/07/08）

2026-07-21 已完成一个轻量、无新运行依赖的切片：知识检索增加 source id、citation、no-answer 和 untrusted 标记；owner 可删除文档并立即清理 chunk；TaskResponse 使用 task id 作为 trace id，diagnostics 聚合 event/model/tool/approval/retrieval/error；新增可保存 JSON 报告的 V12 治理门禁；目录演进只引入 `backend/rag` facade，没有大规模搬迁。pgvector、rerank、query rewrite、完整答案验证、分布式 trace 和其他目录物理迁移仍未实现。

## V12 不做事项

- 不引入 CI/CD，不把 GitHub Actions 修复作为阶段目标。
- 不引入多用户、多租户、企业 SSO、ABAC。
- 不默认引入 Temporal、Kafka、Kubernetes、MinIO、Milvus、OpenSearch 等重型基础设施。
- 不默认启用 shell、浏览器自动化、MCP 外部工具或外部账号写操作。
- 不把未实现能力写成已上线能力。
- 不为了目录“像平台”而一次性大搬家。

## 下一步优先事项

第三批轻量切片完成后，后续优先级为：

1. 用真实检索结果生成 recall@k、MRR、abstention 和 injection 回归，而不是继续扩展静态 fixture。
2. 在回答生成链路接入 citation formatter/validator，再评估是否引入本地 embedding/pgvector。
3. 在 `backend/runtime` 与 `backend/tools` 中只选择一个作为下一次 facade/物理迁移边界。
