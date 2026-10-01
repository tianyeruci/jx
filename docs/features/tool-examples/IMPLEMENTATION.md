# 朋友圈与日常写作可配置案例目录 — Implementation Spec

- Feature ID: `tool-examples`
- Status: APPROVED
- Planner: GPT-5.6 Sol
- Implementer: GPT-5.6 Luna
- Architecture: `docs/architecture/PROFILE.md`

## 1. Goal / Non-goals Reference

实现后端可配置案例目录，复用现有 FastAPI 路由、Pydantic 校验、异步 SQLAlchemy `AdminSetting` 和 Streamlit 测试 UI；案例卡片直接提供完整 `request` 查看；案例目录本身不改变 Dify DSL。本轮 EV-05/EV-06 证明必要时允许指定工具 App 最小兼容修正，保留前发布版本并复验，公开协议与 Start 变量保持兼容。

## 2. Repository Evidence

现有工具入口位于 `ai_management/api/routers/tools.py`，管理入口位于 `ai_management/api/routers/admin_management.py`，输入契约位于 `ai_management/schemas.py`，测试 UI 位于 `ai_management/test_ui.py`；`AdminSetting.value` 已支持 JSON 配置。

## 3. Design Decision

新增 `ai_management/services/tool_examples.py` 作为案例目录的唯一业务归属：定义默认值、kind 校验、JSON 读写、排序、CRUD 与恢复缺失默认项；路由只负责 HTTP/权限/响应映射。案例结构只保存 `id/title/description/request/enabled/sort_order`，其中 `request` 直接复用既有工具输入字段。

### Credible alternatives rejected

不新增数据库模型和迁移，不创建通用 repository 或额外服务层；不把案例内容写进 DSL。若用户界面和管理界面都需要格式化案例，仅复用稳定的 schema/响应类型，不抽象与本功能无关的组件。

## 4. Modules and Ownership

| Path/module | Ownership |
|---|---|
| `ai_management/services/tool_examples.py` | 默认值、存储与目录业务逻辑 |
| `ai_management/schemas.py` | 案例请求/响应校验模型 |
| `ai_management/api/routers/tools.py` | 普通用户读取接口 |
| `ai_management/api/routers/admin_management.py` | 管理员案例 CRUD/恢复接口 |
| `ai_management/test_ui.py` | 案例展示、点击立即生成、管理维护 UI |
| `tests/test_tool_examples.py` | 案例服务与 HTTP 契约测试 |
| `tests/test_app_routing.py`, `tests/test_v2_hardening.py`, `tests/test_cleaning_release_contract.py` | 路由、既有输入契约与接口文档冻结摘要回归 |
| `docs/小程序后端与 AI 管理服务接口对接交互版本.md`, `docs/小程序后端与AI管理服务接口对接标准.md`, `docs/API与SSE说明.md`, `docs/功能与接口索引.md`, `docs/README.md`, `docs/Dify创建导入绑定与契约测试.md`, `docs/architecture/PROFILE.md`, `AI管理服务当前状态与后续测试交接.md` | 接口标准、索引、Dify 运行时说明、架构和交接同步 |

## 5. Dependency Direction

### Allowed

`api.routers` → `services`/`schemas`/数据库会话；`test_ui` → HTTP API；案例服务 → `AdminSetting` 与 schema/模型；测试 → 公共 HTTP/服务 seam。

### Forbidden

路由直接拼接 JSON 存储；案例服务依赖 Streamlit；为案例复制 Dify Workflow 或引入第二套鉴权；修改无关的历史/清洗模块。

## 6. Reuse Decisions

For every new shared abstraction, state its semantic owner and why sharing is stable.

本 Feature 不新增跨域共享抽象；案例 schema 与默认值仅服务于两个工具，由 `tool_examples` 统一持有，kind 分支保持简单明确。

## 7. Contract Impact

