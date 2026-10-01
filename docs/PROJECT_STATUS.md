# Project Status

> GENERATED FILE — do not edit manually. Source: `docs/features/*/feature.json` plus current approval/documentation/module state.

## Active Features

| Feature | Title | Kind | Status | Risk | Module progress | Docs | Updated |
|---|---|---|---|---|---|---|---|
| cleaning-tolerance-methodology | 文档清洗容错、咨询方案方法与隔离测试修复 | engineering-change | approved | high | 0/6 | stale | 2026-09-30T22:08:34+08:00 |
| moments-ip-methodology | 朋友圈顾问 IP 方法论 Prompt 与案例增量 | engineering-change | stale_requirements | standard | 0/4 | current | 2026-08-30T15:33:18+08:00 |
| qwen-batch-cleaning | 千问 Batch 清洗策略验证与轮询修复 | engineering-change | approved | high | 0/8 | stale | 2026-09-30T22:08:35+08:00 |
| recent-activity | 最近活动清洗与商业顾问 Chatflow 接入 | feature | approved | high | 0/4 | stale | 2026-09-30T22:08:35+08:00 |
| tool-examples | 朋友圈与日常写作可配置案例目录 | feature | approved | standard | 3/7 | stale | 2026-09-30T22:08:36+08:00 |

## Functional Module Progress

### cleaning-tolerance-methodology — 文档清洗容错、咨询方案方法与隔离测试修复

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| semantic-contract | verified | AC-001, AC-002, AC-003 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `knowledge_cleaner/consultation_rag.py`<br>`ai_management/model_cleaning.py`<br>`tests/test_consultation_rag_integration.py`<br>`tests/test_model_cleaning_workflow.py`<br>`tests/test_cleaning_tolerance_methodology_regression.py` |
| cleaning-workers | verified | AC-001, AC-002, AC-005 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `ai_management/workers/tasks/model_cleaning.py`<br>`ai_management/workers/tasks/cleaning.py`<br>`ai_management/cleaning.py`<br>`tests/test_model_cleaning_*.py`<br>`tests/test_ai_management.py`<br>`ai_management/services/dify_references.py`<br>`ai_management/workers/tasks/maintenance.py`<br>`ai_management/models.py`<br>`migrations/versions/o2p3q4r5s6t7_source_delete_tombstone_generation.py`<br>`tests/test_dify_client.py` |
| pdf-ocr-route | verified | AC-004 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `knowledge_cleaner/pdf_convert.py`<br>`knowledge_cleaner/rapidocr_backend.py`<br>`tests/test_pdf_ocr_quality.py` |
| isolated-test-deploy | verified | AC-006, AC-007 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `docker-compose.cleaning-test.yml`<br>`docker-compose.cleaning-test-ui.yml`<br>`scripts/jx_*cleaning*`<br>`ai_management/config.py`<br>`ai_management/api/routers/knowledge_admin.py`<br>`ai_management/api/routers/health.py`<br>`ai_management/test_ui.py`<br>`tests/test_test_ui.py`<br>`Dockerfile.cleaning-test`<br>`Dockerfile.cleaning-test-ocr`<br>`Dockerfile.cleaning-test-ui`<br>`tests/test_cleaning_test_deploy_contract.py`<br>`tmp/preflight-contract-*/isolated.env`<br>`tmp/start-compensation-contract-*/compose-calls.log`<br>`uv.lock`<br>`tmp/start-compensation-contract-*/compose-calls.log*`<br>`tmp/attest-debug/a`<br>`tmp/f.env`<br>`docker-compose.ys.yml` |
| contract-docs | verified | AC-001, AC-006 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `docs/执行计划合集/jx隔离清洗测试接续执行计划.md`<br>`docs/troubleshooting/*.md`<br>`AI管理服务当前状态与后续测试交接.md`<br>`scripts/generate_project_index.py`<br>`tests/test_governance_contract.py` |
| acceptance-runner | verified | AC-005, AC-006, AC-007 | Bind current final paid POST defect fix to its shared owners after complete Quality; one HTTP generation attempt per reservation, existing outer two-call limit preserved. No further product behavior changes. | `scripts/full_cleaning_acceptance.py`<br>`scripts/jx_release_data.py`<br>`ai_management/acceptance_budget.py`<br>`tests/test_release_acceptance_contract.py` |

### moments-ip-methodology — 朋友圈顾问 IP 方法论 Prompt 与案例增量

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| prompt-dsl | verified | AC-001, AC-002 | 修正 request/source_material 事实来源冲突，保持现有 Dify App、qwen-plus、API Key、可选素材和 SSE 链路；新增测试锁定完整 inputs、管理员风格、现有 moments Key 映射和成功事件序列。已完成 Dify 原 App 发布、jx 测试页五类回归、精确测试记录清理及本地 canonical quality。 | `dify/dsl/moments_advisor.yml`<br>`dify/prompts/朋友圈顾问提示词.md` |
| examples-defaults | verified | AC-003 | 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。 | `ai_management/services/tool_examples.py` |
| examples-tests | verified | AC-003 | 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。 | `tests/test_tool_examples.py` |
| release-docs | not_started | AC-004, AC-005 | - | `docs/features/moments-ip-methodology` |

