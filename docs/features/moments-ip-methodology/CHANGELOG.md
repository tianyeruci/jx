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
