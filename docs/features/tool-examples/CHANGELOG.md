# 朋友圈与日常写作可配置案例目录 — Change Log

This is a semantic change log, not a copy of Git diff.

## 2026-08-17 — 案例目录、点击生成与管理接口

- Before: 朋友圈顾问和日常写作只有手动输入，没有可查看的完整提示词案例、案例读取接口或管理员维护能力。
- After: 两个工具各提供 3 个默认案例；用户可查看完整 `request` 并点击后立即复用既有 `/run`、SSE 和 stop 链路；管理员可查询、新增、编辑、启停、删除和恢复缺失默认案例。配置复用按 kind 隔离的 `AdminSetting` JSON，不新增迁移。
- Documentation: 同步接口交互版本、正式接口标准、API/SSE 说明、功能索引、README 功能矩阵、Dify 运行时说明、架构 Profile 和根目录状态交接；DSL 未变更，jx 发布尚未执行。
- Validation: `gov quality` passed；专项案例/路由/输入契约测试、文档索引检查、format/lint/compile passed。canonical 全套测试仍记录 2 个缺失清理脚本和 4 个缺失咨询样本的既有基线失败，未伪造资料。

## 2026-08-17 — jx 发布与远端回归

- Before: jx 尚未部署本 Feature；发布包使用 Windows `Compress-Archive` 时未暴露服务器 Python 3.12 的反斜杠路径兼容问题，服务器 `knowledge_cleaner` 源码也落后于现有 wheelhouse。
- After: 使用显式 POSIX ZIP 路径包发布到 jx release `quick-20260817-081805-b051c4d3304f`；备份远端旧 `knowledge_cleaner` 后从现有 wheelhouse 对齐，最终 Core/UI/Worker/Scheduler 与 UI、API 均通过发布检查。未选择 clean-data，未修改 Dify DSL。
- Validation: jx `health/ready=200`、测试页 `HTTP 200`、用户/管理员案例 GET 均返回 3 个默认案例；临时管理员 CRUD（创建、修改、启用、删除）通过且无残留；OCR timer 为 `enabled/active`。问题记录见 `var/reports/20260817-tool-examples/issues.jsonl`。

## 2026-08-17 — 独立复核修正

- Before: 正式接口标准中的管理员案例路径与实际路由不一致；案例保存排序可能改变相同排序值的创建顺序；质量命令和 Feature 所有权没有覆盖本 Feature 的全部关键路径；旧部署 ZIP 不含新服务模块。
- After: 正式标准统一为 `/internal/v1/admin/tools/{kind}/examples...` 并补齐 `enabled`；保存保持插入顺序并增加同排序回归测试；项目质量命令、Feature 验证和文档所有权纳入案例模块；首次配置写入增加 SQLite/PostgreSQL 冲突安全处理和 SQLite 瞬时锁重试；发布前要求从当前源码重建 ZIP。
- Documentation: 同步 SPEC/IMPLEMENTATION 审批状态、正式接口标准、项目目录结构、架构/质量边界和状态交接；DSL 仍未变更。
- Validation: 聚焦案例/路由/既有契约测试已通过；完整质量门禁和 Streamlit 本地运行 smoke 已通过；本地 Dify 不可用导致的 502 仅记录为外部依赖限制。


## 2026-08-17T03:37:12+08:00 — 正式接口标准统一为实际 admin tools examples 路由并补齐 enabled；案例保存保持插入顺序并增加同排序回归；首次 AdminSetting 写入采用 SQLite/PostgreSQL 冲突安全处理；项目质量命令、Feature 验证和文档所有权覆盖案例模块；旧 ZIP 不作为发布包。

- **Purpose:** 正式接口标准统一为实际 admin tools examples 路由并补齐 enabled；案例保存保持插入顺序并增加同排序回归；首次 AdminSetting 写入采用 SQLite/PostgreSQL 冲突安全处理；项目质量命令、Feature 验证和文档所有权覆盖案例模块；旧 ZIP 不作为发布包。
- **Affected modules:**
  - N/A
