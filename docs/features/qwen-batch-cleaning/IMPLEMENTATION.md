# 千问 Batch/实时清洗一致性与商业顾问活动查询修复 — Implementation Spec

- Feature ID: `qwen-batch-cleaning`
- Status: USER-APPROVED SHARED CONTRACT UPDATE; GOVERNANCE REAPPROVAL PENDING
- Planner: GPT-5
- Implementer: exactly one GPT-5.6 Terra write owner
- Architecture: `docs/architecture/PROFILE.md` (L2)

## 1. Goal / Non-goals Reference

> **Current shared-path boundary (2026-09-13):** the active
> `cleaning-tolerance-methodology` and `recent-activity` contracts are current.
> Activity remains realtime-only at `50/200`; normal runtime keeps Batch false.
> A fail-closed Batch acceptance seam is permitted only for the four approved
> content strategies in the isolated test environment. No new review/fallback
> behavior is created, and DB 6/Dify test data is preserved.

在现有 FastAPI/Celery 模块化单体、Dify Chatflow 和 `general` 知识库边界内完成：

1. 活动 Chatflow 的既有混合意图识别、默认 50/最高 200 条、名称/时间/状态逐行展示和指定活动详情；本任务不改变 `recent_activity` 运行行为。
2. 最近活动固定 realtime；四个内容模型策略沿用服务器配置的 realtime/Batch 双模式。
3. realtime 与 Batch 共用 Prompt、Schema、校验、归一化、Metadata 投影和 Dify 发布路径，但认知分享、咨询会议总结、最近活动继续保持独立业务契约。
4. 四策略完成免费本地确定性 Batch/Realtime parity；仅咨询与知识星球在隔离门禁和预算内完成真实 provider 对照，生产阈值和默认关闭保持不变。
5. 保证所有 Dify 文档保留原始文件安全 stem 前缀，并在同知识库/同策略契约范围内按最终正文跨源、跨批次去重。
6. 模型结果未采用时保存脱敏原因；新链路不新增 `review_required`、不以源 Markdown 兜底，只有调用异常或零核心内容才按总上限完整重跑一次。

不新增公开 API、数据库迁移、查询期数据服务或新架构层。

## 2. Repository Evidence

- `dify/商业顾问（当前使用版本）.yml` 已扩展活动自然表达、独立状态路由、详情筛选和可配置列表上限。
- `ai_management/model_cleaning.py` 的 `choose_call_mode` 已提供开关与候选/Token 阈值选择 seam；需保留默认 realtime 和现有阈值。
- `ai_management/workers/tasks/model_cleaning.py` 提供 Batch 提交、轮询、结果守恒、修复和发布衔接；需验证终态停止轮询和竞态。
- `ai_management/cleaning.py`、`workers/tasks/cleaning.py`、`knowledge_cleaner/dify.py` 提供策略注册、Metadata 白名单、Dify 索引/回读和补偿发布。

## 3. Design Decision

- 采用确定性规则优先、严格枚举分类器兜底的活动混合意图；活动列表与详情在结果结构上分界。
- 清洗策略复用模型传输、校验、Metadata 和发布工程能力，但不复用认知、咨询会议总结、最近活动的业务结果 Schema。
- 最近活动固定 realtime；四个内容模型策略复用 realtime/Batch 双 transport。正常运行保持 Batch 关闭和既有生产阈值，隔离验收通过受 manifest、白名单、预算和正式健康保护的窄 seam 显式触发。
- realtime 与 Batch 通过 `build_model_request_body` 共用 `temperature=0.01`、`enable_thinking=false`、`seed=1234` 和 JSON object 输出；seed 已按百炼 OpenAI 兼容接口支持范围核对，不承诺字节级复现。
- 契约失败由 Worker 写入受控脱敏审核材料，保留响应指纹、受控模型结果、字段路径和错误摘要；不保存普通报告可见的原始文件名/正文，不产生新的 `review_required` 或源文兜底。独立可用单元继续处理，零核心内容或调用异常才允许一次完整重跑。
- 发布前在同知识库/策略/规格锁内按规范化最终正文指纹去重；重复条目只有在远端成果有效性验证后形成 `duplicate_noop`。Realtime 是不同 digest 时的固定 canonical 候选；两 transport 任一不合格或语义不等价时均不发布，同 digest 只发布 Realtime并验证 Batch noop，不同 digest 但语义等价只发布 Realtime并保留 Batch 脱敏证据。

