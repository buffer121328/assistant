# AGENTS.md

## 适用范围

本文件适用于当前仓库根目录及其全部子目录。

## 项目上下文

- 项目方案文档在 `docs/个人Agent助手系统完整方案.md`。
- MVP 阶段开发文档入口在 `docs/mvp/index.md`。
- 当前项目是个人 Agent 助手系统的 Python 后端仓库。
- Python 版本由 `.python-version` 固定为 `3.12`。
- 依赖由 `uv` 管理，锁文件为 `uv.lock`。
- 当前依赖边界以 V1/MVP 后端为准：FastAPI、PostgreSQL、Redis、Celery、Dify API、Tavily、DeepSeek/模型网关相关客户端能力。
- V2/V3 依赖，例如 LangGraph、MinIO、pgvector 迁移、Office 文件生成、Next.js 管理后台等，只有在明确进入对应阶段时再引入。

## 工作规则

- 改动要小，方便审查。
- 动手前先说明计划和会改的文件。
- 执行命令前说明为什么执行。
- 不胡编路径、配置、环境变量或外部服务地址。
- 不泄露密钥、Token、Cookie、API Key、私有 URL 或其他敏感信息。
- 行为变化尽量补测试。
- 默认使用中文，表达简洁，可复制。
- 不要回滚用户或其他工具产生的无关改动。

## 开发范式

- 项目采用 OpenSpec + ATDD 的 phase-by-phase 开发范式。
- 阶段推进前，优先读取 `docs/mvp/index.md` 和对应阶段文档。
- 每个阶段开始前，先明确本阶段目标、范围、验收标准和不做事项。
- 行为变化先写验收标准，再补自动化测试，最后实现代码。
- 测试应覆盖用户可观察行为，优先写 API、服务层或端到端边界的验收测试。
- 如果仓库尚未初始化 OpenSpec 目录或规范文件，不要猜路径；先说明建议目录和文件，再执行初始化。
- 每个阶段完成时，必须同步更新 `README.md`。
- 阶段完成的最低要求：验收标准可追踪、相关测试通过、README 反映当前真实状态。

## README 维护

- `README.md` 需要长期维护。
- 每完成一个阶段，都要更新 README。
- README 至少包含：项目介绍、启动方式、如何配置、项目目录介绍、核心功能。
- README 只写当前已经存在或已明确规划的内容，不写虚假的启动命令、路径或配置。
- 如果当前阶段尚不能启动完整服务，需要在 README 中明确说明当前状态和下一阶段入口。

## uv 约定

- 添加运行依赖使用 `uv add <package>`。
- 添加开发依赖使用 `uv add --dev <package>`。
- 不手写 `uv.lock`。
- 修改依赖后运行 `uv lock --check` 或 `uv sync` 验证。
- 本地命令优先使用 `uv run ...`。

## 测试与质量

- 测试命令：`uv run pytest`。
- 覆盖率命令：`uv run pytest --cov`。
- Lint 命令：`uv run ruff check .`。
- 类型检查命令：`uv run mypy .`。
- 当前没有业务源码时，不强行新增空测试。

## 安全

- `.env` 和 `.env.*` 仅用于本地配置，不提交。
- 示例配置使用 `.env.example`，只能包含占位值。
- 日志、测试快照和文档中不得写入真实密钥。