- **Before:** 接口标准存在管理员路径偏差，排序稳定性和首次并发写缺少明确实现，质量与所有权未覆盖全部案例路径。
- **After:** 实现、测试、正式标准、治理质量命令和文档归属一致；发布前将从当前源码重建并检查 ZIP，DSL 仍无需变更。
- **Impact / Notes:** 内部令牌边界、AdminSetting JSON、无迁移、空目录手输和既有 run/SSE/stop 链路不变。
- **Validation:** 案例/路由/既有契约聚焦测试通过；gov quality 和 Streamlit 本地 smoke 待修正后重跑。
- **Changed paths:**
  - N/A

## 2026-08-17T04:01:15+08:00 — 补充 SQLite 瞬时锁重试及回归测试；记录 moments/writing 本地 Streamlit smoke、空目录手动输入和 Dify 502 外部依赖限制；临时发布归档已验证含新模块，根目录正式 ZIP 待 release gate 后替换。

- **Purpose:** 补充 SQLite 瞬时锁重试及回归测试；记录 moments/writing 本地 Streamlit smoke、空目录手动输入和 Dify 502 外部依赖限制；临时发布归档已验证含新模块，根目录正式 ZIP 待 release gate 后替换。
- **Affected modules:**
  - N/A
- **Before:** 质量/文档指纹在本地 UI smoke 后失效，SQLite 锁竞争仍可能未处理，根目录 ZIP 仍为旧包。
- **After:** 当前实现、测试、文档和治理记录已同步；SQLite 瞬时锁可重试，UI smoke 有证据，正式 ZIP 将在最终 gate 前从源码重建并验证。
- **Impact / Notes:** 无 DSL 语义变更；接口/存储/鉴权/空目录行为不变；不将 Dify 502 误判为代码故障。
- **Validation:** 专项案例测试通过；gov quality 上一轮已通过，最终锁重试变更后需重跑；临时 ZIP 解压导入通过；Streamlit smoke 已完成。
- **Changed paths:**
  - N/A

## 2026-08-17T04:05:40+08:00 — 同步 SQLite 锁重试实现、回归测试和最终本地质量指纹；现有接口、排序、UI、DSL 与发布边界无新增语义变化。

- **Purpose:** 同步 SQLite 锁重试实现、回归测试和最终本地质量指纹；现有接口、排序、UI、DSL 与发布边界无新增语义变化。
- **Affected modules:**
  - N/A
- **Before:** 锁重试实现的最后导入整理发生在上一轮文档同步之后。
- **After:** 案例服务、回归测试、CHANGELOG、当前状态和治理指纹重新一致；根目录正式 ZIP仍待 release gate 后替换。
- **Impact / Notes:** Dify 502 仅为本地外部依赖不可用；Streamlit smoke 证据已保留；无 DSL 更新。
- **Validation:** gov quality 7ca9889... passed；专项测试、索引、包导入和 Streamlit smoke 已通过；canonical 既有 6 项基线失败仍单独记录。
- **Changed paths:**
  - N/A

## 2026-08-17T04:08:02+08:00 — 记录根目录 ai_management.zip 已从当前源码重建并完成 58/58 文件匹配、解压导入检查；旧包已备份，jx 尚未上传，等待最终 release gate。

- **Purpose:** 记录根目录 ai_management.zip 已从当前源码重建并完成 58/58 文件匹配、解压导入检查；旧包已备份，jx 尚未上传，等待最终 release gate。
- **Affected modules:**
  - N/A
- **Before:** 最终发布 ZIP 替换后当前状态文档未记录归档证据，治理实现指纹因此需要重新同步。
- **After:** 当前状态、案例 CHANGELOG 和治理文档记录了最终归档证据；业务实现、接口、DSL 和 UI 行为无新增变化。
- **Impact / Notes:** 不含密钥和真实数据；Dify DSL 未变更；正式 jx 发布仍待 release gate。
- **Validation:** 根 ZIP 58/58 Python 文件匹配、关键模块存在、解压导入通过；gov quality 将在本次同步后重跑。
- **Changed paths:**
  - N/A

