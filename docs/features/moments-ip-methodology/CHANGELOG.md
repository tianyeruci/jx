# 朋友圈顾问 IP 方法论 Prompt 与案例增量 — Change Log

This is a semantic change log, not a copy of Git diff.


## 2026-08-24T14:40:58+08:00 — 更新朋友圈顾问运行 Prompt 与参考 Prompt，明确用户指定类型优先、五类方法自动识别、真实性与发布前检查；默认案例仅追加 moments-feedback、moments-knowledge、moments-interaction，旧三条不变；新增 AdminSetting 增量追加回归测试。Dify/jx 隔离发布和旧数据保留仍待外部验收。

- **Purpose:** 更新朋友圈顾问运行 Prompt 与参考 Prompt，明确用户指定类型优先、五类方法自动识别、真实性与发布前检查；默认案例仅追加 moments-feedback、moments-knowledge、moments-interaction，旧三条不变；新增 AdminSetting 增量追加回归测试。Dify/jx 隔离发布和旧数据保留仍待外部验收。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
  - `examples-defaults — 朋友圈默认案例`
  - `examples-tests — 朋友圈案例回归测试`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py; uv run ruff check ai_management/services/tool_examples.py tests/test_tool_examples.py; uv run python -m compileall -q ai_management knowledge_cleaner/dify.py
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`
- `tests/test_tool_examples.py`

## 2026-08-24T21:10:00+08:00 — 撤回过宽接口兜底，恢复 request-only 兼容并补齐 SSE 路由回归

- **Purpose:** 独立复核发现空素材接口硬拒绝会截断测试页和既有案例，且静态流缺少既有 `status` 事件；按兼容性证据撤回该接口改动。
- **Before:** API 层把所有空 `source_material` 请求直接返回固定提示，测试页五类案例没有进入 Dify；新增测试只覆盖私有 generator。
- **After:** API 完全恢复原有 request-only 与有素材调用链路；素材真实性约束保留在现有 DSL/Prompt，并追加到用户消息。新增路由契约测试覆盖 request-only、有素材、Dify 调用次数、SSE `request_started → status → answer_delta → message_end` 顺序。
- **Validation:** 兼容修复前先在 jx 测试页完成五类真实生成回归，确认现有 App、qwen-plus、原 API Key 和 SSE 链路可用；测试产生的 5 个精确 Workflow 运行及关联测试用户/节点记录随后已清理。当前仍不把模型输出的“无素材不补写”视为确定性门禁，后续应以真实业务素材验证。

## 2026-08-24T19:00:00+08:00 — 按现有 App/Key 最小范围完成 Dify 发布、空素材兜底、jx 部署与回归清理

- **Purpose:** 根据用户补充说明，改为复用现有“朋友圈顾问” Dify Workflow、现有 qwen-plus、现有 API Key 和本地 `dify/dsl`，不创建新 App/Key、不改模型、不装插件；补上模型自然语言门禁不稳定时的接口级确定性兜底。
- **Before:** 仅依赖 Prompt 禁止空素材扩写；测试页五类空素材请求仍会生成未提供的时间、人物、工具、功能或步骤等细节。
- **After:** `POST /internal/v1/tools/moments/run` 在 `source_material` 为空时直接返回固定提示，不调用 Dify、不创建任务；有原始素材时保持原工作流调用。现有 Dify App 的 Workflow 已更新并发布，qwen-plus、入口变量和现有 Key 未更换。
- **Cases:** 追加 `moments-feedback`、`moments-knowledge`、`moments-interaction`；旧三条案例字段、顺序和服务端快照未删除或覆盖，部署后六条案例与本地默认值逐项一致。
- **Deployment:** jx 使用 `--keep-data` 完成发布，报告为 `/opt/ai-management/var/reports/v2/quick-20260824-184322-b9c2d813eb7a-quick-deploy`，发布前备份为 `/opt/ai-management/backups/quick-20260824-184322-b9c2d813eb7a-predeploy`；健康检查和容器切换通过。
- **Validation:** 本地源素材兜底/案例聚焦测试通过；发布后使用测试页验证生活、反馈、产品、干货、互动五类请求均返回固定素材提示，未产生新的 Dify 任务。此前失败回归产生的 12 个精确 Workflow 运行及关联测试用户/节点记录已删除；旧业务数据和正式案例未做整库清理。
- **Changed paths:**
  - `ai_management/api/routers/tools.py`
  - `ai_management/services/tool_examples.py`
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`
  - `tests/test_moments_source_guard.py`
  - `tests/test_tool_examples.py`
  - `docs/features/moments-ip-methodology/SPEC.md`
  - `docs/features/moments-ip-methodology/IMPLEMENTATION.md`

## 2026-08-24T14:52:08+08:00 — 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。