## 4. Modules and Ownership

| Module | Owned paths | Acceptance |
|---|---|---|
| activity-chatflow | `dify/商业顾问（当前使用版本）.yml`, `dify/商业顾问_统一修复版_Dify-1.13.3.yml`, `tests/test_dify_activity_contract.py`, `tests/test_business_advisor_unified_dsl.py` | AC-001, AC-002, AC-003 |
| model-cleaning-policy | `ai_management/model_cleaning.py`, `ai_management/config.py` | AC-004, AC-005, AC-009 |
| model-cleaning-orchestration | `ai_management/workers/tasks/model_cleaning.py`, `ai_management/services/bailian_cleanup.py` | AC-004, AC-005, AC-006 |
| strategy-contracts | `ai_management/cleaning.py`, `ai_management/activity_cleaning.py`, `knowledge_cleaner/dify.py` | AC-005, AC-008, AC-009 |
| cleaning-contract-tests | `tests/test_model_cleaning_workflow.py`, `tests/test_large_scale_cleaning.py`, `tests/test_activity_cleaning.py`, `tests/test_release_acceptance_contract.py` | AC-004, AC-005, AC-006, AC-008, AC-009 |
| jx-acceptance-docs | `scripts/jx_*.sh`, `docs/features/qwen-batch-cleaning/**`, `docs/features/recent-activity/**`, `var/reports/**` | AC-007, AC-010 |

Shared `cleaning.py`/Dify changes must preserve existing strategy behavior and require regression coverage; do not add repository/service/DI layers.

## 5. Dependency Direction

Allowed: Chatflow → Dify retrieval output; strategy → existing cleaner/model request; Worker → existing DB/Celery/Redis/Dify seams; tests → isolated fakes and static DSL execution.

Forbidden: Chatflow → PostgreSQL/Redis; strategy → HTTP router/UI; direct Dify database edits; name/time-window deletion; secret or source正文 in logs/reports; re-enable course model paths.

## 6. Reuse Decisions

Reuse: `choose_call_mode`, existing Qwen client/request builder, Prompt version/fingerprint, Pydantic/business validators, `poll_batch_model_task`, Dify publication and exact manifest scripts. New shared code is permitted only when it owns transport-level parity and does not merge the three business contracts.

## 7. Contract Impact

- Public API/SSE: no new fields; existing strategy discovery and answer stream remain compatible.
- Dify environment `ACTIVITY_MAX_RESULTS` 的当前合同为默认 50、最高 200，并向 `activity_max_results` 输出受控数值；本任务只校正过期合同/测试，不改变 activity Code、DSL 或外部 SSE 语义。
- Persistent schema: none.
- Runtime: recent activity never schedules Batch; content strategies snapshot realtime/Batch mode at task creation. Batch acceptance defaults off and is valid only for the approved isolated environment and strategy allowlist.
- Realtime delivery lease: Worker delivery/retry counts remain distinct from per-document provider-call accounting. A current, unexpired lease token fences reservation, document writes and terminal completion; the current owner renews after rate limiting and immediately before reservation, while a lost-token exception bypasses repair/fallback/failure writes. Exact retry releases a terminal lease and relies on the durable `model_attempts`/reservation ledger for the two-call limit across HTTP, realtime, Batch and message redelivery; a new batch/file name cannot reset it.
- Security/data: only allowlisted public Metadata crosses to Dify; existing test DB 6, database, volumes and Dify documents remain unless a later exact cleanup authorization is recorded. No name/time-range or whole-store cleanup is allowed.
- Canonical parity: realtime and Batch each pass the same source/privacy/fact/structure/metadata/chunking/parent-child and publication-plan checks; shared publication paths are verified locally. Natural-language wording, provider/remote IDs, timestamps and completion order are not byte-equality requirements. Realtime is the fixed canonical candidate; a failed or semantically unequal pair publishes neither result and does not gain a third provider call. With different equivalent digests only Realtime is remotely indexed/read/retrieved; Batch remains a redacted unpublished comparison, while same digest may prove shared remote validity plus `duplicate_noop`.

