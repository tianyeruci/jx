# Project Status

> GENERATED FILE — do not edit manually. Source: `docs/features/*/feature.json` plus current approval/documentation/module state.

## Active Features

| Feature | Title | Kind | Status | Risk | Module progress | Docs | Updated |
|---|---|---|---|---|---|---|---|
| advisor-message-actions | 三类 AI 顾问消息操作 | feature | done | standard | 7/7 | current | 2026-09-09T14:52:12+08:00 |
| moments-ip-methodology | 朋友圈顾问 IP 方法论 Prompt 与案例增量 | engineering-change | stale_requirements | standard | 1/4 | current | 2026-09-09T14:52:12+08:00 |
| qwen-batch-cleaning | 千问 Batch 清洗策略验证与轮询修复 | engineering-change | done | high | 2/8 | current | 2026-09-09T14:52:13+08:00 |
| recent-activity | 最近活动清洗与商业顾问 Chatflow 接入 | feature | stale_requirements | high | 0/4 | stale | 2026-08-31T14:36:58+08:00 |
| tool-examples | 朋友圈与日常写作可配置案例目录 | feature | done | standard | 6/7 | current | 2026-09-09T14:52:14+08:00 |

## Functional Module Progress

### advisor-message-actions — 三类 AI 顾问消息操作

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| business-regeneration | done | AC-001, AC-002 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `ai_management/conversations.py`<br>`ai_management/context.py`<br>`ai_management/api/routers/business.py` |
| summary-safety | done | AC-003 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `ai_management/workers/tasks/summary.py` |
| tool-message-actions | done | AC-001, AC-004, AC-005 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `ai_management/dify.py`<br>`ai_management/api/routers/tools.py`<br>`ai_management/schemas.py` |
| entry-test-ui | done | AC-001, AC-005, AC-007 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `ai_management/api/routers/entry.py`<br>`ai_management/test_ui.py` |
| message-action-tests | done | AC-001, AC-002, AC-003, AC-004, AC-005, AC-007, AC-008 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `tests/test_ai_management.py`<br>`tests/test_tool_conversations_contract.py`<br>`tests/test_app_routing.py`<br>`tests/test_dify_client.py`<br>`tests/test_moments_run_contract.py`<br>`tests/test_test_ui.py`<br>`tests/test_cleaning_release_contract.py`<br>`tests/test_advisor_message_action_docs.py` |
| dify-memory-contract | done | AC-006 | 新增入口级持久错误状态：HTTP 预检失败保留旧答案和 message ID，已受理失败保留错误并清空答案，成功/停止清除旧错误；页面结果区独立渲染错误。 | `dify/dsl/moments_advisor.yml`<br>`dify/dsl/writing_advisor.yml`<br>`tests/test_business_advisor_optimized_dsl.py`<br>`tests/test_business_advisor_unified_dsl.py` |
| contract-docs | not_started | AC-008 | - | `docs/API与SSE说明.md`<br>`docs/小程序后端与AI管理服务接口对接标准.md`<br>`docs/Dify创建导入绑定与契约测试.md`<br>`docs/功能与接口索引.md`<br>`AI管理服务当前状态与后续测试交接.md`<br>`docs/features/advisor-message-actions/**`<br>`docs/architecture/PROFILE.md`<br>`docs/小程序后端与 AI 管理服务接口对接交互版本.md`<br>`docs/小程序后端与 AI 管理服务接口对接交互版本（修改后接口影响版）.md`<br>`docs/测试与验收指南.md`<br>`docs/README.md` |

### moments-ip-methodology — 朋友圈顾问 IP 方法论 Prompt 与案例增量

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| prompt-dsl | done | AC-001, AC-002 | 方法论 Prompt 与节点不变；朋友圈工具错误状态跨 app rerun 保持可见。 | `dify/dsl/moments_advisor.yml`<br>`dify/prompts/朋友圈顾问提示词.md` |
| examples-defaults | verified | AC-003 | 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。 | `ai_management/services/tool_examples.py` |
| examples-tests | verified | AC-003 | 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。 | `tests/test_tool_examples.py` |
| release-docs | not_started | AC-004, AC-005 | - | `docs/features/moments-ip-methodology` |