- **Purpose:** 本次补充 AdminSetting 增量旧案例保护测试，明确用户显式类型优先并保留既有管理员输出风格优先级；同时修复一个既有冻结接口文档测试的跨平台换行与当前指纹基线。朋友圈 Prompt/DSL、案例 API/SSE 契约和数据保留边界保持既定范围；Dify/jx 隔离发布与真实回归仍待外部验收。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
  - `examples-defaults — 朋友圈默认案例`
  - `examples-tests — 朋友圈案例回归测试`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py tests/test_cleaning_release_contract.py; uv run ruff check ai_management/services/tool_examples.py tests/test_tool_examples.py tests/test_cleaning_release_contract.py; uv run python -m compileall -q ai_management knowledge_cleaner/dify.py
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`
  - `tests/test_tool_examples.py`


## 2026-08-24T21:08:32+08:00 — 保留现有 Dify App、qwen-plus、API Key 和 request-only/SSE 调用链；仅在本地 Prompt/DSL 增加五类内容方法与不补写具体细节约束，新增三条案例并保留旧案例；补充真实路由契约测试、jx --keep-data 发布和精确测试数据清理记录。

- **Purpose:** 保留现有 Dify App、qwen-plus、API Key 和 request-only/SSE 调用链；仅在本地 Prompt/DSL 增加五类内容方法与不补写具体细节约束，新增三条案例并保留旧案例；补充真实路由契约测试、jx --keep-data 发布和精确测试数据清理记录。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_moments_run_contract.py tests/test_tool_examples.py
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`

## 2026-08-24T21:33:45+08:00 — 将 request 与 source_material 中逐字明确事实统一纳入可用来源，明确主题/目标/写作要求不授权补造细节；保持现有 App、qwen-plus、API Key、可选素材和 SSE 链路不变。补强真实路由契约断言，已登录 Dify UI 原地发布后再次完成五类测试页回归并按精确运行 ID 清理。

- **Purpose:** 将 request 与 source_material 中逐字明确事实统一纳入可用来源，明确主题/目标/写作要求不授权补造细节；保持现有 App、qwen-plus、API Key、可选素材和 SSE 链路不变。补强真实路由契约断言，已登录 Dify UI 原地发布后再次完成五类测试页回归并按精确运行 ID 清理。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`

## 2026-08-24T21:43:14+08:00 — 修正 request/source_material 事实来源冲突，保持现有 Dify App、qwen-plus、API Key、可选素材和 SSE 链路；新增测试锁定完整 inputs、管理员风格、现有 moments Key 映射和成功事件序列。已完成 Dify 原 App 发布、jx 测试页五类回归、精确测试记录清理及本地 canonical quality。

- **Purpose:** 修正 request/source_material 事实来源冲突，保持现有 Dify App、qwen-plus、API Key、可选素材和 SSE 链路；新增测试锁定完整 inputs、管理员风格、现有 moments Key 映射和成功事件序列。已完成 Dify 原 App 发布、jx 测试页五类回归、精确测试记录清理及本地 canonical quality。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_moments_run_contract.py tests/test_tool_examples.py
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`
  - `dify/prompts/朋友圈顾问提示词.md`

## 2026-09-08T22:12:43+08:00 — 朋友圈顾问继续使用原 Dify App 和既有 Prompt/案例链路；仅为实际生成节点声明 Memory window 6，并由共享工具路由补齐同会话消息反馈、重生成和停止的兼容接口。

- **Purpose:** 朋友圈顾问继续使用原 Dify App 和既有 Prompt/案例链路；仅为实际生成节点声明 Memory window 6，并由共享工具路由补齐同会话消息反馈、重生成和停止的兼容接口。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T22:29:59+08:00 — 朋友圈顾问的原 App、Prompt 与案例边界保持不变；Memory window 6 和同会话消息操作已完成本地契约回归，线上发布事实仍待 Release Gate 后核验。

- **Purpose:** 朋友圈顾问的原 App、Prompt 与案例边界保持不变；Memory window 6 和同会话消息操作已完成本地契约回归，线上发布事实仍待 Release Gate 后核验。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:12:15+08:00 — 朋友圈顾问保持现有 IP 方法论与本地不持久化正文边界；新增的同会话回读重生成、Dify 反馈和受限停止只扩展当前工具会话操作，不改变方法论输入或持久化模型。

- **Purpose:** 朋友圈顾问保持现有 IP 方法论与本地不持久化正文边界；新增的同会话回读重生成、Dify 反馈和受限停止只扩展当前工具会话操作，不改变方法论输入或持久化模型。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 针对性回归已通过；完整 gov quality 将在所有相关 Feature 同步后从头执行。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:15:47+08:00 — 朋友圈顾问保持现有 IP 方法论与本地不持久化正文边界；同会话回读重生成、Dify 反馈和受限停止只扩展会话操作，测试页活跃状态按入口隔离。