### qwen-batch-cleaning — 千问 Batch 清洗策略验证与轮询修复

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| model-cleaning-policy | verified | AC-001, AC-004, AC-005 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `ai_management/model_cleaning.py`<br>`ai_management/config.py` |
| model-cleaning-orchestration | verified | AC-002, AC-004, AC-005, AC-006 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `ai_management/workers/tasks/model_cleaning.py`<br>`ai_management/services/bailian_cleanup.py` |
| model-cleaning-contract-tests | verified | AC-001, AC-002, AC-003, AC-004 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `tests/test_model_cleaning_workflow.py` |
| jx-cleaning-acceptance | verified | AC-005, AC-006 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `scripts/jx_*.sh` |
| activity-chatflow | verified | AC-001, AC-002, AC-003 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `dify/商业顾问（当前使用版本）.yml`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_dify_activity_contract.py`<br>`tests/test_business_advisor_optimized_dsl.py`<br>`tests/test_business_advisor_unified_dsl.py` |
| strategy-contracts | verified | AC-005, AC-008, AC-009 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `ai_management/cleaning.py`<br>`ai_management/activity_cleaning.py`<br>`knowledge_cleaner/dify.py` |
| cleaning-contract-tests | verified | AC-004, AC-005, AC-006, AC-008, AC-009 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `tests/test_model_cleaning_workflow.py`<br>`tests/test_large_scale_cleaning.py`<br>`tests/test_activity_cleaning.py`<br>`tests/test_release_acceptance_contract.py` |
| jx-acceptance-docs | verified | AC-007, AC-008, AC-010 | Bind final paid POST defect fix to current shared owner; one HTTP generation attempt per reservation with existing outer two-call limit. Complete Quality and independent local retry checks passed; no further product changes. | `scripts/jx_*.sh`<br>`docs/features/qwen-batch-cleaning/**`<br>`docs/features/recent-activity/**`<br>`var/reports/**` |

### recent-activity — 最近活动清洗与商业顾问 Chatflow 接入

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| activity-cleaning | verified | AC-001, AC-002, AC-003, AC-004 | Bind final shared client paid POST fix to current candidate after verified local retry checks. No recent activity business API changes. | `ai_management/activity_cleaning.py`<br>`ai_management/cleaning.py`<br>`Other/event_rag_cleaner/**`<br>`tests/test_activity_cleaning.py` |
| cleaning-publication | verified | AC-002, AC-004 | Bind final shared client paid POST fix to current candidate after verified local retry checks. No recent activity business API changes. | `ai_management/workers/tasks/cleaning.py`<br>`knowledge_cleaner/dify.py` |
| activity-chatflow | verified | AC-005, AC-006, AC-007 | Bind final shared client paid POST fix to current candidate after verified local retry checks. No recent activity business API changes. | `dify/商业顾问（当前使用版本）.yml`<br>`tests/test_dify_activity_contract.py`<br>`tests/test_business_advisor_optimized_dsl.py`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_business_advisor_unified_dsl.py` |
| activity-validation-docs | verified | AC-008 | 本次文档清洗修复未改变 recent_activity 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `docs/features/recent-activity/**`<br>`ai_management.zip` |

### tool-examples — 朋友圈与日常写作可配置案例目录

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| examples-service | verified | AC-001, AC-003, AC-005 | 本次文档清洗修复未改变 tool-examples 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `ai_management/services/tool_examples.py` |
| examples-contracts | done | AC-001, AC-003, AC-004 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/schemas.py` |
| examples-api | verified | AC-001, AC-003, AC-004, AC-005 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/api/routers/tools.py` |
| examples-admin-api | done | AC-003, AC-004 | - | `ai_management/api/routers/admin_management.py` |
| examples-ui | done | AC-002, AC-005 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/test_ui.py` |
| examples-tests | verified | AC-001, AC-003, AC-004, AC-005, AC-006 | Bind final shared client paid POST fix to current candidate after verified local retry checks. No tool business API changes. | `tests/test_tool_examples.py`<br>`tests/test_app_routing.py`<br>`tests/test_v2_hardening.py`<br>`tests/test_cleaning_release_contract.py` |
| examples-docs | verified | AC-006 | Bind final shared client paid POST fix to current candidate after verified local retry checks. No tool business API changes. | `docs/小程序后端与 AI 管理服务接口对接交互版本.md`<br>`docs/小程序后端与AI管理服务接口对接标准.md`<br>`docs/API与SSE说明.md`<br>`docs/功能与接口索引.md`<br>`docs/README.md`<br>`docs/Dify创建导入绑定与契约测试.md`<br>`docs/architecture/PROFILE.md`<br>`AI管理服务当前状态与后续测试交接.md` |

## Status

- `draft`: requirements/planning not approved
- `requirements_approved`: current requirements approved; implementation plan not yet approved
- `approved`: implementation contract approved
- `in_progress`: implementation active
- `stale_requirements`: SPEC changed after its recorded approval
- `stale_implementation`: implementation/architecture/quality/module contract changed after approval
- `blocked`: material issue pending
- `done`: completed and validated
- `superseded`: retained history; replaced by newer feature records
- `archived`: retained history; no longer current product scope

## Module lifecycle

- `not_started`: planned in the approved module map
- `in_progress`: matching owned implementation path changed
- `verified`: current Quality Gate passed after the module change
- `done`: task completed with current docs/quality/review gates
- `blocked`: implementation cannot currently proceed

## Documentation

- `current`: semantic docs/changelog are synchronized with the recorded implementation diff
- `stale`: code/config/test changes were detected after the last documentation synchronization