## 2026-08-17T08:38:42+08:00 — 案例目录已部署到 jx；修复 Windows ZIP 反斜杠兼容和服务器 knowledge_cleaner 源码与 wheelhouse 漂移；DSL 未变更。

- **Purpose:** 案例目录已部署到 jx；修复 Windows ZIP 反斜杠兼容和服务器 knowledge_cleaner 源码与 wheelhouse 漂移；DSL 未变更。
- **Affected modules:**
  - N/A
- **Before:** 交接文档只记录本地实现和发布前状态，未记录 jx 发布结果。
- **After:** 交接文档与变更日志记录最终 release、备份、问题修复、health/UI/API/CRUD/OCR 回归和无 Dify 发布。
- **Impact / Notes:** 发布采用 keep-data 和进程级 JX_OCR_DEPLOYMENT_ENABLED=true，未写入该开关到远端 .env；canonical 6 项既有基线失败保持单独记录。
- **Validation:** 远端发布报告；docker compose ps；health/ready=200；测试页 HTTP 200；用户/管理员案例 GET；临时 CRUD 烟测；OCR timer enabled/active。
- **Changed paths:**
  - N/A

## 2026-08-18T03:30:49+08:00 — 案例目录服务与对应回归测试的当前工作树变更已与现有 SPEC、IMPLEMENTATION 和 API/索引文档语义一致；本次不新增案例目录接口、字段、DSL 或发布边界。

- **Purpose:** 案例目录服务与对应回归测试的当前工作树变更已与现有 SPEC、IMPLEMENTATION 和 API/索引文档语义一致；本次不新增案例目录接口、字段、DSL 或发布边界。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
- **Before:** tool-examples 文档记录停留在上一轮同步，治理状态对服务和测试路径标记 stale。
- **After:** 案例目录文档、CHANGELOG 与治理指纹重新与当前工作树一致；本次 recent_activity 修复不改变案例目录语义。
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_tool_examples.py tests/test_app_routing.py tests/test_v2_hardening.py; uv run python scripts/generate_project_index.py --check
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T03:50:01+08:00 — 案例目录当前实现与既有 SPEC、IMPLEMENTATION 和接口文档语义一致；本次只同步索引生成区影响的治理指纹，不新增案例目录语义。

- **Purpose:** 案例目录当前实现与既有 SPEC、IMPLEMENTATION 和接口文档语义一致；本次只同步索引生成区影响的治理指纹，不新增案例目录语义。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
- **Before:** 案例目录文档因共享功能索引生成区变化再次标记 stale。
- **After:** 案例目录治理记录与共享索引当前内容一致；recent_activity 的清洗发布改动不属于案例目录。
- **Impact / Notes:** None
- **Validation:** uv run python scripts/generate_project_index.py --check
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T04:08:31+08:00 — 案例目录当前实现、测试与既有 SPEC/IMPLEMENTATION 语义一致；本次只同步共享功能索引变更引起的治理状态，不新增案例目录语义。

- **Purpose:** 案例目录当前实现、测试与既有 SPEC/IMPLEMENTATION 语义一致；本次只同步共享功能索引变更引起的治理状态，不新增案例目录语义。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
- **Before:** 共享索引 WORKERS 生成区更新后，案例目录 feature 的 docs 状态再次标记 stale。
- **After:** 案例目录治理文档与共享索引当前内容一致；recent_activity 清洗发布变更不影响案例目录。
- **Impact / Notes:** None
- **Validation:** uv run python scripts/generate_project_index.py --check
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T04:13:03+08:00 — 案例目录无语义变化；只同步最终 ai_management.zip 和共享索引产生的相关治理指纹，案例 SPEC、IMPLEMENTATION、接口和 DSL 边界保持不变。