### qwen-batch-cleaning — 千问 Batch 清洗策略验证与轮询修复

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| model-cleaning-policy | verified | AC-001, AC-004, AC-005 | 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。 | `ai_management/model_cleaning.py`<br>`ai_management/config.py` |
| model-cleaning-orchestration | verified | AC-002, AC-004, AC-005, AC-006 | 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。 | `ai_management/workers/tasks/model_cleaning.py`<br>`ai_management/services/bailian_cleanup.py` |
| model-cleaning-contract-tests | verified | AC-001, AC-002, AC-003, AC-004 | 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。 | `tests/test_model_cleaning_workflow.py` |
| jx-cleaning-acceptance | done | AC-005, AC-006 | 补充 Streamlit fragment 终态错误持久化排障；Qwen Batch、清洗与发布语义不变。 | `scripts/jx_*.sh` |
| activity-chatflow | verified | AC-001, AC-002, AC-003 | 四个内容清洗策略继续保留实时/Batch配置，最近活动固定实时；实时与Batch共用temperature 0.01、seed 1234、enable_thinking false和JSON object请求体；活动列表/详情DSL已发布并验证环境变量默认10。验收脚本补齐源树预算模块、咨询多案例子文档命名契约，Dify SSE安全证据已生成；四策略真实Batch对照仍待jx部署后按100候选阈值完成。 | `dify/商业顾问（当前使用版本）.yml`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_dify_activity_contract.py`<br>`tests/test_business_advisor_unified_dsl.py` |
| strategy-contracts | verified | AC-005, AC-008, AC-009 | 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。 | `ai_management/cleaning.py`<br>`ai_management/activity_cleaning.py`<br>`knowledge_cleaner/dify.py` |
| cleaning-contract-tests | verified | AC-004, AC-005, AC-006, AC-008, AC-009 | 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。 | `tests/test_model_cleaning_workflow.py`<br>`tests/test_large_scale_cleaning.py`<br>`tests/test_activity_cleaning.py`<br>`tests/test_release_acceptance_contract.py` |
| jx-acceptance-docs | done | AC-007, AC-008, AC-010 | 补充 Streamlit fragment 终态错误持久化排障；Qwen Batch、清洗与发布语义不变。 | `scripts/jx_*.sh`<br>`docs/features/qwen-batch-cleaning/**`<br>`docs/features/recent-activity/**`<br>`var/reports/**` |

### recent-activity — 最近活动清洗与商业顾问 Chatflow 接入

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| activity-cleaning | verified | AC-001, AC-002, AC-003, AC-004 | 共享清理生命周期在取消异常时保留精确远端引用并可由 maintenance 重建；recent_activity 业务语义未改变。 | `ai_management/activity_cleaning.py`<br>`ai_management/cleaning.py`<br>`Other/event_rag_cleaner/**`<br>`tests/test_activity_cleaning.py` |
| cleaning-publication | verified | AC-002, AC-004 | 共享清理生命周期在取消异常时保留精确远端引用并可由 maintenance 重建；recent_activity 业务语义未改变。 | `ai_management/workers/tasks/cleaning.py`<br>`knowledge_cleaner/dify.py` |
| activity-chatflow | verified | AC-005, AC-006, AC-007 | 活动查询现在按自然表达识别列表与命名详情；详情走无时间窗口状态检索并支持第1至第5个序号；列表默认10条逐行输出名称、时间、状态，ACTIVITY_MAX_RESULTS可配置；最近活动保持实时Qwen。Dify发布后验证了最近/最新/全部活动与命名详情SSE形状。 | `dify/商业顾问（当前使用版本）.yml`<br>`tests/test_dify_activity_contract.py`<br>`dify/商业顾问_统一修复版_Dify-1.13.3.yml`<br>`tests/test_business_advisor_unified_dsl.py` |
| activity-validation-docs | verified | AC-008 | 本次文档清洗修复未改变 recent_activity 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `docs/features/recent-activity/**`<br>`ai_management.zip` |

### tool-examples — 朋友圈与日常写作可配置案例目录

| Module | Status | Acceptance | Last change | Owned paths |
|---|---|---|---|---|
| examples-service | verified | AC-001, AC-003, AC-005 | 本次文档清洗修复未改变 tool-examples 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。 | `ai_management/services/tool_examples.py` |
| examples-contracts | done | AC-001, AC-003, AC-004 | 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。 | `ai_management/schemas.py` |
| examples-api | done | AC-001, AC-003, AC-004, AC-005 | 工具案例能力不变；工具生成/重生成错误可跨 app rerun 稳定显示，后续成功清除旧错误。 | `ai_management/api/routers/tools.py` |
| examples-admin-api | done | AC-003, AC-004 | - | `ai_management/api/routers/admin_management.py` |
| examples-ui | done | AC-002, AC-005 | 工具案例能力不变；工具生成/重生成错误可跨 app rerun 稳定显示，后续成功清除旧错误。 | `ai_management/test_ui.py` |
| examples-tests | done | AC-001, AC-003, AC-004, AC-005, AC-006 | 工具案例能力不变；工具生成/重生成错误可跨 app rerun 稳定显示，后续成功清除旧错误。 | `tests/test_tool_examples.py`<br>`tests/test_app_routing.py`<br>`tests/test_v2_hardening.py`<br>`tests/test_cleaning_release_contract.py` |
| examples-docs | done | AC-006 | 本次发布仅涉及运行时区和清洗命名，工具会话及 DSL 业务语义不变。 | `docs/小程序后端与 AI 管理服务接口对接交互版本.md`<br>`docs/小程序后端与AI管理服务接口对接标准.md`<br>`docs/API与SSE说明.md`<br>`docs/功能与接口索引.md`<br>`docs/README.md`<br>`docs/Dify创建导入绑定与契约测试.md`<br>`docs/architecture/PROFILE.md`<br>`AI管理服务当前状态与后续测试交接.md` |

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
