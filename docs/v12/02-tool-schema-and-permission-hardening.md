# V12-02 Tool Schema 与权限硬化

## 阶段目标

把 ToolRegistry 从“有 schema 描述”升级为“执行前强制 schema 校验、权限校验、审批校验、审计记录”的工具网关。

## 当前问题

当前 `ToolSpec` 已有：

- `name`
- `description`
- `risk_level`
- `handler`
- `input_schema`
- `version`
- `source_id`
- `parallel_safe`

但统一执行入口未形成强制 JSON Schema 参数校验。模型虽然能看到工具 schema，但程序不能相信模型一定按 schema 输出。

## 范围

- ToolRegistry 单工具执行校验。
- ToolRegistry batch 执行校验。
- 工具风险等级与审批策略补强。
- 工具调用审计增强。
- 工具元数据为后续幂等、timeout、dry-run、补偿预留字段。

## 不做事项

- 不启用未登记 MCP server。
- 不默认开放 shell、浏览器、邮件、日历等高风险工具。
- 不实现完整企业 ABAC。
- 不接入外部策略中心。

## 验收标准

- [x] 工具参数不符合 `input_schema` 时，ToolRegistry 拒绝执行。
- [x] schema 校验失败写入 ToolLog，状态为 failed，错误信息脱敏。
- [x] batch 工具中任一参数非法时，不执行对应 handler。
- [x] L3/L4 高风险工具没有审批不能执行。
- [x] 工具版本或 snapshot 不一致时拒绝执行。
- [x] 工具输出被统一裁剪和脱敏。
- [x] 新增测试覆盖非法参数、额外字段、缺必填字段、审批缺失、snapshot stale。

## 建议设计

扩展或补充 `ToolSpec`：

```text
risk_level: L0 | L1 | L2 | L3 | L4
requires_approval: bool
timeout_seconds: float
max_retries: int
idempotent: bool
supports_dry_run: bool
compensation_tool: str | None
required_permissions: tuple[str, ...]
```

执行流程建议：

```text
resolve tool
  ↓
source/version/snapshot check
  ↓
allowed_tools check
  ↓
JSON Schema validation
  ↓
risk + approval check
  ↓
timeout/retry wrapper
  ↓
handler
  ↓
output sanitize + ToolLog
```

## 推荐测试

- `tests/unit/test_tool_registry_schema_validation.py`
- `tests/acceptance/test_v12_tool_hardening.py`

关键样例：

- 缺少 required 字段。
- 多出 additionalProperties。
- 字段类型错误。
- L3 工具无审批。
- 审批 subject 不匹配。
- batch 中包含非 parallel_safe 工具。


## 完成状态（2026-07-21）

- ToolRegistry 使用 `jsonschema` 在注册时验证 schema，在单次与 batch 执行时于 handler 前验证 arguments。
- schema、source、allowlist、snapshot/version 和高风险审批失败均通过 Registry ToolLog 写入有界脱敏信息。
- batch 在调度 handler 前完成全量预检；任一调用非法时整批不启动 handler。
- ToolRiskLevel 已扩展为 L0-L4；L3/L4 或 `requires_approval=True` 的工具必须具备匹配审批。
- ToolSpec 已增加 timeout、retry、idempotent、dry-run、compensation 和 required permissions 元数据；本阶段只声明契约，不实现完整重试、补偿或权限后端。
- Registry 持久化的输入、输出和错误统一限制为 4000 字符并脱敏，handler 返回对象不裁剪。
