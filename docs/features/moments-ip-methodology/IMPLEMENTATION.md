# 朋友圈顾问 IP 方法论 Prompt 与案例增量 — Implementation Spec

- Feature ID: `moments-ip-methodology`
- Status: DRAFT
- Planner: GPT-5.6 Sol
- Implementer: GPT-5.6 Luna
- Architecture: `docs/architecture/PROFILE.md`

## 1. Goal / Non-goals Reference

实现范围是将已确认的方法论落到现有朋友圈顾问运行 Prompt/DSL，并向现有案例目录追加三条案例。保持 FastAPI 路由、SSE、Dify 输入字段、AdminSetting 存储、鉴权、现有 Dify App、现有 API Key、qwen-plus 和写作助手不变。明确不做知识库/RAG、category 字段、Dify 节点改造、数据库迁移或旧数据删除。

## 2. Repository Evidence

入口为 `dify/dsl/moments_advisor.yml` 的 Start→LLM→End Workflow；运行 Prompt 位于 LLM 节点。`ai_management/services/tool_examples.py` 维护 `DEFAULT_EXAMPLES`，运行时案例持久化在 `AdminSetting` 的 `tool_examples:moments` JSON；既有案例 CRUD 可用于 jx 增量写入。测试沿用现有工具案例测试、应用路由/SSE 契约和 DSL 检查。

## 3. Design Decision

在原有 LLM Prompt 的最小修改中加入五类内容的自动识别顺序、各类写作动作、素材真实性约束和发布前检查；保留已有输入变量和输出格式。针对“素材为空时不要补写细节”的规则，将约束同时放在 system Prompt 和 user 消息末尾，但不在 API 层拒绝 request-only 调用，避免破坏既有可选素材契约。默认案例常量只追加三个稳定 ID，现有三条原样保留。部署时更新现有 Dify App 的 Workflow，继续使用现有 qwen-plus、现有 API Key 和入口参数，jx 代码使用 `--keep-data` 发布。

### Credible alternatives rejected

- 不新增输入字段或 Dify 节点：方法论和素材真实性规则由 Prompt 完成；保持可选素材的既有 API/SSE 契约。
- 不导入知识库：老板文档是方法论，不是检索型事实资料。
- 不覆盖旧案例或调用 `restore-defaults`：违反旧数据保留要求且不可逆。
- 不新建 Dify App、Key、模型或插件：用户已明确使用现有 DSL、模型和 Key，原地更新现有 Workflow 是最小范围。

## 4. Modules and Ownership

| Module | Owner path | Change | Acceptance |
|---|---|---|---|
| Prompt/DSL | `dify/dsl/moments_advisor.yml`; `dify/prompts/朋友圈顾问提示词.md` | 更新朋友圈顾问方法论与约束；同步参考 Prompt。 | AC-001, AC-002 |
| Default examples | `ai_management/services/tool_examples.py` | 只追加反馈、专业干货、互动三条默认案例。 | AC-003 |
| Contract tests | `tests/test_tool_examples.py`; `tests/test_moments_run_contract.py` 及相关既有测试 | 固化旧案例不变、新案例存在、request-only/有素材调用和既有 SSE 事件序列。 | AC-002, AC-003, AC-004 |
| Release evidence/docs | `docs/features/moments-ip-methodology/**`；按实际结果更新交接文档 | 记录现有 App 发布、测试、清理和回滚证据，不写入密钥。 | AC-004, AC-005 |

## 5. Dependency Direction

### Allowed

现有 `tools` 路由调用 Dify 服务，案例服务读取 `AdminSetting`；Prompt/DSL 与案例默认值只依赖现有字段和服务边界。测试只依赖公开/内部既有接口，不新增跨模块调用。

### Forbidden

禁止新增数据库/Redis 直接写入、Dify 业务数据库修改、生产发布、旧数据删除、模糊清理和把 Key 写入代码、文档或报告。禁止借本次需求重构路由、案例存储或 Dify 服务抽象。

## 6. Reuse Decisions

For every new shared abstraction, state its semantic owner and why sharing is stable.

