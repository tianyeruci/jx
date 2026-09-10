# 三类 AI 顾问消息操作 — Implementation Spec

- Feature ID: `advisor-message-actions`
- Status: APPROVED
- Planner: GPT-5.6 Sol
- Implementer: GPT-5.6 Luna
- Architecture: `docs/architecture/PROFILE.md`

## 1. Goal / Non-goals Reference

实现 `SPEC.md` 的 FR-001 至 FR-006。商业顾问严格在既有 `BusinessMessage` 上替换；朋友圈/写作仅通过 Dify 会话映射操作远端消息。不得改变 ORM schema、创建消息版本、持久工具正文、引入 service/repository 层或改动用户保留的非 canonical DSL 文件。

## 2. Repository Evidence

- `conversations.py::prepare_business_chat` 已使用 `ai:conversation-lock:{conversation.id}`，创建 user/pending assistant，并通过 `_persist_business_result` 更新同一助手行。
- `context.py::build_context` 只支持一个排除 ID；商业重生成需要排除一组 user/assistant IDs。
- `summary.py::_summarize_sync` 当前直接从未摘要消息批处理，需在选择批次前移除最新完整二元组。
- `tools.py::_chat_stream` 已复用 `ToolConversation.dify_conversation_id` 和 kind/user 锁，但未提前 SSE task ID、未回传 Dify message ID、停止映射缺 marker 且有不安全的双 App fallback。
- `dify.py` 已有 chat/stop/feedback/delete client；需局部新增受限 `/messages` 读取。
- `MomentsInputs`/`WritingInputs` 都 `extra=forbid`，是远端 inputs 回读的白名单 seam。

## 3. Design Decision

### 3.1 Business strict replacement (RD-001, RD-003)

新增 `prepare_business_regeneration`（或等价的同模块局部入口）。在现有会话锁内查找目标助手、配对前一用户消息、最新性、归属和摘要 cursor；先把该二元组置为非上下文的 regeneration 状态并清空助手可见资料后 commit。以 `build_context(..., exclude_message_ids={user, assistant})` 构造 inputs，原用户内容只作为 `query`。复用原助手 ID 和通用 SSE/persist 逻辑，增加 `regeneration` prepared metadata，不新增数据库行。

最终持久化规则：completed 覆盖助手并恢复用户 completed；stopped 覆盖新片段并恢复用户 completed；failed 清空助手、将二元组标记 failed，且不进入 context/summary/profile。failed pair 的 content/message/task 均清空，但若已取得新的精确 Dify conversation ID，仍保留 cleanup-only `BusinessConversationSegment`，供精确删除初始及恢复远端会话；失败保留最新可重试资格；`can_regenerate` 只对可安全重生的最新助手为 true。错误终态携带原本地 message ID 和 `status=failed`。

### 3.2 Summary candidate selection (RD-003)

在 summary worker 取得未摘要消息后，先按顺序识别最后一个完整、`completed|stopped` 的 user+assistant 二元组并从本次批次移除；如果仅剩最新一组，安全 no-op。不得引入摘要回滚或画像回滚。

### 3.3 Tool remote operations (RD-001, RD-002, RD-004)

`DifyAppClient.list_messages` 只发送当前 `conversation_id` 和匿名 `user`，返回经基本结构验证的分页 data。tools router 的局部 helper 统一：验证 kind、取得 active owned `ToolConversation`、用 messages list 确认 ID 属于当前 Dify 会话，并选择最新可重生 message。反馈直接复用 `feedback`，不写本地内容。

重生成从 message 的 `query` 和 `inputs` 构造当前 `MomentsInputs`/`WritingInputs`；未知字段被 Pydantic 忽略前先从白名单字典过滤、缺失或非法字段转稳定 Dify response error。根据当前 row 与 style settings 重新注入 type、writing format、admin style，仍传原 conversation ID/user 到 `_chat_stream`。

### 3.4 Tool task/terminal lifecycle (RD-002)

`_chat_stream` 在首个 task ID 立即写 Redis mapping（entry/user/transport/conversation）并 SSE `task_started`。同时读取 Dify `message_id`。stop route 只认匹配当前匿名 user 的 mapping，先在 terminal claim 内写入 stopping in-flight marker，再调用对应 Chat API；仅远端成功写 stopped marker，不做应用轮询 fallback。流处理及 GeneratorExit 在同一 terminal claim 下读取停止结果，所有 terminal 路径统一清理 task/stopping/stopped keys；得到 Dify end 或已确认 stop 均发一个带 conversation/message/task/status 的 terminal event，失败发 `error(status=failed)`，成功/停止才发 `message_end`。

