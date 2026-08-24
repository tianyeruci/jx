# 最近活动清洗与商业顾问 Chatflow 接入 — Implementation Spec

- Feature ID: `recent-activity`
- Status: APPROVED FOR IMPLEMENTATION
- Planner: GPT-5
- Implementer: exactly one `luna_implementer`
- Architecture: `docs/architecture/PROFILE.md` (L2)

## 1. Goal / Non-goals Reference

在现有活动清洗与 Dify `general` 链路上修复自然语言活动意图、列表/详情展示和 10 条默认上限。活动清洗仍使用实时 Qwen；不新增查询期数据库/Redis 服务、公开 API 或课程能力。

## 2. Repository Evidence

- `dify/商业顾问（当前使用版本）.yml` 的 `quick_intent` 已扩展活动实体、状态、详情、报名和序号/指代表达，并保持案例/分析负向优先。
- 活动结果 Code 已改为环境变量 `ACTIVITY_MAX_RESULTS`（默认 10，安全限制 1～20）；列表只输出名称/时间/状态，详情分支才保留摘要、地点/形式和有效链接。
- `ai_management/activity_cleaning.py`、`ai_management/cleaning.py` 和发布 Worker 已提供活动事实、Metadata、同名替换和索引回读能力；不复制独立脚本运行时状态。

## 3. Design Decision

1. 混合意图：确定性规则处理活动实体、列表、状态、详情、报名和序号/指代表达；分析/案例负向优先，无法确认时保持普通分支，避免误进入活动检索。
2. 普通近期窗口为未来 30 天正常/延期；所有活动为前后 30 天四状态；列表默认 10 条、逐行名称/时间/状态；指定活动详情按名称或序号只返回一个活动并使用现有公开业务字段。
3. `recent_activity` 的模型任务固定 realtime，不参与 Provider Batch；活动 Metadata 和事实边界不改。
4. 保持 Dify 动态 metadata、二次排序、去重、报名链接门禁和现有 SSE answer 契约。

## 4. Modules and Ownership

| Module | Owned paths | Acceptance |
|---|---|---|
| activity-chatflow | `dify/商业顾问（当前使用版本）.yml`, `dify/商业顾问_统一修复版_Dify-1.13.3.yml`, `tests/test_dify_activity_contract.py`, `tests/test_business_advisor_unified_dsl.py` | AC-005, AC-006, AC-007 |
| activity-cleaning | `ai_management/activity_cleaning.py`, `ai_management/cleaning.py`, `tests/test_activity_cleaning.py` | AC-001, AC-002, AC-003 |
| cleaning-publication | `ai_management/workers/tasks/cleaning.py`, `knowledge_cleaner/dify.py`, `tests/test_release_acceptance_contract.py` | AC-002, AC-004 |
| activity-validation-docs | `docs/features/recent-activity/**`, `var/reports/**` | AC-008 |

## 5. Dependency Direction

Allowed: Chatflow → Dify retrieval output; activity strategy → existing cleaner/model request; Worker → existing DB/Celery/Dify seams; tests → isolated fakes/static DSL.

Forbidden: Chatflow → PostgreSQL/Redis; strategy → router/UI; direct Dify database edits; name/time-window deletion; secrets/source正文 in reports; changes to course paths.

## 6. Reuse Decisions

Reuse existing activity parser, Metadata allowlist, publication/readback and compensation. Do not merge activity facts with cognition or consultation schemas.

## 7. Contract Impact

- Public API/SSE: none; existing answer stream remains unchanged.
- Dify environment variable: `ACTIVITY_MAX_RESULTS` defaults to 10 and is clamped to 1～20; the internal `activity_max_results` output remains numeric.
- Persistent schema/migration: none.
- Compatibility: normal business and consultation-case routing remains unchanged; recent activity remains realtime only.
- Security: only allowlisted public activity fields are exposed.

## 8. Vertical Implementation Slices

| Slice | Decisions | Acceptance | Seam / level | TDD | Focus |
|---|---|---|---|---|---|
| S1 intent and formatter | RD-010, RD-008 | AC-005, AC-006, AC-007 | execute Chatflow Code + static DSL seam | TDD yes | phrase matrix, negative precedence, 10 cap, list/detail formatting |
| S2 activity contract | RD-003, RD-006 | AC-001, AC-002, AC-003, AC-004 | activity strategy + publication fakes seam | TDD yes | facts, status, Metadata, no Batch, replacement compatibility |
| S3 jx Chatflow | RD-001, RD-002, RD-007 | AC-005, AC-006, AC-007, AC-008 | isolated Dify/jx SSE seam | TDD no | dynamic filters, real queries, ordinary/case negatives |

## 9. Canonical Quality Commands

- Run project canonical `gov quality` and activity/DSL focused tests.
- Add tests for “最新活动有哪些”“最近活动有哪些”“活动有哪些”“所有活动”、analysis/case negatives, 0/1/10/over-limit, four statuses, postponed-unknown, list/detail formatting and valid link behavior.
- jx evidence must separately record Dify index, Metadata/readback, Chatflow answer and SSE completion; HTTP 200 alone is insufficient.

## 10. Escalation Triggers

- Dify classifier or dynamic Metadata is unavailable in the bound Chatflow; stop the formal switch and report evidence.
- Activity facts cannot prove status/time/link provenance or exact jx test scope; do not publish.
- A public API, migration, security boundary, or architecture change becomes necessary.

## 11. Rollout / Rollback

Deploy with the combined jx release after all local issues are fixed. If DSL changed, back up current DSL/app configuration, import/publish the tested DSL and verify current test App binding. Rollback uses the pre-deploy code/DSL/config backup; no Dify DB or volume mutation.

## 10. Open Questions

None

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