不创建新的共享抽象。方法论规则归 `moments_advisor` Prompt，案例归 `tool_examples` 默认目录；两者语义稳定且已有唯一所有者，保持重复最少的局部修改即可。

## 7. Contract Impact

- Public API / SDK: None; 现有请求字段、路由和响应/SSE 契约不变。
- Events/messages: None; 仍由现有 Dify/SSE 事件流返回。
- Persistent schema/migration: None; `AdminSetting` JSON 结构不变，只增量追加案例。
- Backward compatibility: 保留旧案例、旧 App/Key 和旧调用方式；旧案例指纹必须不变。
- Security/privacy: 现有 Key 仅由发布人员复用到 jx 测试环境，不进入仓库、日志、报告或回复。
- Operational behavior: 需更新现有 Dify Workflow、jx `--keep-data` 部署、测试页真实 SSE 回归及精确清理。

Use `None` explicitly where no impact exists.

## 8. Vertical Implementation Slices

| Slice | Decision IDs | Acceptance IDs | Test seam | Test level | TDD? | If no, why | Modules/files | Focused validation |
|---|---|---|---|---|---|---|---|---|
| S1 | RD-001, RD-002 | AC-001, AC-002 | DSL/P prompt static assertions + existing moments route contract | unit/contract | no | Prompt 文本和 DSL 结构在现有契约边界验证，强行 TDD 不增加可靠性 | `dify/dsl/moments_advisor.yml`; `dify/prompts/朋友圈顾问提示词.md` | DSL contract check; focused route/SSE tests |
| S2 | RD-003 | AC-003 | `DEFAULT_EXAMPLES` snapshot and AdminSetting examples service tests | unit | yes | 案例增量和旧数据指纹可用稳定单元断言锁定 | `ai_management/services/tool_examples.py`; `tests/test_tool_examples.py` | focused tool examples tests |
| S3 | RD-004, RD-005 | AC-004, AC-005 | existing Dify publish, jx health/parameters, test-page SSE, exact cleanup manifest | integration/system | no | 依赖外部 jx/Dify 环境，不能由本地单元测试替代 | release evidence and current handoff docs | release checklist; test page; post-cleanup fingerprints |

A test seam must be agreed here before a TDD-dependent implementation begins.

## 9. Canonical Quality Commands

- Format: `uv run ruff format --check ...` (canonical paths from `.codex/governance/project.json`)
- Lint/static: `uv run ruff check ...` (canonical paths from `.codex/governance/project.json`)
- Typecheck/compile: `uv run python -m compileall -q ai_management knowledge_cleaner/dify.py`
- Build: None for the local Prompt/case change; deployment script performs the image build.
- Unit: focused `tests/test_tool_examples.py` and canonical project unit command.
- Integration: existing routing/SSE/cleaning contract tests plus DSL contract check.
- E2E/system: jx `/ai-management-test/` with real SSE after isolated Dify publish and `--keep-data` deployment.

## 10. Rollout / Migration / Rollback

先保存本地 DSL、默认案例和 jx AdminSetting 案例快照，不把密钥写入证据。完成本地质量门禁后，在现有 Dify App 上更新并发布 Workflow，核对入口参数、qwen-plus 和现有 Key 不变；使用 canonical `jx_quick_deploy.sh --keep-data` 部署，验证容器 ready、`/parameters`、案例目录和测试页真实 SSE。任一失败均保留证据、恢复发布前 Workflow/镜像并重新从本地检查开始。最终只清理本轮 manifest 中能精确证明归属的测试数据，旧案例、现有 App/Key 和业务数据全部保留。

## 11. Escalation Triggers During Luna Implementation

若 Prompt/DSL 与现有 API/SSE 契约冲突、旧案例快照变化、现有 Dify App 发布无法核对、jx 部署影响数据卷、测试页 SSE 失败、或平台不支持精确清理，立即停止对应后续步骤并记录证据；不得通过扩大范围、模糊删除或修改旧数据绕过问题。

## 12. Open Questions

- None

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->