### 3.5 DSL/UI/docs (RD-005, RD-006)

只修改权威 `dify/dsl/moments_advisor.yml`、`dify/dsl/writing_advisor.yml` 及其 contract tests，让实际生成节点 Memory window 6；商业 DSL 仅加/更新 test，断言 Memory 未开。不要编辑用户保留的 `dify/*（当前版本）.yml` 或 `old` 文件。Streamlit 保存本轮 task/message/conversation 状态，失败丢 buffer、停止保留，操作按钮仅在所需 ID 存在时启用。

### Credible alternatives rejected

- 抽取泛化消息操作 service：商业持久替换与工具远端同会话重发的保证不同，抽象会掩盖差异且不复用稳定。
- 工具建本地正文/版本表：违背最小改动和 Dify 保留责任。
- 工具生成新会话或使用 `parent_message_id`：不能保留当前 Dify Memory，且 Service API 不支持所需分支语义。
- 对已摘要商业消息重建摘要/画像：无法可靠移除既有信息且超出本 Feature。

## 4. Modules and Ownership

| Module ID | Ownership paths | Acceptance IDs | Responsibility |
|---|---|---|---|
| business-regeneration | `ai_management/conversations.py`, `ai_management/context.py`, `ai_management/api/routers/business.py` | AC-001, AC-002 | 商业预检、严格上下文排除、同 ID 持久化、SSE/列表能力 |
| summary-safety | `ai_management/workers/tasks/summary.py` | AC-003 | 暂留最新完整二元组 |
| tool-message-actions | `ai_management/dify.py`, `ai_management/api/routers/tools.py`, `ai_management/schemas.py` | AC-001, AC-004, AC-005 | `/messages`、反馈、重生、task/stop SSE/Redis |
| entry-test-ui | `ai_management/api/routers/entry.py`, `ai_management/test_ui.py` | AC-001, AC-005, AC-007 | feature flags、测试页操作与 terminal 呈现 |
| message-action-tests | `tests/test_ai_management.py`, `tests/test_tool_conversations_contract.py`, `tests/test_app_routing.py`, `tests/test_dify_client.py`, `tests/test_moments_run_contract.py`, `tests/test_test_ui.py`, `tests/test_cleaning_release_contract.py`, `tests/test_advisor_message_action_docs.py` | AC-001, AC-002, AC-003, AC-004, AC-005, AC-007, AC-008 | 公共 seam、接口索引/冻结契约、结构性接口文档断言和回归覆盖 |
| dify-memory-contract | `dify/dsl/moments_advisor.yml`, `dify/dsl/writing_advisor.yml`, `tests/test_business_advisor_optimized_dsl.py`, `tests/test_business_advisor_unified_dsl.py` | AC-006 | Memory 静态合同与非回归 |
| contract-docs | `docs/API与SSE说明.md`, `docs/小程序后端与AI管理服务接口对接标准.md`, `docs/小程序后端与 AI 管理服务接口对接交互版本.md`, `docs/小程序后端与 AI 管理服务接口对接交互版本（修改后接口影响版）.md`, `docs/Dify创建导入绑定与契约测试.md`, `docs/测试与验收指南.md`, `docs/README.md`, `docs/功能与接口索引.md`, `AI管理服务当前状态与后续测试交接.md`, `docs/features/advisor-message-actions/**`, `docs/architecture/PROFILE.md` | AC-008 | 文档归属（非功能生命周期模块）；完成后同步语义、索引、脱敏证据和当前状态 |

## 5. Dependency Direction

### Allowed

- routers → existing conversations/context/tool-conversation/Dify client/SSE/cache/models.
- conversations → context/Dify/cache/models/style/privacy/SSE；summary worker → context/Dify/models/cache.
- Dify client → HTTP only；test UI → existing HTTP helpers only。
- DSL tests → YAML asset parsing；不读取 secrets。

### Forbidden

- router/UI → Dify business DB、直接远端数据清理、工具正文/反馈 ORM 持久化。
- `dify.py` → ORM、HTTP request authentication或业务权限决定。
- 新 generic `services/messages.py`、repository/DI/继承抽象。
- 任何路径 → 用户保留的 6 个非 canonical DSL 文件。

## 6. Reuse Decisions

