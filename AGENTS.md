# AGENTS.md

## 项目定位

`assistant` 是一个可长期演进、可控、可审计的 Agent 助手系统，当前提供后端任务运行能力和本地桌面工作台，并向企业统一管理的 Work Agent 演进。

产品目标不是暴露 Skill、MCP、Plugin、Prompt 或模型供应商等技术概念，而是让用户围绕对话、任务、文件、产物、审批和能力完成工作；组织、权限、知识、工具和高风险动作由受治理的控制面统一约束。

## 文档分层

- `AGENTS.md`：Agent 行为入口，只保留项目定位、工具原则、禁止事项和文档索引。
- `rules/`：开发、测试、安全、目录、Git 和兼容清理等必须遵守的强约束。
- `docs/knowledge.md`：只记录无法从代码或 CodeGraph 稳定推导的业务、产品和领域知识。

目录结构、文件位置、符号定义、调用链和影响范围不在文档中维护，以 CodeGraph 和当前源码为准。

## 工具使用原则

- 仓库存在 `.codegraph/` 时，理解现有结构、符号、调用链和影响范围必须先使用 CodeGraph：优先 `codegraph_explore`，不可用时使用 `codegraph explore`。
- 仓库未建立 CodeGraph 索引时，直接以当前源码、测试和 `rg` 为依据，不从历史文档推断代码事实。
- 查询运行状态、数据库或外部资源时，优先使用当前可用的只读工具或 MCP；不可用时说明限制，只采用任务所需的最小权限替代方案。
- 会改变用户可见行为、API、Agent 行为、配置、部署或运维结果的变更，优先走 OpenSpec，明确目标、范围、验收场景和任务后再实现。
- OpenSpec 实现完成并通过验证后，先等待用户确认；确认后自动归档 change，并仅将本阶段相关变更创建为本地 commit。默认不 push、不创建 PR、不合并分支。
- 查业务语境先读 `docs/knowledge.md`；
- 如果 `uv` 因缓存权限或 sandbox 写入限制失败，在后端目录使用本地缓存：`cd backend && UV_CACHE_DIR=.uv-cache uv run ...` 或 `cd backend && UV_CACHE_DIR=.uv-cache uv sync`。
- 桌面客户端（Electron）连调的是远程服务器后端：本地不启动 assistant 后端、数据库或 Docker 栈，而是通过 SSH 隧道把远程 `127.0.0.1:18080` 映射到本地端口（`ssh -N -L 18081:127.0.0.1:18080 ubuntu@43.139.236.58`），客户端 `apiBaseUrl` 指向该本地端口。本地 `18080` 可能被其他项目占用，起隧道前先确认端口空闲；严禁把客户端指向本机其他项目的服务来冒充连通性验证。

## 禁止事项

- 不要编造不存在的路径、配置、环境变量、外部服务地址或执行结果。
- 不要提交、输出或写入真实密钥、Token、Cookie、API Key、私有 URL、认证头或隐藏推理。
- 不要回滚、覆盖或清理用户及其他工具产生的无关改动。
- 不要手写 `uv.lock`；依赖变更必须通过 `uv` 生成。
- 不要在未明确进入对应阶段前引入重型依赖、外部服务或新的运行时。
- 不要把可选能力默认塞入核心运行路径。
- 不要让前端、脚本或测试绕过后端审批、权限、租户/owner 隔离和工具治理策略。
- 不要根据 `reference/` 中的旧路径、旧接口或旧完成状态直接修改代码。

## 规则与知识索引

- [规则索引](rules/README.md)
- [开发流程](rules/development.md)
- [后端约束](rules/backend.md)
- [桌面端约束](rules/frontend.md)
- [目录与放置规则](rules/directory.md)
- [测试规则](rules/testing.md)
- [兼容代码清理](rules/compatibility.md)
- [Git 规范](rules/git.md)
- [远程服务器与部署](rules/server.md)
- [业务、产品与领域知识](docs/knowledge.md)
- [历史参考资料](reference/README.md)