## 8. Vertical Implementation Slices

| Slice | Decisions | Acceptance | Seam / level | TDD | Focus |
|---|---|---|---|---|---|
| S1 Activity intent/output | RD-001, RD-002 | AC-001, AC-002, AC-003 | Execute current Chatflow Code + static DSL contract seam | TDD yes | phrase matrix, negative precedence, current 50/200 cap, formatting, details; no runtime activity change |
| S2 Mode/request parity | RD-003, RD-004, RD-005, RD-006, RD-010 | AC-004, AC-005 | planner + shared request/validator/publisher fakes seam | TDD yes | recent realtime-only, four local content modes, two real comparisons, canonical and deterministic Metadata |
| S3 Batch lifecycle | RD-005, RD-006 | AC-004, AC-005, AC-006 | `poll_batch_model_task` with provider/task fakes seam | TDD yes | threshold, transient errors, terminal stop, duplicate/missing/bad output |
| S4 Naming/dedup/failure | RD-008, RD-009, RD-010 | AC-005, AC-008, AC-009 | naming/publish/failure service seams with DB/provider fakes | TDD yes | source stem, cross-source hash, duplicate_noop, canonical, no review/fallback, concurrent idempotency |
| S5 jx isolated acceptance | RD-007, RD-010 | AC-004, AC-005, AC-007, AC-010 | manifest + real jx/Dify/Bailian seam | TDD no | read-only preflight, two real transports, retained data, production-health evidence |

## 9. Canonical Quality Commands

- Format/lint/compile and canonical `gov quality` commands from `.codex/governance/project.json`.
- Focused activity/DSL tests and model workflow/large-scale tests.
- Real jx test keeps production Batch thresholds/default unchanged, uses the explicit isolated acceptance seam and existing budget fail-closed guard, and records consulting/zsxq provider status plus document/index/Metadata/parent-child/readback/retrieval evidence.
- Any failure is recorded first; after the test set is complete, fix all local issues and rerun from a new evidence batch. Do not claim success from partial or cancelled runs.

## 8. Rollout / Rollback

1. Complete local implementation and quality checks.
2. Read-only verify existing test DB/role, Redis DB 6, queues, volumes, images, Dataset, accumulated cost/reservations and formal service health; do not pre-clean existing test data.
3. Deploy or restore only the isolated immutable test release after governance, exact-command and budget gates. Keep normal Batch false; enable the acceptance seam only for the approved comparison and restore it to false afterward.
4. Keep the current authoritative business-advisor DSL unchanged and read-only; this cleaning acceptance does not import, publish or roll back DSL.
5. Keep all current test results. Rollback affects only the isolated project and never uses `down -v`, Dify DB mutation, whole Redis cleanup or production service restart.

## 9. Escalation Triggers

- Current Qwen model rejects a stability parameter; remove only that parameter and document evidence.
- Dify dynamic Metadata or `doc_metadata` is unavailable; stop only the current cleaning acceptance that depends on it. 历史 formal Chatflow switch/cleanup 不属于本次执行范围，不以此触发 DSL 发布、回退或清理。
- The isolated acceptance seam cannot produce a real Batch within budget/provider window; stop and report as external-blocked rather than changing production defaults or expanding the cost ceiling.
- Exact general cleanup ownership or jx release boundary cannot be proven.
- A public API, migration, security boundary, or architecture change becomes necessary.

## 10. Open Questions

None

> 下列 `gov:` 块是 2026-08-31 的机器生成历史快照；不得手改。当前可用性只由 feature record 和内容绑定治理命令裁定，本 Feature 的 shared-contract reapproval 仍 pending。

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
