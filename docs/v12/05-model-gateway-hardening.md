# V12-05 模型网关可靠性增强

## 阶段目标

把当前 Model Gateway 从“统一适配和基础 failover”增强为具备健康检查、冷却、熔断、限流、fallback 和成本统计的本地可靠模型入口。

## 当前问题

当前项目已有：

- GatewayRequest / GatewayResult。
- 模型分类路由。
- 模型池和 WeightedLeastLoadBalancer。
- provider 适配。
- token 和 latency 日志字段。
- 敏感文本脱敏。

仍缺：

- 健康检查。
- 冷却窗口。
- 熔断。
- RPM/TPM 限流。
- 结构化输出失败率统计。
- 单任务预算联动。
- 成本统计。

## 范围

- 模型节点健康状态。
- provider fallback。
- 请求超时和 retry 策略标准化。
- 本地 RPM/TPM 限流。
- token 和估算成本统计。
- ModelLog 字段和诊断展示。

## 不做事项

- 不接入商业账单系统。
- 不做多租户配额。
- 不强制切换模型供应商。
- 不依赖个人桌面订阅作为服务端推理池。

## 验收标准

- [x] 主模型节点失败后自动尝试备用节点。
- [x] 连续失败的节点进入 cooldown，不再立即重试。
- [x] 超过 RPM/TPM 本地限制时拒绝或排队。
- [x] 每次模型调用记录 provider、model、latency、input_tokens、output_tokens、status、error_code。
- [x] 预算不足时不调用高成本模型。
- [x] fallback 发生时写入 TaskEvent 或 ModelLog。
- [x] 测试覆盖 provider 429、5xx、timeout、无可用节点、fallback 成功。

## 建议设计

模型节点状态：

```text
healthy
unhealthy
cooldown
circuit_open
```

调用流程：

```text
select pool
  ↓
filter healthy nodes
  ↓
check RPM/TPM/budget
  ↓
call provider with timeout
  ↓
record usage/latency/status
  ↓
update health metrics
  ↓
fallback or return
```


## 完成状态（2026-07-21）

- ModelNode/NodeMetrics 增加本地健康、连续失败、cooldown、RPM/TPM 和 token 成本元数据。
- PooledModelGateway 在 provider 429/5xx/timeout 等失败后 fallback 到同池可用节点，并通过 diagnostic sink 记录 from_node、to_node、error_code。
- 达到连续失败阈值的节点进入 cooldown，窗口内不会被 rank 选中。
- 本地 RPM/TPM 超限节点会在 provider 调用前被跳过；无可用节点时返回 ModelGatewayError。
- GatewayResult/build_response_summary 暴露 estimated_cost 与 diagnostics。
- 本阶段不接入商业账单系统、不做多租户配额、不强制切换模型供应商。