- **Purpose:** 朋友圈顾问保持现有 IP 方法论与本地不持久化正文边界；同会话回读重生成、Dify 反馈和受限停止只扩展会话操作，测试页活跃状态按入口隔离。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 当前完整质量门从格式阶段重新开始；Ruff 格式已修正，完整结果待执行。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:22:35+08:00 — 朋友圈顾问保持 IP 方法论和本地不持久化正文边界；同会话回读重生成、反馈、受限停止及入口隔离测试页不改变方法论输入或数据模型。

- **Purpose:** 朋友圈顾问保持 IP 方法论和本地不持久化正文边界；同会话回读重生成、反馈、受限停止及入口隔离测试页不改变方法论输入或数据模型。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 完整质量门已执行至第二单元组；冻结摘要基线修正后将从头重跑。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:44:11+08:00 — 朋友圈顾问保留既有本地无正文持久化边界，新增通过有效 Dify 会话回读消息后的反馈、重生成和停止契约，并更新接口与验收说明。

- **Purpose:** 朋友圈顾问保留既有本地无正文持久化边界，新增通过有效 Dify 会话回读消息后的反馈、重生成和停止契约，并更新接口与验收说明。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:44:24+08:00 — 朋友圈顾问保留既有本地无正文持久化边界，新增通过有效 Dify 会话回读消息后的反馈、重生成和停止契约，并更新接口与验收说明。

- **Purpose:** 朋友圈顾问保留既有本地无正文持久化边界，新增通过有效 Dify 会话回读消息后的反馈、重生成和停止契约，并更新接口与验收说明。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-08T23:58:05+08:00 — 确认朋友圈工具消息操作的正式接口正文和接口索引与当前实现一致。

- **Purpose:** 确认朋友圈工具消息操作的正式接口正文和接口索引与当前实现一致。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T00:04:09+08:00 — 工具会话跨日删除测试改为显式构造北京时间前一日，保持既有生产清理与精确 Dify 会话删除语义不变。

- **Purpose:** 工具会话跨日删除测试改为显式构造北京时间前一日，保持既有生产清理与精确 Dify 会话删除语义不变。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T00:33:39+08:00 — 朋友圈顾问工具会话的反馈、重生成与停止文档改为正文级契约，并在工具终态、时间与SSE校验收敛后同步测试说明。

- **Purpose:** 朋友圈顾问工具会话的反馈、重生成与停止文档改为正文级契约，并在工具终态、时间与SSE校验收敛后同步测试说明。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T00:48:18+08:00 — 同步朋友圈顾问的 Memory=6、同会话远端消息反馈/重生成/停止的兼容边界；不改变既有方法论或正文持久化范围。

- **Purpose:** 同步朋友圈顾问的 Memory=6、同会话远端消息反馈/重生成/停止的兼容边界；不改变既有方法论或正文持久化范围。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T00:54:12+08:00 — 同步最终文档/测试指纹；朋友圈顾问保持同一 Dify 会话、远端消息操作及 Memory=6 的已批准边界。

- **Purpose:** 同步最终文档/测试指纹；朋友圈顾问保持同一 Dify 会话、远端消息操作及 Memory=6 的已批准边界。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T02:05:00+08:00 — 商业顾问同本地ID严格替换/静默EOF failed；工具同Dify会话远端feedback+regenerate+task_started/stop终态；Streamlit accepted时序；Moments/Writing Memory窗口6；无迁移。

- **Purpose:** 商业顾问同本地ID严格替换/静默EOF failed；工具同Dify会话远端feedback+regenerate+task_started/stop终态；Streamlit accepted时序；Moments/Writing Memory窗口6；无迁移。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** 三类顾问消息操作未完成本次增量契约同步。
- **After:** 商业顾问同本地ID严格替换并将静默EOF标记failed；工具同Dify会话支持远端feedback与regenerate并返回task_started和stop终态；Streamlit按accepted时序处理；Moments/Writing Memory窗口6；无迁移。
- **Impact / Notes:** 同步共享接口路径、SSE契约、DSL约束与测试记录。
- **Validation:** 聚焦pytest、Ruff、索引确定性检查已完成。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T02:41:46+08:00 — 朋友圈/写作 canonical DSL 的实际生成节点保持 Memory window=6；共享工具 SSE/停止并发修复、接口索引和契约测试已最终同步；不改用户保留 DSL、不新增迁移。

- **Purpose:** 朋友圈/写作 canonical DSL 的实际生成节点保持 Memory window=6；共享工具 SSE/停止并发修复、接口索引和契约测试已最终同步；不改用户保留 DSL、不新增迁移。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Ruff format/check; advisor focused pytest; generate_project_index --check
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T03:18:55+08:00 — 朋友圈顾问权威 DSL 的实际生成 LLM 节点启用 Dify Memory window 6；本次只同步 canonical DSL、其契约测试和接口索引，不改用户保留 DSL 文件、知识库、App Key 或数据库。