- **Purpose:** 案例目录无语义变化；只同步最终 ai_management.zip 和共享索引产生的相关治理指纹，案例 SPEC、IMPLEMENTATION、接口和 DSL 边界保持不变。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
- **Before:** 最终发布制品更新后，案例目录 feature 的共享 docs 状态再次 stale。
- **After:** 案例目录治理状态与共享发布制品/索引一致；本次发布实际变更仍仅为 recent_activity。
- **Impact / Notes:** None
- **Validation:** uv run python scripts/generate_project_index.py --check
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T04:33:59+08:00 — 本轮最近活动实现和 jx 回归未改变 tool-examples 语义；tool-examples 保持既有案例目录、接口和 DSL 未变更。

- **Purpose:** 本轮最近活动实现和 jx 回归未改变 tool-examples 语义；tool-examples 保持既有案例目录、接口和 DSL 未变更。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T04:50:30+08:00 — 本轮版本摘要与 recent_activity 清洗修复未改变 tool-examples 语义；案例目录、接口和 DSL 保持不变。

- **Purpose:** 本轮版本摘要与 recent_activity 清洗修复未改变 tool-examples 语义；案例目录、接口和 DSL 保持不变。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T05:01:21+08:00 — 本轮 recent_activity 版本摘要和状态说明修复未改变 tool-examples；案例目录和 DSL 保持不变。

- **Purpose:** 本轮 recent_activity 版本摘要和状态说明修复未改变 tool-examples；案例目录和 DSL 保持不变。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T05:14:47+08:00 — 共享交接文档已新增 recent_activity 的最终发布、回归和精确清理记录；tool-examples 功能语义未变更。

- **Purpose:** 共享交接文档已新增 recent_activity 的最终发布、回归和精确清理记录；tool-examples 功能语义未变更。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T05:18:45+08:00 — 共享交接文档已同步 recent_activity 最终质量指纹和发布事实；tool-examples 功能语义未变更。

- **Purpose:** 共享交接文档已同步 recent_activity 最终质量指纹和发布事实；tool-examples 功能语义未变更。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T05:22:38+08:00 — 共享交接文档已同步 recent_activity 最终质量、发布、回归和精确清理事实；tool-examples 功能语义未变更。

- **Purpose:** 共享交接文档已同步 recent_activity 最终质量、发布、回归和精确清理事实；tool-examples 功能语义未变更。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T08:17:31+08:00 — 本工作周期涉及的共享源码与测试变更未改变 tool-examples 的目录语义、接口或 DSL；其现有实现保持不变。

- **Purpose:** 本工作周期涉及的共享源码与测试变更未改变 tool-examples 的目录语义、接口或 DSL；其现有实现保持不变。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** tool-examples 当前状态存在未同步变更路径。
- **After:** tool-examples 当前状态与既有语义保持一致，相关变更已记录。
- **Impact / Notes:** 本次 qwen-batch-cleaning 实现未修改 tool-examples 专属模块。
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py；ruff check
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T09:01:13+08:00 — 本次改动未改变工具案例目录接口、UI、DSL 或案例验收语义。

- **Purpose:** 本次改动未改变工具案例目录接口、UI、DSL 或案例验收语义。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T09:01:23+08:00 — 本次改动未改变工具案例目录接口、UI、DSL 或案例验收语义。

- **Purpose:** 本次改动未改变工具案例目录接口、UI、DSL 或案例验收语义。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T11:35:24+08:00 — 本次未改变工具案例目录接口、UI、DSL、数据边界或验收语义；仅同步共享索引和当前状态。

- **Purpose:** 本次未改变工具案例目录接口、UI、DSL、数据边界或验收语义；仅同步共享索引和当前状态。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T12:58:07+08:00 — 本次未修改工具案例目录实现、测试或 DSL；现有 tool-examples 文档与代码保持一致。