- 复用 `DifyAppClient.feedback/stop_chat`、`SSEEmitter`、cache lock/JSON APIs、`tool_conversations.owned/current/reset`、`FeedbackRequest`、Pydantic inputs 和 `record_event`。
- `/messages` 读取只添加到现有 Dify client，因为其生命周期、错误映射、鉴权与已有 chat/feedback 同属 Dify App HTTP 边界。
- business 与 tool route 各保留局部 prepare/validation helper；它们的持久化和 Memory 保证不同，不能制造伪共享。

## 7. Contract Impact

- Public API / SDK: 内部 `/internal/v1` 增加三条 POST 路由；现有 stop path 保持，entry config features 增量。
- Events/messages: 工具新增早期 `task_started` 和 terminal metadata；商业 regeneration 复用现有 SSE 并增强 failed metadata。
- Persistent schema/migration: None。
- Backward compatibility: 正常请求与既有字段保持，新增字段可忽略；工具 ID 明确 opaque。
- Security/privacy: 每个远端 ID/action 均以当前 user/kind/conversation 校验；审计仅用哈希/ID/status。
- Operational behavior: Worker 延迟最新完整二元组一回合；工具 Memory 需线上原 App 最小发布；完整 wheel 及 backup release。

## 8. Vertical Implementation Slices

| Slice | Decision IDs | Acceptance IDs | Test seam | Test level | TDD? | If no, why | Modules/files | Focused validation |
|---|---|---|---|---|---|---|---|---|
| S1 商业替换 | RD-001, RD-003 | AC-001, AC-002 | Business route / `prepare_business_chat` stream with fake Dify | integration + unit | yes | — | business-regeneration, summary-safety tests | focused business pytest |
| S2 工具远端操作 | RD-001, RD-002, RD-004 | AC-001, AC-004, AC-005 | tools router + fake Dify client / cache | integration + unit | yes | — | tool-message-actions, tests | focused tools pytest |
| S3 摘要与能力/UI | RD-003, RD-005 | AC-003, AC-005, AC-007 | summary sync, entry response, Streamlit source tests | unit + integration | yes | — | summary-safety, entry-test-ui | focused summary/UI pytest |
| S4 DSL 与文档 | RD-006 | AC-006, AC-008 | YAML contract test / index checker | contract | no | 基于静态资产与现有 manifest | dify-memory-contract, contract-docs | DSL pytest + index check |

## 9. Canonical Quality Commands

- Format: repository `uv run ruff format --check …` command registered in `.codex/governance/project.json`.
- Lint/static: repository `uv run ruff check …` command registered in `.codex/governance/project.json`.
- Typecheck/compile: `uv run python -m compileall -q ai_management knowledge_cleaner/dify.py`.
- Build: no local build; full Docker/wheel build belongs to release validation.
- Unit: registered two `uv run python -m pytest -q tests …` commands plus focused Feature selectors.
- Integration: registered `uv run python -m pytest -q Other/event_rag_cleaner` and mock FastAPI/Dify tests.
- E2E/system: post-quality jx complete wheel/Dify/Streamlit/actual caller-host evidence, not a quality command.

## 10. Rollout / Migration / Rollback

No migration. Before Dify or jx mutation: save current App export/backup and validate a recoverable jx backup plus wheel SHA/image provenance/Alembic head. If tool Memory is already on, no Dify republish; if off, minimally enable current generation LLM `memory.window.enabled=true,size=6` in the bound original App, save and publish. Rollback uses verified jx backup/image and saved Dify export; never delete user data broadly.

## 11. Escalation Triggers During Luna Implementation

- Existing model schema lacks a status required for strict failed pair exclusion, or summary cursor lacks sufficient pair ordering evidence.
- Dify `/messages` returned shape/permissions conflict with documented current API, or message data cannot prove current conversation/user ownership.
- Tests reveal a conflict with ordinary business stop semantics or normal tool generation compatibility.
- Canonical DSL cannot express Memory window 6 without changing existing Start/Prompt/model/key mappings.
- Any production command, real Dify publish or jx release: stop after local quality/review and obtain exact Release Gate command approval.

> Ownership path note: the filesystem path for the interaction-version contract is `docs/小程序后端与 AI 管理服务接口对接交互版本.md`; URL-encoded `%20` remains valid only in Markdown URL targets, not in module ownership paths.

> Lifecycle note: `contract-docs` is an optional ownership module. It continues to bind shared documentation to this Feature and must be synchronized through `gov sync-docs`; it is not a production-code module, so Quality cannot advance it to `verified`.

## 12. Open Questions

None

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