- **Purpose:** 朋友圈顾问权威 DSL 的实际生成 LLM 节点启用 Dify Memory window 6；本次只同步 canonical DSL、其契约测试和接口索引，不改用户保留 DSL 文件、知识库、App Key 或数据库。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T03:58:23+08:00 — 朋友圈顾问权威 DSL 保持实际生成LLM节点Memory window 6；本次仅同步canonical DSL、契约测试和索引，不改用户保留DSL、知识库、App Key或数据库。

- **Purpose:** 朋友圈顾问权威 DSL 保持实际生成LLM节点Memory window 6；本次仅同步canonical DSL、契约测试和索引，不改用户保留DSL、知识库、App Key或数据库。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T05:11:20+08:00 — 朋友圈顾问的权威 DSL 仅对实际生成 LLM 启用 Memory window 6；共享工具路由新增受当前匿名用户、kind 和 Dify conversation 约束的远端消息反馈、同会话重生成、早期 task_started 和可靠 stopped/failed SSE 终态。未改工具正文持久化、App Key 或非 canonical DSL 文件。

- **Purpose:** 朋友圈顾问的权威 DSL 仅对实际生成 LLM 启用 Memory window 6；共享工具路由新增受当前匿名用户、kind 和 Dify conversation 约束的远端消息反馈、同会话重生成、早期 task_started 和可靠 stopped/failed SSE 终态。未改工具正文持久化、App Key 或非 canonical DSL 文件。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T05:55:48+08:00 — 正式共享小程序接口文档修正终态契约：message_end 仅 completed/stopped，流中失败仅 error.status=failed 并清空本轮 answer_delta；不改变朋友圈输入、会话创建或方法论生成语义。

- **Purpose:** 正式共享小程序接口文档修正终态契约：message_end 仅 completed/stopped，流中失败仅 error.status=failed 并清空本轮 answer_delta；不改变朋友圈输入、会话创建或方法论生成语义。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest tests/test_advisor_message_action_docs.py (4 passed)
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T06:06:09+08:00 — 朋友圈共享文档与测试证据同步：正式接口冻结值反映已审查 SSE 失败终态，且不改变朋友圈方法论、DSL 输入或生成语义。

- **Purpose:** 朋友圈共享文档与测试证据同步：正式接口冻结值反映已审查 SSE 失败终态，且不改变朋友圈方法论、DSL 输入或生成语义。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest test_cleaning_release_contract.py (27 passed); pytest Other/event_rag_cleaner (45 passed)
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T06:30:10+08:00 — 恢复项目级 cleaning/RAG/activity 架构事实并保留 recent-activity 的现有 ownership、record_type=event、metadata 与测试 seams；本 Feature 的业务语义、DSL 和实现不变。

- **Purpose:** 恢复项目级 cleaning/RAG/activity 架构事实并保留 recent-activity 的现有 ownership、record_type=event、metadata 与测试 seams；本 Feature 的业务语义、DSL 和实现不变。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest tests/test_advisor_message_action_docs.py (5 passed)
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T12:53:26+08:00 — 朋友圈实际生成节点启用 Dify Memory 窗口 6，以支持同会话消息重生成；方法论节点、Prompt、输入和发布绑定不变。

- **Purpose:** 朋友圈实际生成节点启用 Dify Memory 窗口 6，以支持同会话消息重生成；方法论节点、Prompt、输入和发布绑定不变。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** 仓库权威 DSL 未显式固化工具会话 Memory 窗口。
- **After:** 权威 DSL 与线上已核验状态一致，实际生成节点 Memory enabled 且 window size=6。
- **Impact / Notes:** None
- **Validation:** DSL 契约与三类顾问相关测试 194 项通过。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T13:42:34+08:00 — 朋友圈方法论 Prompt/节点不变；工具消息完成后页面稳定刷新，conversation ID 持久化提交次数改为常数级。

- **Purpose:** 朋友圈方法论 Prompt/节点不变；工具消息完成后页面稳定刷新，conversation ID 持久化提交次数改为常数级。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 工具与测试页两文件 66 项通过。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`

## 2026-09-09T13:47:38+08:00 — 方法论 Prompt 与节点不变；朋友圈工具错误状态跨 app rerun 保持可见。

- **Purpose:** 方法论 Prompt 与节点不变；朋友圈工具错误状态跨 app rerun 保持可见。
- **Affected modules:**
  - `prompt-dsl — 朋友圈 Prompt 与 DSL`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 共享测试页与工具契约 67 项通过。
- **Changed paths:**
  - `dify/dsl/moments_advisor.yml`