- **Purpose:** 本次未修改工具案例目录实现、测试或 DSL；现有 tool-examples 文档与代码保持一致。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-18T13:09:22+08:00 — 本次未修改工具案例目录实现、测试或 DSL；工具案例 Feature 继续保持现有接口、发布和回归状态记录。

- **Purpose:** 本次未修改工具案例目录实现、测试或 DSL；工具案例 Feature 继续保持现有接口、发布和回归状态记录。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_tool_examples.py`

## 2026-08-19T14:19:23+08:00 — 核对工具案例代码、测试和当前部署交接；本次课程写入关闭与咨询 PDF OCR 门禁修复不改变 tool-examples 的业务语义。

- **Purpose:** 核对工具案例代码、测试和当前部署交接；本次课程写入关闭与咨询 PDF OCR 门禁修复不改变 tool-examples 的业务语义。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
- **Before:** 工具案例 Feature 文档未完成当前工作树治理周期同步。
- **After:** 工具案例 Feature 文档和变更记录已与当前代码/质量证据同步，明确本次无语义影响。
- **Impact / Notes:** 仅同步治理记录，不修改工具案例实现或 DSL。
- **Validation:** gov quality 通过；相关仓库质量命令通过。
- **Changed paths:**
  - `ai_management/services/tool_examples.py`
  - `tests/test_app_routing.py`
  - `tests/test_tool_examples.py`

## 2026-08-19T14:43:25+08:00 — 本次文档清洗修复未改变 tool-examples 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。

- **Purpose:** 本次文档清洗修复未改变 tool-examples 语义、实现边界或 DSL；仅同步共享工作树的治理指纹。
- **Affected modules:**
  - `examples-service — 案例目录服务与默认数据`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** tool-examples 当前文档状态与共享工作树治理指纹未同步。
- **After:** tool-examples 文档标记为无语义变化，治理指纹已同步。
- **Impact / Notes:** 无语义变化；无 DSL 变化。
- **Validation:** gov quality; documentation contract
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/services/tool_examples.py`
  - `tests/test_app_routing.py`
  - `tests/test_tool_examples.py`

## 2026-08-20T16:00:30+08:00 — Shared cleaning publication path now uses contract-scoped dedup locking and preserves existing tool-example publication semantics.

- **Purpose:** Shared cleaning publication path now uses contract-scoped dedup locking and preserves existing tool-example publication semantics.
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** full local pytest and app routing regression
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T16:57:57+08:00 — Shared publication path remains compatible with tool-example semantics while adding contract-scoped dedup locking; app-routing and full quality checks pass.

- **Purpose:** Shared publication path remains compatible with tool-example semantics while adding contract-scoped dedup locking; app-routing and full quality checks pass.
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** app-routing/full local regression
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T21:17:59+08:00 — 本轮共享交接文件变化未新增 tool-examples 产品语义；案例目录行为和接口契约保持不变，文档仅完成当前质量/索引证据同步。

- **Purpose:** 本轮共享交接文件变化未新增 tool-examples 产品语义；案例目录行为和接口契约保持不变，文档仅完成当前质量/索引证据同步。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享交接文件变化后，tool-examples 的当前 Feature 文档尚未完成本轮同步。
- **After:** tool-examples 文档已确认与代码和交接现状一致；当前 gov quality 通过。
- **Impact / Notes:** 不改变案例目录 API、Dify DSL、测试数据或 jx 配置。
- **Validation:** gov quality；案例目录与路由契约测试；项目索引检查
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T21:54:11+08:00 — 同步共享工具/清洗入口对本轮发布一致性修复的影响：复核动作仅允许 review_required 且使用行锁，远端排除建立精确补偿任务，跨批次与历史分片去重按最终发布正文统一比较，审核材料原子写入。

