# 千问 Batch/实时清洗一致性与商业顾问活动查询修复 — Implementation Spec

- Feature ID: `qwen-batch-cleaning`
- Status: APPROVED FOR IMPLEMENTATION
- Planner: GPT-5
- Implementer: exactly one `luna_implementer`
- Architecture: `docs/architecture/PROFILE.md` (L2)

## 1. Goal / Non-goals Reference

在现有 FastAPI/Celery 模块化单体、Dify Chatflow 和 `general` 知识库边界内完成：

1. 活动 Chatflow 混合意图识别、10 条默认上限、名称/时间/状态逐行展示和指定活动详情。
2. 最近活动固定 realtime；四个内容模型策略沿用服务器配置的 realtime/Batch 双模式。
3. realtime 与 Batch 共用 Prompt、Schema、校验、归一化、Metadata 投影和 Dify 发布路径，但认知分享、咨询会议总结、最近活动继续保持独立业务契约。
4. 在现有 100 候选/200000 Token 阈值下完成真实 jx Batch/实时对照，问题先集中记录，修复后再部署。
5. 保证所有 Dify 文档保留原始文件安全 stem 前缀，并在同知识库/同策略契约范围内按最终正文跨源、跨批次去重。
6. 模型结果未采用时保存服务端审核材料并进入 `review_required`，人工 approve 后才允许源 Markdown 兜底入库，exclude 永不入库。

不新增公开 API、查询期数据服务或新架构层。为解决已确认的 PublicationShard 长事务外并发风险，允许且仅允许新增一份可空租约字段迁移；除该迁移外不新增数据库迁移。

## 2. Repository Evidence

- `dify/商业顾问（当前使用版本）.yml` 已扩展活动自然表达、独立状态路由、详情筛选和可配置列表上限。
- `ai_management/model_cleaning.py` 的 `choose_call_mode` 已提供开关与候选/Token 阈值选择 seam；需保留默认 realtime 和现有阈值。
- `ai_management/workers/tasks/model_cleaning.py` 提供 Batch 提交、轮询、结果守恒、修复和发布衔接；需验证终态停止轮询和竞态。
- `ai_management/cleaning.py`、`workers/tasks/cleaning.py`、`knowledge_cleaner/dify.py` 提供策略注册、Metadata 白名单、Dify 索引/回读和补偿发布。

## 3. Design Decision

- 采用确定性规则优先、严格枚举分类器兜底的活动混合意图；活动列表与详情在结果结构上分界。
- 清洗策略复用模型传输、校验、Metadata 和发布工程能力，但不复用认知、咨询会议总结、最近活动的业务结果 Schema。
- 最近活动固定 realtime；四个内容模型策略由服务器配置决定 realtime 或 Batch，Batch 保持 100 候选 / 200000 Token 阈值。
- realtime 与 Batch 通过 `build_model_request_body` 共用 `temperature=0.01`、`enable_thinking=false`、`seed=1234` 和 JSON object 输出；seed 已按百炼 OpenAI 兼容接口支持范围核对，不承诺字节级复现。
- 契约失败由 Worker 写入受控 `audit_root/model-review/<strategy>+<original-stem>.json`，保留响应指纹/受控模型结果、字段路径和错误摘要；本地条目保留源 Markdown 但保持 `review_required`，事件审计复用当前事务，避免测试 SQLite 并发锁。
- 发布前在同知识库/策略/规格锁内按规范化最终正文指纹去重；重复条目标记 `excluded` 并写入去重范围，未审批条目不会创建 Dify 文档。显式 approve 复用既有内部复核入口后才重新排队发布，exclude 为终态。

## 4. Modules and Ownership

| Module | Owned paths | Acceptance |
|---|---|---|
| activity-chatflow | `dify/商业顾问（当前使用版本）.yml`, `dify/商业顾问_统一修复版_Dify-1.13.3.yml`, `tests/test_dify_activity_contract.py`, `tests/test_business_advisor_unified_dsl.py` | AC-001, AC-002, AC-003 |
| model-cleaning-policy | `ai_management/model_cleaning.py`, `ai_management/config.py` | AC-004, AC-005 |
| model-cleaning-orchestration | `ai_management/workers/tasks/model_cleaning.py`, `ai_management/services/bailian_cleanup.py` | AC-004, AC-005, AC-006 |
| strategy-contracts | `ai_management/cleaning.py`, `ai_management/activity_cleaning.py`, `knowledge_cleaner/dify.py` | AC-005, AC-008, AC-009 |
| cleaning-contract-tests | `tests/test_model_cleaning_workflow.py`, `tests/test_large_scale_cleaning.py`, `tests/test_activity_cleaning.py`, `tests/test_release_acceptance_contract.py` | AC-004, AC-005, AC-006, AC-008, AC-009 |
| jx-acceptance-docs | `scripts/jx_*.sh`, `docs/features/qwen-batch-cleaning/**`, `docs/features/recent-activity/**`, `var/reports/**` | AC-007, AC-010 |
| lease-fencing | `ai_management/lease.py`, `ai_management/workers/tasks/cleaning.py`, `ai_management/workers/tasks/model_cleaning.py`, `ai_management/workers/tasks/maintenance.py`, `ai_management/models.py`, `migrations/versions/*publication_shard_leases.py` | AC-011, AC-012 |
| tool-sse-contract | `ai_management/dify.py`, `ai_management/api/routers/tools.py`, `tests/test_tool_conversations_contract.py` | AC-013 |

Shared `cleaning.py`/Dify changes must preserve existing strategy behavior and require regression coverage; do not add repository/service/DI layers.

