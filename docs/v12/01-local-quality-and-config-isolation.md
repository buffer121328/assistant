# V12-01 本地质量与配置隔离

## 阶段目标

建立无 CI 的本地质量门禁，确保本地验证不受真实 `.env`、私有 token 或个人配置影响。

## 当前问题

本次运行：

```bash
uv run pytest
```

结果：

```text
1 failed, 482 passed, 10 skipped
```

失败项：

```text
tests/acceptance/test_foundation.py::test_health_endpoint_returns_service_status
```

原因：`/health` 返回的 `service_name` 受本地 `.env` 中 `SERVICE_NAME=assistant` 影响，而测试期望 `assistant-api`。

## 范围

- 测试环境变量隔离。
- 本地验证命令文档化。
- `.env.example` 与 README 的本地运行说明保持真实。
- 不改 CI，不新增 GitHub Actions，不维护远端流水线。

## 不做事项

- 不处理 CI/CD。
- 不引入新测试框架。
- 不改变生产配置加载方式。
- 不删除用户本地 `.env`。

## 验收标准

- [x] `uv run pytest` 不因本地 `.env` 的 `SERVICE_NAME`、`DATABASE_URL`、`REDIS_URL` 等值而失败。
- [x] 测试明确使用默认测试配置或 monkeypatch 隔离环境变量。
- [x] `uv run ruff check .` 通过。
- [x] `uv run mypy .` 通过。
- [x] `uv lock --check` 或 `uv sync` 验证依赖状态。
- [x] README 说明本地验证命令和本地 `.env` 的边界。

## 建议实现

1. 在相关测试中清理 `SERVICE_NAME`：

```python
monkeypatch.delenv("SERVICE_NAME", raising=False)
```

2. 对创建 App 的测试优先传入显式 `Settings`，避免读取真实 `.env`。
3. 增加一个统一测试 fixture，用来屏蔽外部服务环境变量。
4. README 增加“本地验证不会读取真实密钥”的说明。

## 风险

- 如果测试直接读取 `.env`，后续任何本地配置变化都可能造成误报。
- 如果为了测试修改生产默认值，可能破坏真实本地启动体验；因此应优先隔离测试，而不是改生产配置。


## 完成状态（2026-07-21）

- `tests/acceptance/test_foundation.py` 创建测试 App 时显式使用 `_env_file=None` 的测试 `Settings`，不再读取仓库根个人 `.env`；生产 `load_settings()` 行为保持不变。
- README 已记录 pytest、coverage、Ruff、mypy、`uv lock --check` 和本地 `.env` 边界。
- 本批验证结果：`493 passed, 10 skipped`；coverage `81.29%`；Ruff、mypy 和锁文件检查通过。两项本地协议集成测试需要绑定 `127.0.0.1` 临时端口，因此在允许本地 socket 的环境执行。