- **Purpose:** 同步共享工具/清洗入口对本轮发布一致性修复的影响：复核动作仅允许 review_required 且使用行锁，远端排除建立精确补偿任务，跨批次与历史分片去重按最终发布正文统一比较，审核材料原子写入。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享模块当前状态未反映本轮一致性修复。
- **After:** 共享模块当前状态已反映复核、去重、元数据和审核材料写入修复；公共工具接口未改变。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 0584e6b07bb577cad5d6b21dd71cd1c9e1cc4cb9d03aaac0d7bdfd5f9feb2c54
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T22:01:45+08:00 — 同步共享工具/清洗入口的审核材料安全修复：审核 JSON 使用锁和原子替换、0600 权限及 16 MiB 单文件大小上限；公共工具接口未改变。

- **Purpose:** 同步共享工具/清洗入口的审核材料安全修复：审核 JSON 使用锁和原子替换、0600 权限及 16 MiB 单文件大小上限；公共工具接口未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享模块状态未反映审核材料新增大小上限。
- **After:** 共享模块已记录审核材料锁、原子写、权限和大小上限。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** previous gov quality code checks passed; final quality rerun pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T22:14:00+08:00 — 同步共享工具/清洗入口的最终修复：审核复核、远端补偿、历史最终正文去重和分片命名语义已统一；审核材料保留原始模型 payload，使用原子写、目录 0700、文件 0600 与 16 MiB 上限；公共工具接口未改变。

- **Purpose:** 同步共享工具/清洗入口的最终修复：审核复核、远端补偿、历史最终正文去重和分片命名语义已统一；审核材料保留原始模型 payload，使用原子写、目录 0700、文件 0600 与 16 MiB 上限；公共工具接口未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享模块状态未反映最终审核 artifact 安全与历史分片命名修复。
- **After:** 共享模块已记录最终审核、补偿、去重、命名与审核材料安全语义。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** final gov quality rerun pending after index regeneration
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T22:27:23+08:00 — 同步当前交接文档中的最终质量指纹、本地 wheel 校验和审核材料安全边界；未改变 tool-examples 公共接口或功能语义。

- **Purpose:** 同步当前交接文档中的最终质量指纹、本地 wheel 校验和审核材料安全边界；未改变 tool-examples 公共接口或功能语义。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 交接文档仍引用上一轮质量指纹和 wheel 摘要。
- **After:** 交接文档已记录最终质量指纹、wheel SHA、原始模型 payload 保留、权限和大小边界，并明确未发布 jx。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 7f196f54aa0fa2d8b2562b699ba3e7283e9908a8a70a35f7263150c3c642680f
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T22:38:40+08:00 — 同步当前交接文档的最终状态：Batch cancelling 轮询、取消/提交锁与孤儿 Batch 补偿、审核 artifact 安全创建、质量指纹和 wheel 校验已更新；tool-examples 公共接口未改变。

- **Purpose:** 同步当前交接文档的最终状态：Batch cancelling 轮询、取消/提交锁与孤儿 Batch 补偿、审核 artifact 安全创建、质量指纹和 wheel 校验已更新；tool-examples 公共接口未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 交接文档未记录最新 Batch 生命周期和安全创建修复。
- **After:** 交接文档已记录最终质量、wheel、Batch 取消边界与审核 artifact 安全边界，并明确 jx 未发布。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 6222e8c21dc8464c014a02c4facc4b25f4d06bd9f608643ec1cada2ed5c17965
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T22:47:25+08:00 — 仅同步动态质量证据，代码与远端范围不变

- **Purpose:** 仅同步动态质量证据，代码与远端范围不变
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T23:12:34+08:00 — Handover status records cancellation locks, recovery cleanup, request fingerprint, and focused verification

- **Purpose:** Handover status records cancellation locks, recovery cleanup, request fingerprint, and focused verification
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T23:22:18+08:00 — Handover points to the latest quality state artifact and records the final local verification boundary

- **Purpose:** Handover points to the latest quality state artifact and records the final local verification boundary
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T23:34:40+08:00 — Handover records final recovery lock and provider-failure request fingerprint fixes