## 5. Dependency Direction

Allowed: Chatflow → Dify retrieval output; strategy → existing cleaner/model request; Worker → existing DB/Celery/Redis/Dify seams; tests → isolated fakes and static DSL execution.

Forbidden: Chatflow → PostgreSQL/Redis; strategy → HTTP router/UI; direct Dify database edits; name/time-window deletion; secret or source正文 in logs/reports; re-enable course model paths.

## 6. Reuse Decisions

Reuse: `choose_call_mode`, existing Qwen client/request builder, Prompt version/fingerprint, Pydantic/business validators, `poll_batch_model_task`, Dify publication and exact manifest scripts. New shared code is permitted only when it owns transport-level parity and does not merge the three business contracts.

## 7. Contract Impact

- Public API/SSE: no new fields; existing strategy discovery and answer stream remain compatible.
- Dify environment `ACTIVITY_MAX_RESULTS` defaults to 10 and feeds the numeric `activity_max_results` output; activity Code adds semantic intent/detail behavior but no external SSE schema change.
- Persistent schema: one additive nullable PublicationShard lease migration only; no other migrations.
- Runtime: recent activity never schedules Batch; content strategies snapshot realtime/Batch mode at task creation.
- Security/data: only allowlisted public Metadata crosses to Dify; old target-strategy general data is deleted without backup only after exact ownership/ID manifest validation; subsequent processing is limited to new uploads and their artifacts remain.

## 8. Vertical Implementation Slices

| Slice | Decisions | Acceptance | Seam / level | TDD | Focus |
|---|---|---|---|---|---|
| S1 Activity intent/output | RD-001, RD-002 | AC-001, AC-002, AC-003 | Execute Chatflow Code + static DSL contract seam | TDD yes | phrase matrix, negative precedence, 10 cap, formatting, details |
| S2 Mode/request parity | RD-003, RD-004, RD-005, RD-006 | AC-004, AC-005 | planner + shared request/validator/publisher fakes seam | TDD yes | recent realtime, four content modes, distinct contracts, deterministic Metadata |
| S3 Batch lifecycle | RD-005, RD-006 | AC-004, AC-005, AC-006 | `poll_batch_model_task` with provider/task fakes seam | TDD yes | threshold, transient errors, terminal stop, duplicate/missing/bad output |
| S4 Naming/dedup/review | RD-008, RD-009 | AC-008, AC-009 | naming/publish/review service seams with DB/provider fakes | TDD yes | source stem, cross-source hash, concurrent idempotency, artifact and approval lifecycle |
| S5 jx data/release | RD-007 | AC-007, AC-010 | manifest + real jx/Dify/Bailian seam | TDD no | pre-clean, real dual-path, retained data, DSL/SSE evidence |
| S6 Lease/fencing | RD-010 | AC-011 | DB task seam with PostgreSQL concurrency fakes | TDD yes | claim, heartbeat, takeover, stale finalize, cancel/recovery |
| S7 Outbox convergence | RD-011 | AC-012 | ExternalDeletionTask consumer seam | TDD yes | single consumer, idempotent enqueue, backoff/manual, 404 |
| S8 SSE cleanup | RD-012 | AC-013 | Chat/Workflow generator seam | TDD yes | DB/parse errors, rollback, mapping cleanup, disconnect |

## 9. Canonical Quality Commands

- Format/lint/compile and canonical `gov quality` commands from `.codex/governance/project.json`.
- Focused activity/DSL tests and model workflow/large-scale tests.
- Real jx test keeps Batch thresholds unchanged, uses existing budget fail-closed guard, and records per-strategy provider status, document/index/Metadata/readback evidence.
- Any failure is recorded first; after the test set is complete, fix all local issues and rerun from a new evidence batch. Do not claim success from partial or cancelled runs.

## 8. Rollout / Rollback

1. Complete local implementation and quality checks.
2. Precisely delete old target-strategy `general` data using an exact manifest; verify Dify/local/Redis/upload scopes. Per the current user authorization, no data backup is created, and non-target data is not touched.
3. Deploy immutable release to jx test environment; temporarily enable Batch only through server configuration, then restore the prior configuration after the comparison.
4. If DSL changed, back up current DSL/app configuration, import/publish tested DSL, verify `/parameters`, current binding, positive/negative SSE and ordinary/case regressions.
5. Keep all current test results. Rollback uses the pre-deploy code/DSL/config backup; no Dify DB or volume mutation.

## 9. Escalation Triggers

- Current Qwen model rejects a stability parameter; remove only that parameter and document evidence.
- Dify dynamic Metadata or `doc_metadata` is unavailable; stop formal Chatflow switch.
- Existing threshold cannot produce a real Batch within budget/24-hour provider window; stop and report as external-blocked rather than lowering production-equivalent threshold.
- Exact general cleanup ownership or jx release boundary cannot be proven.
- A public API, migration, security boundary, or architecture change becomes necessary.

## 10. Open Questions

None

## 11. Reliability Contract

- Remote side effects are at-least-once with idempotency, precise compensation and eventual convergence; exactly-once is not promised.
- Lease heartbeat is at most TTL/3, fails closed on database failure, and every finalize/retry/release/cancel/recovery path checks id plus token plus unexpired lease.
- ExternalDeletionTask has one logical consumer; local cleanup only upserts outbox and never performs direct remote deletion.
- PublicationShard uses its own lease columns from the single approved migration. If migration/schema verification fails, stop the slice before deployment.
- Any implementation change after approval requires re-approval and current independent review.

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