- Internal HTTP API / SDK: 新增内部用户 GET 与管理员案例 CRUD/恢复接口；接口受内部令牌边界保护，既有 `/run` 不变。
- Events/messages: None。
- Persistent schema/migration: 复用 `AdminSetting` JSON，无迁移。
- Backward compatibility: 旧客户端忽略新增接口即可继续手动调用 `/run`；无配置时返回内置默认。
- Security/privacy: 普通读取复用内部 Token/用户标识，管理写入复用内部 Token+Admin Token；校验不允许空/超长内容。
- Operational behavior: 配置写入需事务提交；案例目录配置无需重新导入/发布 DSL。仅在本轮 EV-05/EV-06 失败证据证明必要时，按批准范围兼容修正指定工具 App，保存前版本并复验。

Use `None` explicitly where no impact exists.

## 8. Vertical Implementation Slices

| Slice | Decision IDs | Acceptance IDs | Test seam | Test level | TDD? | If no, why | Modules/files | Focused validation |
|---|---|---|---|---|---|---|---|---|
| S1 | RD-001,RD-002 | AC-001,AC-003 | `tool_examples` service + HTTP TestClient | unit/integration | no | 现有异步 DB/API seam 足够，先实现后集中回归 | service, schemas, admin/tools routers, test_tool_examples | 默认值、CRUD、恢复、空配置、排序、校验 |
| S2 | RD-004 | AC-002,AC-005 | `tool_tab`/Streamlit API call seam | static/manual-compatible | no | Streamlit UI 无独立组件测试框架，保留现有 UI 回归检查 | test_ui.py | 卡片点击调用原 run、手动输入与空目录 |
| S3 | RD-001..RD-004 | AC-004,AC-006 | route/documentation contracts | integration/static | no | 使用现有路由和文档契约测试 | test_app_routing.py, v2 hardening, interface/index/status docs | auth、路由、旧接口兼容；案例目录本身不改 DSL，必要 App 兼容修正另按 EV-05/EV-06 同候选复验 |

A test seam must be agreed here before a TDD-dependent implementation begins.

## 9. Canonical Quality Commands

- Format: `uv run ruff format --check ai_management/services/tool_examples.py ai_management/schemas.py ai_management/api/routers/tools.py ai_management/api/routers/admin_management.py ai_management/test_ui.py tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py`
- Lint/static: `uv run ruff check ai_management/services/tool_examples.py ai_management/schemas.py ai_management/api/routers/tools.py ai_management/api/routers/admin_management.py ai_management/test_ui.py tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py`
- Typecheck/compile: `uv run python -m compileall -q ai_management tests/test_tool_examples.py`
- Build: None for the catalog (no frontend build). Any necessary authorized App compatibility repair is verified through the actual published App contract and EV-05/EV-06.
- Unit: `uv run python -m pytest -q tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py`
- Integration: `uv run python -m pytest -q tests --ignore=tests/test_project_agents_is_small_and_does_not_store_dynamic_state.py -k "not test_project_agents_is_small_and_does_not_store_dynamic_state"`
- E2E/system: Streamlit click/admin checks and actual moments/writing conversation/SSE verification on the existing ys service per the approved difference-acceptance plan. The production jx service is a read-only baseline; EV-05/EV-06 and the shared cumulative ¥5/Beijing daily ¥4 budget apply. This campaign does not add catalog features.

## 10. Rollout / Migration / Rollback

无数据库迁移。发布前先完成本地测试和接口文档；本轮 ys 更新使用 `docs/ys独立服务重部署运行手册.md`，在取得当次精确命令的 release gate 后执行。回滚保留更新前候选及匹配配置，案例配置保留在 `AdminSetting`，不做破坏性清理；必要工具 App 兼容发布先保留前版本；旧客户端仍可使用 `/run`。

## 11. Escalation Triggers During Luna Implementation

若发现需要新增 DSL Start 变量、改变既有鉴权/错误结构、引入迁移、修改真实小程序源码，停止依赖该决定的实现并回到实质合同门；若 ys 更新命令或目标环境无法从运行手册确定，先完成候选与证据准备，再集中给出具体命令和最小确认项。

## 12. Open Questions

None

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