- **Purpose:** Handover records final recovery lock and provider-failure request fingerprint fixes
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T23:50:56+08:00 — Handover records repair Batch upload/create cancellation rechecks and valid validating state

- **Purpose:** Handover records repair Batch upload/create cancellation rechecks and valid validating state
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T00:02:45+08:00 — Generated HTTP/WORKERS index and handover record final validating cancellation and bailian batch retry behavior

- **Purpose:** Generated HTTP/WORKERS index and handover record final validating cancellation and bailian batch retry behavior
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T00:12:45+08:00 — Generated WORKERS index and final handover watchdog/batch retry evidence are synchronized

- **Purpose:** Generated WORKERS index and final handover watchdog/batch retry evidence are synchronized
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T00:22:57+08:00 — Generated WORKERS index and final artifact failure/review convergence evidence are synchronized

- **Purpose:** Generated WORKERS index and final artifact failure/review convergence evidence are synchronized
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T00:39:12+08:00 — 仅追加当前任务的脱敏状态与验证边界，不改变 tool-examples 产品语义。

- **Purpose:** 仅追加当前任务的脱敏状态与验证边界，不改变 tool-examples 产品语义。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 交接文档未记录本轮接管后的并发清理补修。
- **After:** 交接文档追加补修范围、验证结果和未执行远端动作边界。
- **Impact / Notes:** None
- **Validation:** 聚焦模型清洗测试、Ruff、compileall、git diff --check
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T00:50:09+08:00 — 追加提交失败清理状态保持的脱敏交接记录，不改变 tool-examples 产品语义。

- **Purpose:** 追加提交失败清理状态保持的脱敏交接记录，不改变 tool-examples 产品语义。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 交接文档未记录本轮最新补修。
- **After:** 交接文档记录补修范围、验证结果和未执行远端动作边界。
- **Impact / Notes:** None
- **Validation:** 模型清洗聚焦测试、Ruff、compileall、git diff --check
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T01:00:43+08:00 — 仅追加脱敏的 Batch 生命周期、审批幂等和验证边界交接，不改变 tool-examples 产品语义。

- **Purpose:** 仅追加脱敏的 Batch 生命周期、审批幂等和验证边界交接，不改变 tool-examples 产品语义。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 交接文档未记录最新独立复核修复。
- **After:** 交接文档记录当前修复、测试和未执行远端动作边界。
- **Impact / Notes:** None
- **Validation:** 模型清洗与人工管理聚焦 pytest、Ruff、compileall、git diff --check
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T01:18:41+08:00 — 共享模型 Batch ID 兼容读取和质量命令覆盖已补齐，工具示例共享路径保持当前实现。

- **Purpose:** 共享模型 Batch ID 兼容读取和质量命令覆盖已补齐，工具示例共享路径保持当前实现。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享路径变更后的功能文档状态未同步。
- **After:** 共享路径与当前验证状态已同步。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；gov quality
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T01:40:09+08:00 — 共享清洗路径的看门狗远端状态对账和质量覆盖已同步，工具示例路径保持不变。

- **Purpose:** 共享清洗路径的看门狗远端状态对账和质量覆盖已同步，工具示例路径保持不变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享路径修复后的功能文档状态未同步。
- **After:** 共享路径和当前验证状态已同步。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；gov quality
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T01:50:12+08:00 — 共享清洗路径的看门狗终态对账已完成，工具示例路径保持现有契约；全量门禁通过。

- **Purpose:** 共享清洗路径的看门狗终态对账已完成，工具示例路径保持现有契约；全量门禁通过。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享路径最终修复状态未同步。
- **After:** 共享路径与最终验证状态已同步。
- **Impact / Notes:** None
- **Validation:** gov quality
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T02:08:26+08:00 — 共享清洗路径已统一 stale 任务恢复到既有任务入口，工具示例路径不变。

