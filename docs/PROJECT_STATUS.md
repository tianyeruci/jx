# Project Status

> GENERATED FILE — do not edit manually. Source: `docs/features/*/feature.json` plus current approval/documentation/module state.

## Active Features

| Feature | Title | Kind | Status | Risk | Module progress | Docs | Updated |
|---|---|---|---|---|---|---|---|
| cleaning-tolerance-methodology | 文档清洗容错、咨询方案方法与隔离测试修复 | engineering-change | requirements_approved | high | 0/5 | stale | 2026-09-13T19:41:40+08:00 |
| moments-ip-methodology | 朋友圈顾问 IP 方法论 Prompt 与案例增量 | engineering-change | stale_requirements | standard | 0/4 | current | 2026-08-30T15:33:18+08:00 |
| qwen-batch-cleaning | 千问 Batch 清洗策略验证与轮询修复 | engineering-change | done | high | 0/8 | stale | 2026-09-13T19:41:40+08:00 |
| recent-activity | 最近活动清洗与商业顾问 Chatflow 接入 | feature | stale_requirements | high | 0/4 | stale | 2026-09-13T19:41:40+08:00 |
| tool-examples | 朋友圈与日常写作可配置案例目录 | feature | done | standard | 4/7 | current | 2026-09-13T19:41:40+08:00 |

## Functional Module Progress

### cleaning-tolerance-methodology — 文档清洗容错、咨询方案方法与隔离测试修复

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| semantic-contract | in_progress | AC-001, AC-002, AC-003 | 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。 | `knowledge_cleaner/consultation_rag.py`<br>`ai_management/model_cleaning.py`<br>`tests/test_consultation_rag_integration.py`<br>`tests/test_model_cleaning_workflow.py` |
| cleaning-workers | in_progress | AC-001, AC-002, AC-005 | 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。 | `ai_management/workers/tasks/model_cleaning.py`<br>`ai_management/workers/tasks/cleaning.py`<br>`ai_management/cleaning.py`<br>`tests/test_model_cleaning_*.py`<br>`tests/test_ai_management.py` |
| pdf-ocr-route | in_progress | AC-004 | 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。 | `knowledge_cleaner/pdf_convert.py`<br>`knowledge_cleaner/rapidocr_backend.py`<br>`tests/test_pdf_ocr_quality.py` |
| isolated-test-deploy | in_progress | AC-006 | 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。 | `docker-compose.cleaning-test.yml`<br>`docker-compose.cleaning-test-ui.yml`<br>`scripts/jx_*cleaning*`<br>`ai_management/config.py`<br>`ai_management/api/routers/knowledge_admin.py`<br>`ai_management/test_ui.py`<br>`tests/test_*isolated*.py`<br>`tests/test_test_ui.py` |
| contract-docs | in_progress | AC-001, AC-006 | 记录 U06 测试合同已验证、canonical quality 通过及实施再批准 fail-closed 阻塞；不改变产品语义或远端边界。 | `docs/执行计划合集/*.md`<br>`docs/troubleshooting/*.md`<br>`AI管理服务当前状态与后续测试交接.md` |

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
| model-cleaning-policy | in_progress | AC-001, AC-004, AC-005 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `ai_management/model_cleaning.py`<br>`ai_management/config.py` |
| model-cleaning-orchestration | in_progress | AC-002, AC-004, AC-005, AC-006 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `ai_management/workers/tasks/model_cleaning.py`<br>`ai_management/services/bailian_cleanup.py` |
| model-cleaning-contract-tests | in_progress | AC-001, AC-002, AC-003, AC-004 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `tests/test_model_cleaning_workflow.py` |
| jx-cleaning-acceptance | in_progress | AC-005, AC-006 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `scripts/jx_*.sh` |
| activity-chatflow | in_progress | AC-001, AC-002, AC-003 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `dify/商业顾问（当前使用版本）.yml`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_dify_activity_contract.py`<br>`tests/test_business_advisor_unified_dsl.py` |
| strategy-contracts | in_progress | AC-005, AC-008, AC-009 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `ai_management/cleaning.py`<br>`ai_management/activity_cleaning.py`<br>`knowledge_cleaner/dify.py` |
| cleaning-contract-tests | in_progress | AC-004, AC-005, AC-006, AC-008, AC-009 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `tests/test_model_cleaning_workflow.py`<br>`tests/test_large_scale_cleaning.py`<br>`tests/test_activity_cleaning.py`<br>`tests/test_release_acceptance_contract.py` |
| jx-acceptance-docs | in_progress | AC-007, AC-008, AC-010 | 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。 | `scripts/jx_*.sh`<br>`docs/features/qwen-batch-cleaning/**`<br>`docs/features/recent-activity/**`<br>`var/reports/**` |

### recent-activity — 最近活动清洗与商业顾问 Chatflow 接入

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| activity-cleaning | in_progress | AC-001, AC-002, AC-003, AC-004 | 确认 recent_activity 不参与 post-shard 共享引用判断；保留旧远端 document 的精确 outbox cleanup 与当前活动 Python 清洗行为。 | `ai_management/activity_cleaning.py`<br>`ai_management/cleaning.py`<br>`Other/event_rag_cleaner/**`<br>`tests/test_activity_cleaning.py` |
| cleaning-publication | in_progress | AC-002, AC-004 | 确认 recent_activity 不参与 post-shard 共享引用判断；保留旧远端 document 的精确 outbox cleanup 与当前活动 Python 清洗行为。 | `ai_management/workers/tasks/cleaning.py`<br>`knowledge_cleaner/dify.py` |
| activity-chatflow | in_progress | AC-005, AC-006, AC-007 | 确认 recent_activity 不参与 post-shard 共享引用判断；保留旧远端 document 的精确 outbox cleanup 与当前活动 Python 清洗行为。 | `dify/商业顾问（当前使用版本）.yml`<br>`tests/test_dify_activity_contract.py`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_business_advisor_unified_dsl.py` |
| activity-validation-docs | verified | AC-008 | 本次文档清洗修复未改变 recent_activity 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `docs/features/recent-activity/**`<br>`ai_management.zip` |

### tool-examples — 朋友圈与日常写作可配置案例目录

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| examples-service | verified | AC-001, AC-003, AC-005 | 本次文档清洗修复未改变 tool-examples 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `ai_management/services/tool_examples.py` |
| examples-contracts | done | AC-001, AC-003, AC-004 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/schemas.py` |
| examples-api | verified | AC-001, AC-003, AC-004, AC-005 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/api/routers/tools.py` |
| examples-admin-api | done | AC-003, AC-004 | - | `ai_management/api/routers/admin_management.py` |
| examples-ui | done | AC-002, AC-005 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/test_ui.py` |
| examples-tests | done | AC-001, AC-003, AC-004, AC-005, AC-006 | 本次发布仅涉及运行时区和清洗命名，工具会话及 DSL 业务语义不变。 | `tests/test_tool_examples.py`<br>`tests/test_app_routing.py`<br>`tests/test_v2_hardening.py`<br>`tests/test_cleaning_release_contract.py` |
| examples-docs | in_progress | AC-006 | 交接明确 canonical quality 已通过，独立复审、合法再批准和真实隔离验收仍未完成。 | `docs/小程序后端与 AI 管理服务接口对接交互版本.md`<br>`docs/小程序后端与AI管理服务接口对接标准.md`<br>`docs/API与SSE说明.md`<br>`docs/功能与接口索引.md`<br>`docs/README.md`<br>`docs/Dify创建导入绑定与契约测试.md`<br>`docs/architecture/PROFILE.md`<br>`AI管理服务当前状态与后续测试交接.md` |

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