- **Purpose:** 共享清洗路径已统一 stale 任务恢复到既有任务入口，工具示例路径不变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** 共享路径恢复语义未同步。
- **After:** 共享路径恢复语义和验证状态已同步。
- **Impact / Notes:** None
- **Validation:** gov quality
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T02:43:32+08:00 — 共享模型任务现在保留终态 Batch 映射并在本地结果提交后清理供应商文件；tool-examples 业务案例、接口和 UI 语义未改变。

- **Purpose:** 共享模型任务现在保留终态 Batch 映射并在本地结果提交后清理供应商文件；tool-examples 业务案例、接口和 UI 语义未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_tool_examples.py tests/test_model_cleaning_workflow.py; powershell -NoProfile -ExecutionPolicy Bypass -File .codex/governance/gov.ps1 quality
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T03:00:33+08:00 — 共享 Batch 轮询、终态结果持久化、修复批次切换和 cancelling 清理状态逻辑已同步；旧目标数据按精确清单删除且不备份，后续只处理新上传数据。

- **Purpose:** 共享 Batch 轮询、终态结果持久化、修复批次切换和 cancelling 清理状态逻辑已同步；旧目标数据按精确清单删除且不备份，后续只处理新上传数据。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning workflow tests passed; canonical quality pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T03:07:07+08:00 — 共享 Batch 终态结果先完成本地收敛再清理远端文件，失败时保留恢复信息。

- **Purpose:** 共享 Batch 终态结果先完成本地收敛再清理远端文件，失败时保留恢复信息。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning workflow tests passed; canonical quality pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T03:33:23+08:00 — 共享 Bailian 清理 work item、取消状态与公平补偿逻辑已同步；工具案例业务语义未改变。

- **Purpose:** 共享 Bailian 清理 work item、取消状态与公平补偿逻辑已同步；工具案例业务语义未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning and cleanup lifecycle regressions passed; canonical quality pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-21T03:37:45+08:00 — 共享清理生命周期在取消异常时保留精确远端引用并可由 maintenance 重建；工具案例业务语义未改变。

- **Purpose:** 共享清理生命周期在取消异常时保留精确远端引用并可由 maintenance 重建；工具案例业务语义未改变。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused cleanup lifecycle regressions passed; canonical quality pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-24T21:34:06+08:00 — 新增 feedback、knowledge、interaction 三条朋友圈案例，保留旧案例和排序；通过本地案例回归及 jx 测试页五类真实生成回归后，按精确 Dify Workflow 运行记录完成测试数据清理。

- **Purpose:** 新增 feedback、knowledge、interaction 三条朋友圈案例，保留旧案例和排序；通过本地案例回归及 jx 测试页五类真实生成回归后，按精确 Dify Workflow 运行记录完成测试数据清理。
- **Affected modules:**
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-30T16:04:32+08:00 — 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。

- **Purpose:** 工具会话 run 顶层 conversation_id、reset 后重新选型、并发安全和精确迁移兼容已发布到 jx；测试页验证朋友圈与日常写作 SSE/重置链路，商业顾问隔离不变。
- **Affected modules:**
  - `examples-contracts — 案例请求响应契约`
  - `examples-api — 用户读取与管理员 CRUD API`
  - `examples-ui — 测试 UI 案例展示与维护`
  - `examples-tests — 案例与路由回归测试`
  - `examples-docs — 接口、索引与状态交接文档`
- **Before:** jx 仍运行 2026-08-26 工具会话版本，最新契约修复未发布。
- **After:** jx release toolconv-20260830-155349-contractfix2 已成功，迁移 head n0e1f2g3h4i5，测试页关键链路通过。
- **Impact / Notes:** None
- **Validation:** gov quality；Chrome 测试页朋友圈/日常写作会话验收；jx ready
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/tools.py`
  - `ai_management/schemas.py`
  - `ai_management/test_ui.py`
  - `tests/test_app_routing.py`
  - `tests/test_cleaning_release_contract.py`
  - `tests/test_v2_hardening.py`
