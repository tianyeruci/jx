# 千问 Batch 清洗策略验证与轮询修复 — Change Log

This is a semantic change log, not a copy of Git diff.

## 2026-08-22T10:04:52+08:00 — jx 远端已备份并启用千问 Batch，核心服务完成强制重建

- **Purpose:** 按用户确认把本地策略落实到 jx，并保留可定位的配置回滚点。
- **Before:** jx 为 `BAILIAN_BATCH_ENABLED=false`、`MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=500`；相关服务尚未读取本轮目标配置。
- **After:** jx `.env` 已备份到 `/opt/ai-management/backups/20260822-100452-qwen-batch-upload-config`，运行容器回读为 `BAILIAN_BATCH_ENABLED=true`、`MAX_UPLOAD_FILE_MB=200`、`MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=200`、候选/Token 阈值 `100/200000`、Provider 分片 `40000/419430400`。API、普通 Worker、Batch Worker、realtime Worker、publication Worker、Scheduler 已使用同一镜像完成 `--force-recreate`。
- **Impact / Notes:** 只修改 `.env`，未修改数据库、Redis、Dify 数据、上传卷或 OCR 服务；Nginx `client_max_body_size=128M` 未修改，所以经 Nginx 的 HTTP 上传路径仍受 128M 中间层限制。Compose 报告 orphan containers warning，但本次未执行 `--remove-orphans` 或删除任何容器。
- **Validation:** API `/health/ready` 为 `ready` 且 database/redis/dify 均为 `true`；目标服务重建后 `restart=0`、关键日志错误计数为 0；Nginx `/ai-management-test/` 返回 HTTP 200。未发起真实百炼 Batch，也未用新样本验证 100 文件/200MB 边界。

## 2026-08-22T00:30:00+08:00 — jx Batch 运行配置已回读，最近活动保持 realtime

- **Purpose:** 按本轮运行配置要求复核 jx 的千问 Batch 开关、相关 Worker 重建状态和策略模式选择。
- **Before:** 上一条记录仅保留 jx 的待变更目标：`BAILIAN_BATCH_ENABLED=true`、`MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=200`，远端写入和重建未形成当前运行态证据。
- **After:** jx `.env` 与 API/Worker/Batch Worker/realtime Worker/publication Worker/Scheduler 容器均回读 `BAILIAN_BATCH_ENABLED=true`、候选/Token 阈值 `100/200000`、上传门禁 `100/200MB`；同源核心镜像服务均为 Up，API readiness 为 `ready` 且 database/redis/dify 均可达。
- **Validation:** Batch Worker 容器内 `choose_call_mode(..., batch_enabled=true)` 在候选数 100 下返回：`recent_activity=realtime`；个人商业咨询、微信公众号、知识星球、小报童为 `batch`。本次未发起真实 Provider Batch 或新样本清洗；本地 Compose 因缺少外部 `docker_default` 网络未启动。

## 2026-08-22T00:00:00+08:00 — 本地与 jx 千问 Batch 目标采用 100 文件/200MB 上传门禁

- **Purpose:** 固定本地与 jx 使用百炼 Batch 的目标运行配置，并明确上传入口门禁与 Provider Batch JSONL 分片上限不是同一层限制。
- **Before:** 本地 `.env` 为 `MAX_UPLOAD_FILES=10`、`MAX_UPLOAD_BATCH_MB=500`，未显式设置 `BAILIAN_BATCH_ENABLED`；jx 当前为 `MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=500`、`BAILIAN_BATCH_ENABLED=false`；生产/代码默认仍为 Batch 关闭、10 文件/500MB。
- **After:** 本地 `.env` 已设置 `BAILIAN_BATCH_ENABLED=true`、候选/Token 阈值 100/200000、单文件 200MB、单批 100 文件/200MB。jx 目标为同样的 Batch 开关与上传门禁；`CLEANING_BATCH_MAX_BYTES` 保持默认 400MiB，由 Worker 负责拆分 Provider Batch JSONL。
- **Impact / Notes:** jx 只读预检已通过，远端 `.env` 修改、备份、服务重建和容器内回读待精确命令确认后执行；不改变代码默认、`.env.example`、生产基线、`recent_activity` realtime 边界或任何业务数据。配置生效必须重建使用 `env_file` 的 Compose 服务，普通 restart 不足以证明读取了新值。
- **Validation:** 本地 Settings 覆盖检查通过；jx 核实同源核心服务、Dify `docker_default` 网络和价格快照均存在；远端写入尚未执行。

## 2026-08-18T12:50:00+08:00 — 用户取消远端异步验收并完成精确回收

- **Purpose:** 按用户指令停止未完成的真实 Batch 验收，取消活动供应商任务，恢复默认配置并清除本轮生成数据。
- **Before:** jx 临时开启 Batch 与阈值 1；9 个 general 测试批次已创建，4 个供应商 Batch 仍在处理中。
- **After:** 4 个活动供应商任务已取消；9 个 general 清洗批次、4 个 general Dify 文档和精确 Redis 清洗键已按签名 manifest 删除；`BAILIAN_BATCH_ENABLED=false` 已恢复，临时阈值未保留；课程/路线数据未变更。
- **Impact / Notes:** 用户在 11 个样本全部终态前取消本轮，因此不把本轮标记为完整样本验收。脱敏问题记录见 `var/reports/20260818-qwen-batch-cleaning/issues.jsonl`；DSL 未变更。
- **Validation:** jx `health/ready` ready；general 清洗批次、Dify 文档和清洗 Redis 键均为 0；课程 126、路线 5、方向 0 保持不变。


## 2026-08-18T08:16:53+08:00 — BAILIAN_BATCH_ENABLED=true 且候选数或输入 Token 达到阈值时，personal_business_consultation、wechat_public_account_cognition、zsxq_post、xiaobot_post 与既有课程目录策略一样进入 Batch；默认关闭或未达阈值仍为 realtime。轮询只对仍有 provider_batch_ids 的 batch_running 任务查询和重排，终态、无活动 Batch、结果完成或失败不再重排，临时异常在仍活动时重试，修复 Batch 继续轮询。

- **Purpose:** BAILIAN_BATCH_ENABLED=true 且候选数或输入 Token 达到阈值时，personal_business_consultation、wechat_public_account_cognition、zsxq_post、xiaobot_post 与既有课程目录策略一样进入 Batch；默认关闭或未达阈值仍为 realtime。轮询只对仍有 provider_batch_ids 的 batch_running 任务查询和重排，终态、无活动 Batch、结果完成或失败不再重排，临时异常在仍活动时重试，修复 Batch 继续轮询。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
- **Before:** 内容策略被 choose_call_mode 强制 realtime；轮询异常兜底无条件重排。
- **After:** 四个目标内容策略遵循显式开关与阈值；轮询异常兜底先核验任务仍运行且存在活动供应商 Batch。
- **Impact / Notes:** 未修改默认配置、公开 API、schema、Dify DSL 或容器编排。
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py；compileall；ruff check
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-18T09:00:52+08:00 — 四个指定内容策略在 Batch 开关和阈值命中时使用百炼 Batch；轮询只由 active provider Batch 任务调度；终态结果解析、重复 custom_id、修复重试和 provider 资源清理均具备可观测终态；补偿数量达到 100 时进入修复 Batch，并持久化新 Batch 标识。

- **Purpose:** 四个指定内容策略在 Batch 开关和阈值命中时使用百炼 Batch；轮询只由 active provider Batch 任务调度；终态结果解析、重复 custom_id、修复重试和 provider 资源清理均具备可观测终态；补偿数量达到 100 时进入修复 Batch，并持久化新 Batch 标识。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
- **Before:** 内容策略大批量仍被强制走 realtime；Batch 终态解析异常可能停在 validating；修复失败会过早套用源 Markdown 回退；修复 Batch 标识未覆盖部分远程步骤。
- **After:** 内容策略按 Batch 配置和阈值选择 Batch；终态异常进入 failed 并清理 provider 文件；条目契约错误保留 awaiting_model 直到一次补偿完成；补偿 Batch 的 input/batch ID 按步骤持久化，轮询仅对 active provider Batch 继续。
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py; uv run ruff check ai_management/model_cleaning.py ai_management/workers/tasks/model_cleaning.py tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py; uv run python -m compileall -q ai_management
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-18T11:35:13+08:00 — 在既有四策略 Batch 选择、终态解析和补偿守恒基础上，补充生产 Redis 锁后端不可用时的可观测重排；修复远端 Batch 创建后本地提交异常的 ID 恢复；无 provider ID 的 batch_running 状态转为可诊断失败；多 provider 任务只继续轮询 active Batch，并保留终态输出映射。

- **Purpose:** 在既有四策略 Batch 选择、终态解析和补偿守恒基础上，补充生产 Redis 锁后端不可用时的可观测重排；修复远端 Batch 创建后本地提交异常的 ID 恢复；无 provider ID 的 batch_running 状态转为可诊断失败；多 provider 任务只继续轮询 active Batch，并保留终态输出映射。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
- **Before:** 轮询锁后端故障可能静默停止；远端创建后的本地提交异常存在未持久化 ID 窗口；异常 batch_running 状态可能悬挂；多分片轮询会重复查询已知终态。
- **After:** LockState 区分锁占用与后端不可用，后者记录并重排；提交异常通过恢复函数持久化远端 ID并继续轮询；缺失 provider ID 进入 failed；终态 provider ID 被记录并从 active 列表移除。
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py tests/test_batch_cleaner.py; uv run ruff check ai_management/cache.py ai_management/model_cleaning.py ai_management/workers/tasks/model_cleaning.py tests/test_model_cleaning_workflow.py tests/test_large_scale_cleaning.py; uv run python -m compileall -q ai_management; python .codex/governance/gov.py quality
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-18T12:57:47+08:00 — 四个内容策略在显式 BAILIAN_BATCH_ENABLED=true 且候选数或估算输入 Token 达阈值时可使用百炼 Batch；默认关闭或未达阈值仍使用 realtime。Batch 仅对活动供应商任务轮询，终态/取消后停止。远端本轮验收在 11 个样本全部终态前按用户指令取消，活动任务已取消，9 个测试批次、4 个 Dify 文档和精确清洗 Redis 键已清理，BAILIAN_BATCH_ENABLED 已恢复 false；课程/路线数据未变更，DSL 未变更。

- **Purpose:** 四个内容策略在显式 BAILIAN_BATCH_ENABLED=true 且候选数或估算输入 Token 达阈值时可使用百炼 Batch；默认关闭或未达阈值仍使用 realtime。Batch 仅对活动供应商任务轮询，终态/取消后停止。远端本轮验收在 11 个样本全部终态前按用户指令取消，活动任务已取消，9 个测试批次、4 个 Dify 文档和精确清洗 Redis 键已清理，BAILIAN_BATCH_ENABLED 已恢复 false；课程/路线数据未变更，DSL 未变更。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-18T13:09:09+08:00 — 四个内容策略的 Batch 选择与活动轮询代码已部署并通过本地质量门禁；本轮 jx 真实验收在 11 个样本全部终态前按用户指令取消，活动任务已取消，测试 general/Redis/数据库数据已精确清理，默认 BAILIAN_BATCH_ENABLED=false 已恢复。独立复核发现取消状态终态/提交竞态以及临时清理脚本归属证明问题，本轮不再修复；不能宣称完整远端验收或 Feature 完成。DSL 未变更。

- **Purpose:** 四个内容策略的 Batch 选择与活动轮询代码已部署并通过本地质量门禁；本轮 jx 真实验收在 11 个样本全部终态前按用户指令取消，活动任务已取消，测试 general/Redis/数据库数据已精确清理，默认 BAILIAN_BATCH_ENABLED=false 已恢复。独立复核发现取消状态终态/提交竞态以及临时清理脚本归属证明问题，本轮不再修复；不能宣称完整远端验收或 Feature 完成。DSL 未变更。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-19T14:18:57+08:00 — 课程与学习路线写入统一在批次创建前关闭，历史 worker 队列调用前熔断；咨询含图 PDF 强制 OCR，图片存在本身不再触发人工隐私审核，OCR 质量和残留隐私门禁保持不变。

- **Purpose:** 课程与学习路线写入统一在批次创建前关闭，历史 worker 队列调用前熔断；咨询含图 PDF 强制 OCR，图片存在本身不再触发人工隐私审核，OCR 质量和残留隐私门禁保持不变。
- **Affected modules:**
  - `jx-cleaning-acceptance — jx 清洗验收证据`
- **Before:** 课程策略仅隐藏但显式入口仍可创建批次；咨询 PDF 含图直接生成人工隐私审核阻断。
- **After:** course 知识库及三个历史课程策略返回 STRATEGY_DISABLED 且不创建本地状态；咨询含图 PDF 进入 OCR 并继续通过脱敏、质量和发布门禁。
- **Impact / Notes:** 本次不修改 DSL；历史课程数据清理另按精确备份和脚本执行。
- **Validation:** gov quality 通过；主测试、专项 PDF/路由/worker 回归、Other/event_rag_cleaner 和项目索引检查通过。
- **Changed paths:**
  - `scripts/jx_consultation_model_cleaning_run.sh`

## 2026-08-19T14:43:09+08:00 — 本次实现新增课程入口创建前 STRATEGY_DISABLED 熔断、存量课程 Worker 熔断，以及咨询 PDF 含图统一 OCR；同步记录模型契约失败的 fallback 处理、线上发布和回归证据。

- **Purpose:** 本次实现新增课程入口创建前 STRATEGY_DISABLED 熔断、存量课程 Worker 熔断，以及咨询 PDF 含图统一 OCR；同步记录模型契约失败的 fallback 处理、线上发布和回归证据。
- **Affected modules:**
  - `jx-cleaning-acceptance — jx 清洗验收证据`
- **Before:** 课程显式兼容入口仍可能创建课程批次，咨询 PDF 含图存在旧人工隐私门禁；当前 Feature 文档未覆盖本次共享清洗链路修复。
- **After:** 课程/路线写入在本地批次创建前拒绝，存量任务不再进入课程策略；咨询 PDF 含图强制 OCR，真实 OCR/质量/隐私失败仍阻断；指定咨询批次线上重试成功并完成 Dify 入库。
- **Impact / Notes:** 未修改 DSL；课程旧数据按用户确认的精确范围备份后清理。
- **Validation:** gov quality; targeted cleaning/PDF/model tests; jx health/ready; course 409 probe; consultation retry; course cleanup dry-run
- **Changed paths:**
  - `scripts/jx_consultation_model_cleaning_run.sh`

## 2026-08-20T01:28:20+08:00 — 四个内容清洗策略继续保留实时/Batch配置，最近活动固定实时；实时与Batch共用temperature 0.01、seed 1234、enable_thinking false和JSON object请求体；活动列表/详情DSL已发布并验证环境变量默认10。验收脚本补齐源树预算模块、咨询多案例子文档命名契约，Dify SSE安全证据已生成；四策略真实Batch对照仍待jx部署后按100候选阈值完成。

- **Purpose:** 四个内容清洗策略继续保留实时/Batch配置，最近活动固定实时；实时与Batch共用temperature 0.01、seed 1234、enable_thinking false和JSON object请求体；活动列表/详情DSL已发布并验证环境变量默认10。验收脚本补齐源树预算模块、咨询多案例子文档命名契约，Dify SSE安全证据已生成；四策略真实Batch对照仍待jx部署后按100候选阈值完成。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `cleaning-contract-tests — 清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** 验收脚本依赖仅存在于陈旧build产物；活动补充调用参数与内容策略分叉；Dify SSE证据缺失。
- **After:** 预算模块进入源树并通过wheel重建；活动和内容Qwen调用共用稳定请求参数；Dify发布/SSE验证通过；四策略真实Batch对照保留为部署后验收项。
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_activity_cleaning.py tests/test_dify_activity_contract.py tests/test_cleaning_release_contract.py; gov quality; uv build --wheel; Dify SSE safe evidence
- **Changed paths:**
  - `ai_management/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_reset_all_document_data.sh`
  - `scripts/jx_reset_course_route_catalog.sh`
  - `tests/test_large_scale_cleaning.py`

## 2026-08-20T01:48:20+08:00 — 模型清洗继续由实时与Batch共用稳定Qwen请求体（temperature 0.01、seed 1234、enable_thinking false、JSON object）；最近活动固定实时，四个内容策略保留两种可配置模式。活动DSL进一步区分泛化状态列表与具体活动详情，支持任意中文/阿拉伯序号并防止越界误返回；验收预算模块已纳入源树，Dify安全证据已生成，四策略真实Batch入库对照仍待jx部署后完成。

- **Purpose:** 模型清洗继续由实时与Batch共用稳定Qwen请求体（temperature 0.01、seed 1234、enable_thinking false、JSON object）；最近活动固定实时，四个内容策略保留两种可配置模式。活动DSL进一步区分泛化状态列表与具体活动详情，支持任意中文/阿拉伯序号并防止越界误返回；验收预算模块已纳入源树，Dify安全证据已生成，四策略真实Batch入库对照仍待jx部署后完成。
- **Affected modules:**
  - N/A
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_dify_activity_contract.py tests/test_activity_cleaning.py tests/test_cleaning_release_contract.py
- **Changed paths:**
  - N/A

## 2026-08-20T01:58:03+08:00 — 最终活动DSL已区分泛化报名/状态/排期列表与具体活动详情，支持日程、聚会、安排、排期、联谊等自然活动别称；状态列表保留 all+status 过滤和正确无结果文案，任意序号越界安全返回空结果。已在 Dify 新发布 Chatflow 上验证最新所有活动、泛化报名列表、最近具体活动详情、所有延期状态列表、序号越界；Dify安全证据已绑定最新DSL哈希和Chatflow ID。四个内容策略真实Batch入库对照仍待jx部署后完成。

- **Purpose:** 最终活动DSL已区分泛化报名/状态/排期列表与具体活动详情，支持日程、聚会、安排、排期、联谊等自然活动别称；状态列表保留 all+status 过滤和正确无结果文案，任意序号越界安全返回空结果。已在 Dify 新发布 Chatflow 上验证最新所有活动、泛化报名列表、最近具体活动详情、所有延期状态列表、序号越界；Dify安全证据已绑定最新DSL哈希和Chatflow ID。四个内容策略真实Batch入库对照仍待jx部署后完成。
- **Affected modules:**
  - N/A
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_dify_activity_contract.py tests/test_activity_cleaning.py tests/test_cleaning_release_contract.py; uv run python scripts/verify_qwen_activity_e2e.py
- **Changed paths:**
  - N/A

## 2026-08-20T02:14:16+08:00 — quick_intent 增加裸活动安排、活动日程、活动排期列表表达；统一 DSL 已重新生成，最新 Dify 发布证据与四策略真实 Batch 对照仍待补齐。

- **Purpose:** quick_intent 增加裸活动安排、活动日程、活动排期列表表达；统一 DSL 已重新生成，最新 Dify 发布证据与四策略真实 Batch 对照仍待补齐。
- **Affected modules:**
  - N/A
- **Before:** 活动列表自然表达和发布证据尚未覆盖裸排期同义词。
- **After:** 活动安排/日程/排期进入列表分支，统一 DSL 与源 DSL 同步；待重新发布和真实 Batch 四策略验收。
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_dify_activity_contract.py
- **Changed paths:**
  - N/A

## 2026-08-20T03:23:03+08:00 — 修复帖子模型清洗在 reconcile 与模型任务并发时的增量分片规划：已有分片不再阻断新建子文档，分片索引按已有最大值递增；按文件/分片加数据库锁，重复投递只允许 pending 分片领取，避免孤儿 Dify 文档。

- **Purpose:** 修复帖子模型清洗在 reconcile 与模型任务并发时的增量分片规划：已有分片不再阻断新建子文档，分片索引按已有最大值递增；按文件/分片加数据库锁，重复投递只允许 pending 分片领取，避免孤儿 Dify 文档。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** reconcile 可能提前创建部分帖子分片，后续模型生成的未分配文档被长期 shard_pending；重复发布投递可能并发创建 Dify 文档。
- **After:** 增量规划会追加所有未分配 shard_pending 文档，既有 pending 分片可重试、succeeded 分片不重复入队；PostgreSQL file/shard 行锁与 pending-only claim 保证规划和发布幂等。
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_model_cleaning_workflow.py -k post_shard_planner; gov quality
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T04:25:00+08:00 — 最终质量复核指纹更新为 63974947；代码复审通过，外部 Batch/Dify 边界保持未完成

- **Purpose:** 将当前质量门禁与独立复审证据绑定到最新指纹，避免沿用 634bdc 旧记录。
- **Affected modules:**
  - `model-cleaning-orchestration — 分片规划、pending claim 与 watchdog`
  - `model-cleaning-contract-tests — 清洗发布契约测试`
  - `dify-activity-contract — 活动语义与 DSL 验证`
- **Before:** 最新文档条目引用质量指纹 `634bdc06...`。
- **After:** 当前 `gov quality` 为 `passed`，指纹为 `63974947e43bb6cf735ca101ec03c934c50279145b5adc532f30e82ae55b7a39`；独立代码复审通过。四策略 Batch 最终终态、realtime/Batch 全量 KB parity 与最新 Dify DSL 发布仍未完成，不以旧证据替代。
- **Validation:** `gov quality` passed；`post_shard_planner` 3 passed；最新活动/清洗契约回归保持通过。
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `.codex/state/quality.json`


## 2026-08-20T03:52:44+08:00 — 新增按文件锁定的增量分片规划；已成功、待发布、索引中的内容按最终 Markdown 摘要去重，失败分片复用并重置重试；发布 worker 使用 pending-only 行锁 claim，replacement 与补偿删除目标持久化，维护 watchdog 统一时区并在锁后复检。

- **Purpose:** 新增按文件锁定的增量分片规划；已成功、待发布、索引中的内容按最终 Markdown 摘要去重，失败分片复用并重置重试；发布 worker 使用 pending-only 行锁 claim，replacement 与补偿删除目标持久化，维护 watchdog 统一时区并在锁后复检。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 发布规划可能遗漏模型后生成子文档，重复摘要触发唯一键失败，失败分片内容可能丢失；重复投递和 watchdog 可能重复发布。
- **After:** 增量文档均能进入分片；可确认已发布或可复用内容不重复，失败分片可恢复；发布与维护路径具备数据库锁和持久化远端清理目标。
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_model_cleaning_workflow.py -k post_shard_planner; gov quality (QUALITY PASSED b141a10a0b65a26fb32551e176d5d043c818a0dfeefcb66a975854d2a9302950)
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T04:16:55+08:00 — planner 对失败同摘要分片复用并重置为 pending，避免唯一键冲突和内容丢失；watchdog 使用 _utc 规范化 updated_at 后在锁内重检；fix4 已部署并完成真实 zsxq 增量入库验收，四策略 Batch 供应商终态和 Dify 最新 DSL 发布保留为外部待验收项。

- **Purpose:** planner 对失败同摘要分片复用并重置为 pending，避免唯一键冲突和内容丢失；watchdog 使用 _utc 规范化 updated_at 后在锁内重检；fix4 已部署并完成真实 zsxq 增量入库验收，四策略 Batch 供应商终态和 Dify 最新 DSL 发布保留为外部待验收项。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 上一轮文档未覆盖失败 shard 唯一键重用和 SQLite 时区边界，部署与真实增量回读证据未纳入当前状态。
- **After:** 失败 shard 可重试、SQLite watchdog 不再抛 naive/aware 异常；fix4 发布、备份、健康与 zsxq 196/198 回读有脱敏证据；Batch/Dify 未完成项被明确标记而非宣称通过。
- **Impact / Notes:** None
- **Validation:** uv run pytest -q tests/test_model_cleaning_workflow.py -k post_shard_planner (3 passed); gov quality b141a10a0b65a26fb32551e176d5d043c818a0dfeefcb66a975854d2a9302950
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T04:20:56+08:00 — 最终质量指纹更新为 634bdc06；fix4 jx 健康与 zsxq 增量回读通过；四策略 Batch 首批供应商仍 in_progress，Dify 最新 DSL 因 SSRF/Chrome 文件 chooser 未发布，测试数据和任务保留。

- **Purpose:** 最终质量指纹更新为 634bdc06；fix4 jx 健康与 zsxq 增量回读通过；四策略 Batch 首批供应商仍 in_progress，Dify 最新 DSL 因 SSRF/Chrome 文件 chooser 未发布，测试数据和任务保留。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 当前状态文档未记录最新质量重跑和外部验证阻塞。
- **After:** 状态文档记录最终质量指纹、发布健康、Batch/Dify 未完成边界与恢复后的实时配置，禁止将旧证据当作通过。
- **Impact / Notes:** None
- **Validation:** gov quality 634bdc06d836fedc659de93af53fbd9fb4797d6af795c7c883baed2b1edf7026
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T04:27:38+08:00 — 当前质量门禁通过，分片规划/失败复用/watchdog UTC/pending claim 修复已部署并通过定向回归；四策略 Batch 最终终态与最新 Dify DSL 发布仍待外部验收，不将旧证据当作完成。

- **Purpose:** 当前质量门禁通过，分片规划/失败复用/watchdog UTC/pending claim 修复已部署并通过定向回归；四策略 Batch 最终终态与最新 Dify DSL 发布仍待外部验收，不将旧证据当作完成。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T05:10:00+08:00 — 四策略真实 realtime/Batch 终态完成；最新活动 DSL 已发布并通过浏览器回归，jx 默认 API 入口未切换

- **Purpose:** 完成四个认知分享策略的真实 realtime/Batch 对照验收，并记录最新活动 DSL 的导入、发布和自然语言查询验证。
- **Affected modules:**
  - `model-cleaning-orchestration — 四策略 realtime/Batch 生命周期与入库 parity`
  - `dify-activity-routing — 活动列表/详情 DSL 发布与查询回归`
- **Before:** 仅有部分策略 Batch 进行中，最新 DSL 尚无发布证据。
- **After:** consultation 100/100、wechat 101/101、xiaobot 101/101、zsxq 101/101 候选均完成 Batch；四策略 realtime 任务也均无失败。xiaobot/zsxq 的 realtime 与 Batch 输出文档数不同，但本地状态、Dify ID、metadata key set 和抽样远端索引状态一致，差异归因于模型分片。最新 DSL SHA `de8394a2570c877e64f3c18452f74d632de525438566354a45fb1875077c4e2b` 已发布到独立 Chatflow App，并通过“最新活动有哪些”“最近活动有哪些”和指定活动详情查询验证。
- **Impact / Notes:** jx 默认配置已恢复 realtime（`BAILIAN_BATCH_ENABLED=false`），上传上限 10、Batch 阈值 100/200000；测试数据、任务和报告保留。为避免未经确认改变现有入口，未切换 jx 的 `DIFY_BUSINESS_APP_API_KEY` 到新 Chatflow App。
- **Validation:** 四策略 Batch 供应商任务均 `completed` 且成功数等于候选数；Dify 抽样文档 `indexing_status=completed`；浏览器发布/查询验证通过；当前质量门禁此前已通过（最新代码质量证据见 `quality.json`）。
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `docs/features/qwen-batch-cleaning/CHANGELOG.md`

## 2026-08-20T06:00:00+08:00 — 按项目 Agent 规则补齐全量远端 Metadata parity、Dify 发布边界和排障索引

- **Purpose:** 将四策略 realtime/Batch 全量 Dify 回读结果、活动上限配置和“独立 DSL 已发布但 jx 默认 API 未切换”的边界同步到权威清洗/Dify/排障文档。
- **Affected modules:**
  - `model-cleaning-orchestration — realtime/Batch 终态与 Metadata 回读`
  - `dify-activity-routing — DSL 发布与 App Key 映射边界`
  - `cleaning-documentation — 清洗、Dify、测试和部署排障索引`
- **Before:** 终态证据主要集中在交接和脱敏报告，Dify 指南仍有活动结果上限 3 条的旧描述，排障文档未说明全量远端回读口径。
- **After:** Dify 指南改为 `ACTIVITY_MAX_RESULTS` 默认 10、范围 1～20；新增全量远端 metadata/indexing 回读边界；清洗、Dify、测试排障分别加入可复用的 parity 验收规则；文档索引指向脱敏证据。没有把本地中间态字段差异或模型拆分差异误写成最终知识库失败。
- **Impact / Notes:** 仅文档和脱敏报告索引更新；未修改业务代码、Dify 文档、jx 默认 App Key、测试数据、Batch 任务或知识库数据。
- **Validation:** 四策略全部关联 Dify 文档回读无错误、`indexing_status=completed`、同策略远端 metadata key set 一致；项目级文档同步按 `AGENTS.md` 完成。
- **Changed paths:**
  - `docs/README.md`
  - `docs/清洗策略接入说明.md`
  - `docs/Dify创建导入绑定与契约测试.md`
  - `docs/troubleshooting/data-cleaning.md`
  - `docs/troubleshooting/dify.md`
  - `docs/troubleshooting/testing.md`
  - `AI管理服务当前状态与后续测试交接.md`

## 2026-08-20T09:05:52+08:00 — 四个认知分享策略均完成真实 Batch 与 realtime 对照；本地成功文档、Dify ID、metadata key set 和抽样远端索引状态一致，xiaobot/zsxq 文档数量差异归因于模型分片。最新活动 DSL 已在登录 Chrome 导入发布并通过列表/详情查询验证；jx 默认 API 入口未切换，默认配置恢复实时模式，测试数据保留。

- **Purpose:** 四个认知分享策略均完成真实 Batch 与 realtime 对照；本地成功文档、Dify ID、metadata key set 和抽样远端索引状态一致，xiaobot/zsxq 文档数量差异归因于模型分片。最新活动 DSL 已在登录 Chrome 导入发布并通过列表/详情查询验证；jx 默认 API 入口未切换，默认配置恢复实时模式，测试数据保留。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 四策略 Batch 及最新 DSL 发布证据未完整，交接仍标记为外部待验收。
- **After:** consultation 100/100、wechat 101/101、xiaobot 101/101、zsxq 101/101 Batch 候选成功；realtime 无失败；最新 DSL SHA de8394a2570c877e64f3c18452f74d632de525438566354a45fb1875077c4e2b 已发布到独立 Chatflow App 并通过查询验证。
- **Impact / Notes:** 未切换 jx DIFY_BUSINESS_APP_API_KEY；未删除测试数据、任务或报告。
- **Validation:** 四策略供应商任务终态 completed；Dify 抽样 indexing_status=completed；浏览器活动列表/详情回归通过；既有 gov quality 证据有效。
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T09:11:49+08:00 — 外部验收补录后项目级 gov quality 通过，当前质量指纹 76b897cd60d972b172354da8f5852dae45c1242a24b5cc1b459f5ce9dba15e3c，治理门禁完成重新计算。

- **Purpose:** 外部验收补录后项目级 gov quality 通过，当前质量指纹 76b897cd60d972b172354da8f5852dae45c1242a24b5cc1b459f5ce9dba15e3c，治理门禁完成重新计算。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 交接已补录外部验收，但质量证据仍需绑定当前文档状态。
- **After:** 当前质量、文档同步和独立复审指纹一致，未改变业务代码或外部发布边界。
- **Impact / Notes:** jx 默认 Dify API 入口未切换，测试数据与任务保留。
- **Validation:** gov quality passed；全量单元/集成与 Qwen/Dify E2E 通过。
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T11:31:07+08:00 — 全量只读回读证明四策略 realtime/Batch 的 Dify 远端 metadata key set 按策略一致、必需字段齐全、索引全部完成；本地 realtime 的部分小报童/知识星球中间文档缺少可选字段，但这些字段不发送到 Dify，最终 KB metadata 不受影响。

- **Purpose:** 全量只读回读证明四策略 realtime/Batch 的 Dify 远端 metadata key set 按策略一致、必需字段齐全、索引全部完成；本地 realtime 的部分小报童/知识星球中间文档缺少可选字段，但这些字段不发送到 Dify，最终 KB metadata 不受影响。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `cleaning-contract-tests — 清洗契约回归`
- **Before:** 仅有抽样远端 metadata 证据，未区分本地中间态差异。
- **After:** 新增脱敏全量回读证据并明确最终 KB 一致与本地中间态非完全一致的边界。
- **Impact / Notes:** 不修改业务代码、任务、Dify 文档或测试数据。
- **Validation:** 全量 Dify document get_document 回读；所有关联文档 indexing_status=completed；每组必需 metadata 字段齐全。
- **Changed paths:**
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
## 2026-08-20 — 原始命名前缀、跨源去重与模型失败人工审批

- 修复模型拆分/发布使用模型标题覆盖原始文件名的问题，所有 Dify 文档保留原始 stem 前缀。
- 在同知识库/策略/规格锁内按规范化最终正文跨源、跨批次去重，重复结果记录 `excluded` 和去重范围。
- 模型契约失败保存服务端审核 artifact，条目保持 `review_required`；显式 approve 后才重新发布，exclude 不发布；对外接口保持兼容。
- 首轮回归中的旧自动兜底断言、旧正文指纹断言和生成索引过期已统一修复；聚焦、全量本地和集成测试通过。

## 2026-08-20T16:00:11+08:00 — Model contract failures now persist review artifacts and remain review_required; source stems are retained in Dify names; normalized final-body dedup is serialized by knowledge-base strategy contract; regression and index checks pass.

- **Purpose:** Model contract failures now persist review artifacts and remain review_required; source stems are retained in Dify names; normalized final-body dedup is serialized by knowledge-base strategy contract; regression and index checks pass.
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** Contract failure automatically published source Markdown; model titles could replace source names; duplicate scope was limited.
- **After:** Explicit approve is required before fallback publication; review artifacts are addressable by strategy plus original stem; cross-source and cross-batch duplicates are excluded under a contract lock.
- **Impact / Notes:** None
- **Validation:** focused model-cleaning/release/activity tests; full local pytest; event_rag_cleaner integration; project index check
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T16:57:25+08:00 — Model contract failure review gate, source-stem names, contract-scoped normalized dedup, artifact retention and session-safe audit are implemented; focused/full local tests and index checks pass.

- **Purpose:** Model contract failure review gate, source-stem names, contract-scoped normalized dedup, artifact retention and session-safe audit are implemented; focused/full local tests and index checks pass.
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** Automatic source fallback publication and source-title-derived model shard names.
- **After:** review_required artifact with explicit approve/exclude; original stem names and normalized cross-source/cross-batch dedup under a lock.
- **Impact / Notes:** None
- **Validation:** gov quality passed; full pytest and integration passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T21:17:31+08:00 — 当前实现的模型契约失败人工审批、原始 stem 命名、契约范围规范化去重与事务复用审计已与专项文档一致；生成的 HTTP/WORKERS 索引已更新，当前质量门禁全绿。

- **Purpose:** 当前实现的模型契约失败人工审批、原始 stem 命名、契约范围规范化去重与事务复用审计已与专项文档一致；生成的 HTTP/WORKERS 索引已更新，当前质量门禁全绿。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 质量重跑仍发现 docs/功能与接口索引.md 的 HTTP、WORKERS 自动区块落后于源码，治理质量指纹和文档同步状态未闭环。
- **After:** 已运行 scripts/generate_project_index.py --write；gov quality 通过，当前质量指纹为 c3afc543e11fc15123636210debfce7897f1d58ae2f54348a33854856b49a3a0。
- **Impact / Notes:** 不改变对外 API、Dify 数据、测试数据或发布边界；后续仍需独立复审与 jx 精确发布验收。
- **Validation:** gov quality；生成索引契约；全量本地单元/集成与 Qwen 活动验收
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T21:53:47+08:00 — 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据；模型审核材料采用锁、原子替换和 0600 权限。新增并通过复核、历史摘要、元数据回归测试。

- **Purpose:** 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据；模型审核材料采用锁、原子替换和 0600 权限。新增并通过复核、历史摘要、元数据回归测试。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 复核接口可能处理非待复核状态；历史成功文档和旧分片摘要可能漏过精确去重；分片发布可能丢失策略业务元数据；审核材料并发写存在覆盖风险。
- **After:** 复核仅接受 review_required 且串行化版本更新；远端排除有精确补偿任务；去重比较最终正文；分片元数据与普通发布使用同一投影；审核材料安全原子写入。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 0584e6b07bb577cad5d6b21dd71cd1c9e1cc4cb9d03aaac0d7bdfd5f9feb2c54; focused review/shard tests passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T22:01:58+08:00 — 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据；模型审核材料采用锁、原子替换、0600 权限和 16 MiB 单文件大小上限。新增并通过复核、历史摘要、元数据回归测试。

- **Purpose:** 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据；模型审核材料采用锁、原子替换、0600 权限和 16 MiB 单文件大小上限。新增并通过复核、历史摘要、元数据回归测试。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 复核接口可能处理非待复核状态；历史成功文档和旧分片摘要可能漏过精确去重；分片发布可能丢失策略业务元数据；审核材料并发写与文件大小无明确边界。
- **After:** 复核仅接受 review_required 且串行化版本更新；远端排除有精确补偿任务；去重比较最终正文；分片元数据与普通发布使用同一投影；审核材料安全原子写入并有 16 MiB 上限。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** final gov quality rerun pending after generated index update
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T22:13:39+08:00 — 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据，并在正文规范化后生成远端文档名；模型审核材料保留 finalize 前原始模型 payload，使用锁、原子替换、目录 0700、文件 0600 与 16 MiB 上限。新增并通过复核、历史摘要、审核 artifact 内容和元数据回归测试。

- **Purpose:** 复核动作增加 review_required 状态门禁与数据库行锁；排除含远端文档 ID 的条目在同一事务创建精确补偿删除任务；跨批次去重按重建的最终发布 Markdown 比较，不再依赖旧 audit 摘要或 1000 条扫描上限；后分片沿策略 allowlist 投影完整 Dify 元数据，并在正文规范化后生成远端文档名；模型审核材料保留 finalize 前原始模型 payload，使用锁、原子替换、目录 0700、文件 0600 与 16 MiB 上限。新增并通过复核、历史摘要、审核 artifact 内容和元数据回归测试。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 模型审核 artifact 可能丢失原始模型结果且权限存在崩溃窗口；历史分片远端名称可能使用旧摘要；共享 worker/test lint 范围未覆盖本轮核心文件。
- **After:** 审核 artifact 可复核真实原始模型结果且权限/大小边界明确；历史分片先规范化再命名；canonical lint 覆盖本轮核心 worker 与测试文件。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** final gov quality rerun pending after index regeneration
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T22:27:47+08:00 — 同步当前交接文档中的最终质量指纹、本地 wheel 校验和审核材料安全边界：review_required 状态门禁与行锁、远端精确补偿、最终正文去重、历史分片规范化命名、原始模型 payload 审核 artifact、目录 0700/文件 0600 和 16 MiB 上限均已实现并通过本地质量回归。

- **Purpose:** 同步当前交接文档中的最终质量指纹、本地 wheel 校验和审核材料安全边界：review_required 状态门禁与行锁、远端精确补偿、最终正文去重、历史分片规范化命名、原始模型 payload 审核 artifact、目录 0700/文件 0600 和 16 MiB 上限均已实现并通过本地质量回归。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 交接文档仍引用上一轮质量指纹和 wheel 摘要，未记录最新审核 artifact 内容与权限边界。
- **After:** 交接文档已记录最终质量指纹、wheel SHA、审核 artifact 与历史分片修复，并明确 jx 发布和真实样本验收尚未执行。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 7f196f54aa0fa2d8b2562b699ba3e7283e9908a8a70a35f7263150c3c642680f
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T22:39:04+08:00 — 完成原会话模型清洗边界修复：review_required 状态门禁/行锁、远端精确补偿、跨批次最终正文去重、历史分片规范化命名与策略 metadata 投影；审核 artifact 保留 finalize 前原始 payload，目录 0700、临时/目标文件 0600、16 MiB 上限；供应商 cancelling 保持 active，管理员取消与 Batch 提交共用任务行锁并在上传/创建边界复检，异常恢复不会复活已取消任务。

- **Purpose:** 完成原会话模型清洗边界修复：review_required 状态门禁/行锁、远端精确补偿、跨批次最终正文去重、历史分片规范化命名与策略 metadata 投影；审核 artifact 保留 finalize 前原始 payload，目录 0700、临时/目标文件 0600、16 MiB 上限；供应商 cancelling 保持 active，管理员取消与 Batch 提交共用任务行锁并在上传/创建边界复检，异常恢复不会复活已取消任务。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 供应商 cancelling 会被错误当终态；取消和提交可产生孤儿 Batch；审核临时文件权限在创建后才收紧；交接文档与质量指纹未记录最终修复。
- **After:** Batch 生命周期、取消补偿和审核 artifact 安全边界均已修复；本地质量与 focused regression 通过，jx 远端写入/发布和真实样本验收仍待精确发布确认。
- **Impact / Notes:** 本轮未进行远端写入或发布。
- **Validation:** gov quality passed 6222e8c21dc8464c014a02c4facc4b25f4d06bd9f608643ec1cada2ed5c17965; focused cancellation/artifact tests passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T22:48:05+08:00 — Dynamic evidence only; Batch code semantics unchanged

- **Purpose:** Dynamic evidence only; Batch code semantics unchanged
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T23:12:54+08:00 — Batch cancellation/recovery state boundaries are locked; cancelled repair recovery performs exact provider cleanup; review artifacts fingerprint request blocks, model, and prompt version; focused regressions pass

- **Purpose:** Batch cancellation/recovery state boundaries are locked; cancelled repair recovery performs exact provider cleanup; review artifacts fingerprint request blocks, model, and prompt version; focused regressions pass
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T23:22:36+08:00 — Cancellation locks, cancelled recovery cleanup, request fingerprint, generated index, and local verification are synchronized

- **Purpose:** Cancellation locks, cancelled recovery cleanup, request fingerprint, generated index, and local verification are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T23:34:56+08:00 — Recovery row locking, pre-call request context persistence, exact cancelled cleanup, generated index, and tests are synchronized

- **Purpose:** Recovery row locking, pre-call request context persistence, exact cancelled cleanup, generated index, and tests are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-20T23:51:10+08:00 — Repair Batch upload/create/final transition locks, validating state compatibility, exact cancellation cleanup, request fingerprints, and tests are synchronized

- **Purpose:** Repair Batch upload/create/final transition locks, validating state compatibility, exact cancellation cleanup, request fingerprints, and tests are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T00:03:01+08:00 — Validating cancellation guard, exact bailian batch retry task, provider ID cleanup, repair Batch locks, request fingerprints, and generated index are synchronized

- **Purpose:** Validating cancellation guard, exact bailian batch retry task, provider ID cleanup, repair Batch locks, request fingerprints, and generated index are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T00:13:04+08:00 — Watchdog row lock, cancelled-task skip, batch cancel/file retries, validating cancellation, repair locks, request fingerprints, and generated index are synchronized

- **Purpose:** Watchdog row lock, cancelled-task skip, batch cancel/file retries, validating cancellation, repair locks, request fingerprints, and generated index are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T00:23:11+08:00 — Artifact failure convergence, repair hint fingerprint preservation, watchdog lock, batch cancel/file retry, cancellation guards, and generated index are synchronized

- **Purpose:** Artifact failure convergence, repair hint fingerprint preservation, watchdog lock, batch cancel/file retry, cancellation guards, and generated index are synchronized
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T00:38:43+08:00 — Batch 提交恢复失败时精确清理已创建供应商资源；看门狗清理带远端 ID 的陈旧任务并阻止重复重排；validating 小批量修复调用前后复检任务状态；审核 artifact 记录 repair_hint_sha256。

- **Purpose:** Batch 提交恢复失败时精确清理已创建供应商资源；看门狗清理带远端 ID 的陈旧任务并阻止重复重排；validating 小批量修复调用前后复检任务状态；审核 artifact 记录 repair_hint_sha256。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 异常恢复或看门狗可能遗留供应商 Batch/文件，修复调用可能在取消后继续写入，artifact 不单独记录修复提示摘要。
- **After:** 所有已知供应商资源进入精确取消/文件删除或重试队列，取消获胜不会重复提交或写回结果，审核材料只保存修复提示摘要。
- **Impact / Notes:** None
- **Validation:** 聚焦模型清洗 pytest、Ruff、compileall、git diff --check
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T00:50:02+08:00 — 提交异常兜底保持 pending_retry，不覆盖为 completed；未进入恢复分支的持久化 Batch 取消失败登记精确 bailian_batch 重试，并处理取消返回的 output/error 文件。

- **Purpose:** 提交异常兜底保持 pending_retry，不覆盖为 completed；未进入恢复分支的持久化 Batch 取消失败登记精确 bailian_batch 重试，并处理取消返回的 output/error 文件。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 后续空文件清理可能覆盖已有 pending_retry，持久化 Batch 取消失败仅记录日志。
- **After:** 清理状态单调保持待重试，Batch/文件资源均进入精确清理或可重试任务。
- **Impact / Notes:** None
- **Validation:** 模型清洗聚焦 pytest、Ruff、compileall、git diff --check
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T01:00:37+08:00 — 终态 Batch 文件 ID 持久化并兼容历史 terminal 摘要；提交异常最终收敛锁任务行并保持 cancelled；取消覆盖 shard_pending；审批/复核/实时重试补齐幂等和行锁；未知旧分片摘要不再参与正文去重。

- **Purpose:** 终态 Batch 文件 ID 持久化并兼容历史 terminal 摘要；提交异常最终收敛锁任务行并保持 cancelled；取消覆盖 shard_pending；审批/复核/实时重试补齐幂等和行锁；未知旧分片摘要不再参与正文去重。
- **Affected modules:**
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 终态返回文件可能脱离任务清理字段，取消/提交/修复状态存在竞态，审批和重试缺少重复请求保护。
- **After:** Batch 终态资源可由任务/补偿任务精确追踪，取消不会复活或遗留可发布分片，重复动作不重复入队，旧 digest 域不误匹配。
- **Impact / Notes:** None
- **Validation:** 模型清洗与人工管理聚焦 pytest、Ruff、compileall、git diff --check
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T01:18:33+08:00 — 统一兼容 provider_batch_id 与 provider_batch_ids，轮询归一化历史任务；取消、看门狗和管理端展示复用兼容读取；核心模型 worker 与工作流测试纳入 canonical format/lint。

- **Purpose:** 统一兼容 provider_batch_id 与 provider_batch_ids，轮询归一化历史任务；取消、看门狗和管理端展示复用兼容读取；核心模型 worker 与工作流测试纳入 canonical format/lint。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 历史任务仅有单数 Batch ID 时部分生命周期路径可能漏清理；核心 worker/test 不在 canonical format/lint 文件列表。
- **After:** 轮询、取消、看门狗和管理端均能识别并处理单数/复数 Batch ID，历史任务轮询时归一化；核心模型源码与工作流测试由 canonical format/lint 覆盖。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；Ruff format/check；gov quality
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T01:40:00+08:00 — 看门狗先核对供应商 Batch 终态，保留已完成结果并安排正常轮询；仅活动 Batch 在精确取消清理成功后重排；canonical Ruff 显式覆盖本轮全部变更源码与测试路径。

- **Purpose:** 看门狗先核对供应商 Batch 终态，保留已完成结果并安排正常轮询；仅活动 Batch 在精确取消清理成功后重排；canonical Ruff 显式覆盖本轮全部变更源码与测试路径。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** stale task 带远端 Batch ID 时可能直接取消删除结果并置为不可恢复；format/lint 白名单遗漏本轮共享生命周期路径。
- **After:** 已完成或状态不明的远端 Batch 保留引用和结果并进入轮询；活动 Batch 仅在清理成功后重排；本轮所有变更源码/测试路径纳入 canonical Ruff。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；Ruff format/check；git diff --check；gov quality
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T01:50:03+08:00 — 看门狗远端终态对账、结果保留、活动 Batch 清理后重排和查询失败保留引用已完成；核心变更路径由 canonical Ruff 覆盖并通过全量门禁。

- **Purpose:** 看门狗远端终态对账、结果保留、活动 Batch 清理后重排和查询失败保留引用已完成；核心变更路径由 canonical Ruff 覆盖并通过全量门禁。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 看门狗终态修复和最终质量结果尚未同步。
- **After:** 看门狗状态恢复语义与全量验证结果已同步。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；gov quality；接口索引契约
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T02:08:20+08:00 — stale task 统一复用 poll 状态机，避免自行销毁 Batch 结果；无 Batch ID 的 realtime 复用原任务，batch 回到原行待批准，避免重复唯一键。

- **Purpose:** stale task 统一复用 poll 状态机，避免自行销毁 Batch 结果；无 Batch ID 的 realtime 复用原任务，batch 回到原行待批准，避免重复唯一键。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** 看门狗仍存在取消竞态、状态恢复入口不一致和重复 planner 风险。
- **After:** 所有 stale 模型任务都按远端引用和原任务行安全恢复，结果消费与重排分别由既有 poll/任务入口负责。
- **Impact / Notes:** None
- **Validation:** tests/test_model_cleaning_workflow.py；Ruff format/check；gov quality
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T02:43:13+08:00 — Batch 全终态持久化 provider_terminal_batches；watchdog 可从终态映射恢复；输出文件下载失败保留远端引用重试；本地结果事务提交后才清理供应商文件。旧目标清洗数据按精确 manifest 删除且不建立备份，删除后只处理后续新上传数据。

- **Purpose:** Batch 全终态持久化 provider_terminal_batches；watchdog 可从终态映射恢复；输出文件下载失败保留远端引用重试；本地结果事务提交后才清理供应商文件。旧目标清洗数据按精确 manifest 删除且不建立备份，删除后只处理后续新上传数据。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** uv run python -m pytest -q tests/test_model_cleaning_workflow.py; powershell -NoProfile -ExecutionPolicy Bypass -File .codex/governance/gov.ps1 quality
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T03:00:11+08:00 — 终态供应商 Batch 的结果映射在本地结果事务完成前保留，修复批次切换时清除旧终态映射，输出异常保留远端引用后重试；管理员取消与维护清理对 cancelling 保留精确补偿任务；旧目标数据按精确清单删除且不备份，后续只接收新上传数据。

- **Purpose:** 终态供应商 Batch 的结果映射在本地结果事务完成前保留，修复批次切换时清除旧终态映射，输出异常保留远端引用后重试；管理员取消与维护清理对 cancelling 保留精确补偿任务；旧目标数据按精确清单删除且不备份，后续只接收新上传数据。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning workflow tests passed; canonical quality pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T03:06:57+08:00 — 终态 Batch 结果先写入本地并完成模型任务收敛，再执行供应商文件清理；在本地 finalize 失败时保留终态映射和远端文件供 watchdog 恢复，成功处理后保留终态映射作为审计信息；修复 Batch 切换继续清除旧映射。

- **Purpose:** 终态 Batch 结果先写入本地并完成模型任务收敛，再执行供应商文件清理；在本地 finalize 失败时保留终态映射和远端文件供 watchdog 恢复，成功处理后保留终态映射作为审计信息；修复 Batch 切换继续清除旧映射。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning workflow tests passed; canonical quality pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T03:33:18+08:00 — 新增共享 Bailian 清理服务，修复 Batch 修复轮次切换的原子映射/旧文件 work item、终态结果提交前的文件清理计划、管理员取消崩溃对账、cancelling 处理和清理队列公平调度/人工阻塞；用户范围仍为旧目标数据精确删除、不备份、删除后只处理新数据。

- **Purpose:** 新增共享 Bailian 清理服务，修复 Batch 修复轮次切换的原子映射/旧文件 work item、终态结果提交前的文件清理计划、管理员取消崩溃对账、cancelling 处理和清理队列公平调度/人工阻塞；用户范围仍为旧目标数据精确删除、不备份、删除后只处理新数据。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused model cleaning and cleanup lifecycle regressions passed; canonical quality pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/services/bailian_cleanup.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-21T03:37:39+08:00 — 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。

- **Purpose:** 取消 RPC 异常和 cancelling 状态均保留精确 Batch/input 引用并建立可重试清理 work item；终态清理、publication 入队失败和清理公平调度修复保持不变。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** focused cleanup lifecycle regressions passed; canonical quality pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/services/bailian_cleanup.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-08-31T14:50:06+08:00 — 运行时默认时区固定为 Asia/Shanghai；清洗文档沿用原始文件名 stem 与拆分序号命名，补充 book/lead_magnet/standard_document 回归测试。

- **Purpose:** 运行时默认时区固定为 Asia/Shanghai；清洗文档沿用原始文件名 stem 与拆分序号命名，补充 book/lead_magnet/standard_document 回归测试。
- **Affected modules:**
  - N/A
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** gov.ps1 quality
- **Changed paths:**
  - `.env.example`
  - `AI管理服务当前状态与后续测试交接.md`
  - `Dockerfile`
  - `Dockerfile.ocr-offline`
  - `Dockerfile.offline`
  - `Dockerfile.ui-offline`
  - `ai_management/api/routers/admin_logs.py`
  - `docker-compose.ai.yml`
  - `docker-compose.ui.yml`
  - `tests/test_ai_management.py`
  - `tests/test_cleaning_release_contract.py`
  - `tmp/time_probe.db`

## 2026-08-31T14:58:07+08:00 — 新启动容器统一 Asia/Shanghai；PDF 新解决记录显式北京时间；清洗文档保留原始文件名 stem 与拆分序号，历史数据和 DSL 不变。

- **Purpose:** 新启动容器统一 Asia/Shanghai；PDF 新解决记录显式北京时间；清洗文档保留原始文件名 stem 与拆分序号，历史数据和 DSL 不变。
- **Affected modules:**
  - N/A
- **Before:** 容器未统一 TZ，PDF resolved_at 无显式时区；book/其它策略缺少命名回归覆盖。
- **After:** Dockerfile/Compose/.env.example 固定 Asia/Shanghai，resolved_at 带 +08:00，新增命名和时区契约测试。
- **Impact / Notes:** None
- **Validation:** gov quality 已通过；新增 focused tests 4 passed。
- **Changed paths:**
  - `.env.example`
  - `.pytest-tmp-business-deploy/test_admin_knowledge_batch_sta0/01a04261-9f26-7f2e-86b4-242774e43f6d/failed.md`
  - `.pytest-tmp-business-deploy/test_book_scope_is_trusted_and0/01a04261-ee6a-7bb5-a49b-ad466e5703f7/book.md`
  - `.pytest-tmp-business-deploy/test_cleaning_batch_upload_rep0/01a04261-8d17-7db0-b288-79862c4a6da2/01a04261-8d2c-75c2-8668-fccfa528f972.md`
  - `.pytest-tmp-business-deploy/test_cleaning_strategy_and_fil0/sample.md`
  - `.pytest-tmp-business-deploy/test_cleaning_strategy_and_fil0/公众号.txt`
  - `.pytest-tmp-business-deploy/test_cleaning_worker_enqueues_0/01a04261-dbd8-7b20-8dfd-cf44ee487e93/lead.md`
  - `.pytest-tmp-business-deploy/test_conversion_markdown_root_0/batch-001/file-001.docx`
  - `.pytest-tmp-business-deploy/test_conversion_markdown_toggl0/batch-001/file-001.pdf`
  - `.pytest-tmp-business-deploy/test_course_import_generates_f0/catalog.md`
  - `.pytest-tmp-business-deploy/test_disabled_course_worker_do0/01a04261-e169-7b91-a82b-61583f4f40a6/catalog.xlsx`
  - `.pytest-tmp-business-deploy/test_empty_content_strategy_is0/01a04261-e7a6-7f22-8182-2daf0fb8f149/empty.md`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/fake.md`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/fake.pdf`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/sample.md`
  - `.pytest-tmp-business-deploy2/test_admin_knowledge_batch_sta0/01a04263-b0f1-75b7-a1f5-552b88757d56/failed.md`
  - `.pytest-tmp-business-deploy2/test_book_scope_is_trusted_and0/01a04264-1725-74a9-8574-eb0300635472/book.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_batch_upload_rep0/01a04263-989b-76c8-a60c-8972514910f9/01a04263-98ac-7649-adf6-16727831c665.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_strategy_and_fil0/sample.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_strategy_and_fil0/公众号.txt`
  - `.pytest-tmp-business-deploy2/test_cleaning_worker_enqueues_0/01a04263-f7e3-7c2d-9ec3-35d1b53ded92/lead.md`
  - `.pytest-tmp-business-deploy2/test_conversion_markdown_root_0/batch-001/file-001.docx`
  - `.pytest-tmp-business-deploy2/test_conversion_markdown_toggl0/batch-001/file-001.pdf`
  - `.pytest-tmp-business-deploy2/test_course_import_generates_f0/catalog.md`
  - `.pytest-tmp-business-deploy2/test_disabled_course_worker_do0/01a04263-ffb9-7fdc-b696-3a02981760c3/catalog.xlsx`
  - `.pytest-tmp-business-deploy2/test_empty_content_strategy_is0/01a04264-0c8f-7def-a7f2-e1a9c31d5cb3/empty.md`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/fake.md`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/fake.pdf`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/sample.md`
  - `AI管理服务当前状态与后续测试交接.md`
  - `Dockerfile`
  - `Dockerfile.ocr-offline`
  - `Dockerfile.offline`
  - `Dockerfile.ui-offline`
  - `ai_management/api/routers/admin_logs.py`
  - `docker-compose.ai.yml`
  - `docker-compose.ui.yml`
  - `test.md`
  - `test00.md`
  - `test11.md`
  - `test33.md`
  - `test66.md`
  - `tests/test_ai_management.py`
  - `tests/test_cleaning_release_contract.py`
  - `tmp/time_probe.db`
  - `uv.lock`
  - `测试.md`
  - `测试111.md`
  - `测试2.md`
  - `测试66.md`

## 2026-08-31T15:31:17+08:00 — work0831 的北京时间默认与原始文件名清洗命名已部署到 jx；服务健康、容器时区和远端命名函数均已核验，历史数据与 DSL 不变。

- **Purpose:** work0831 的北京时间默认与原始文件名清洗命名已部署到 jx；服务健康、容器时区和远端命名函数均已核验，历史数据与 DSL 不变。
- **Affected modules:**
  - N/A
- **Before:** jx 尚运行旧 toolconv 镜像，部署与真实运行态验证待完成。
- **After:** jx 核心/UI/Worker/Scheduler 切换到 quick-20260831-152631-cae6bc82c420，ready、时区和命名契约验证通过。
- **Impact / Notes:** None
- **Validation:** 本地 gov quality 通过；jx ready、TZ、命名函数和 admin_logs 代码核验通过。
- **Changed paths:**
  - `.env.example`
  - `.pytest-tmp-business-deploy/test_admin_knowledge_batch_sta0/01a04261-9f26-7f2e-86b4-242774e43f6d/failed.md`
  - `.pytest-tmp-business-deploy/test_book_scope_is_trusted_and0/01a04261-ee6a-7bb5-a49b-ad466e5703f7/book.md`
  - `.pytest-tmp-business-deploy/test_cleaning_batch_upload_rep0/01a04261-8d17-7db0-b288-79862c4a6da2/01a04261-8d2c-75c2-8668-fccfa528f972.md`
  - `.pytest-tmp-business-deploy/test_cleaning_strategy_and_fil0/sample.md`
  - `.pytest-tmp-business-deploy/test_cleaning_strategy_and_fil0/公众号.txt`
  - `.pytest-tmp-business-deploy/test_cleaning_worker_enqueues_0/01a04261-dbd8-7b20-8dfd-cf44ee487e93/lead.md`
  - `.pytest-tmp-business-deploy/test_conversion_markdown_root_0/batch-001/file-001.docx`
  - `.pytest-tmp-business-deploy/test_conversion_markdown_toggl0/batch-001/file-001.pdf`
  - `.pytest-tmp-business-deploy/test_course_import_generates_f0/catalog.md`
  - `.pytest-tmp-business-deploy/test_disabled_course_worker_do0/01a04261-e169-7b91-a82b-61583f4f40a6/catalog.xlsx`
  - `.pytest-tmp-business-deploy/test_empty_content_strategy_is0/01a04261-e7a6-7f22-8182-2daf0fb8f149/empty.md`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/fake.md`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/fake.pdf`
  - `.pytest-tmp-business-deploy/test_uploaded_file_signature_v0/sample.md`
  - `.pytest-tmp-business-deploy2/test_admin_knowledge_batch_sta0/01a04263-b0f1-75b7-a1f5-552b88757d56/failed.md`
  - `.pytest-tmp-business-deploy2/test_book_scope_is_trusted_and0/01a04264-1725-74a9-8574-eb0300635472/book.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_batch_upload_rep0/01a04263-989b-76c8-a60c-8972514910f9/01a04263-98ac-7649-adf6-16727831c665.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_strategy_and_fil0/sample.md`
  - `.pytest-tmp-business-deploy2/test_cleaning_strategy_and_fil0/公众号.txt`
  - `.pytest-tmp-business-deploy2/test_cleaning_worker_enqueues_0/01a04263-f7e3-7c2d-9ec3-35d1b53ded92/lead.md`
  - `.pytest-tmp-business-deploy2/test_conversion_markdown_root_0/batch-001/file-001.docx`
  - `.pytest-tmp-business-deploy2/test_conversion_markdown_toggl0/batch-001/file-001.pdf`
  - `.pytest-tmp-business-deploy2/test_course_import_generates_f0/catalog.md`
  - `.pytest-tmp-business-deploy2/test_disabled_course_worker_do0/01a04263-ffb9-7fdc-b696-3a02981760c3/catalog.xlsx`
  - `.pytest-tmp-business-deploy2/test_empty_content_strategy_is0/01a04264-0c8f-7def-a7f2-e1a9c31d5cb3/empty.md`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/fake.md`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/fake.pdf`
  - `.pytest-tmp-business-deploy2/test_uploaded_file_signature_v0/sample.md`
  - `AI管理服务当前状态与后续测试交接.md`
  - `Dockerfile`
  - `Dockerfile.ocr-offline`
  - `Dockerfile.offline`
  - `Dockerfile.ui-offline`
  - `ai_management-deploy-20260831.zip`
  - `ai_management/api/routers/admin_logs.py`
  - `docker-compose.ai.yml`
  - `docker-compose.ui.yml`
  - `test.md`
  - `test00.md`
  - `test11.md`
  - `test33.md`
  - `test66.md`
  - `tests/test_ai_management.py`
  - `tests/test_cleaning_release_contract.py`
  - `tmp/time_probe.db`
  - `uv.lock`
  - `测试.md`
  - `测试111.md`
  - `测试2.md`
  - `测试66.md`

## 2026-09-12T06:06:54+08:00 — 本轮共享模型清洗 Worker 的失败处理改为内容策略 fail-closed；Provider Batch 在本轮始终关闭，未执行 Batch、未改变其调用阈值或发布语义。

- **Purpose:** 本轮共享模型清洗 Worker 的失败处理改为内容策略 fail-closed；Provider Batch 在本轮始终关闭，未执行 Batch、未改变其调用阈值或发布语义。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 本轮相关回归与静态检查通过；未针对该特性执行外部发布。
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-12T20:07:33+08:00 — 共享 Worker 的失败阶段 allowlist 与小批量 Batch repair 的 realtime 实际计价已覆盖；Provider Batch 持续关闭，未执行 Batch、未改变正式发布语义。

- **Purpose:** 共享 Worker 的失败阶段 allowlist 与小批量 Batch repair 的 realtime 实际计价已覆盖；Provider Batch 持续关闭，未执行 Batch、未改变正式发布语义。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 模型清洗与大规模回归、241 项相关回归和完整 gov quality 通过；未执行 provider 或远端操作。
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-12T20:09:30+08:00 — 补全上次未完成的治理同步：共享 Worker 失败阶段 allowlist 与小批量 Batch repair 的 realtime 实际计价已记录；Provider Batch 持续关闭，未执行 Batch 或远端操作。

- **Purpose:** 补全上次未完成的治理同步：共享 Worker 失败阶段 allowlist 与小批量 Batch repair 的 realtime 实际计价已记录；Provider Batch 持续关闭，未执行 Batch 或远端操作。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 模型清洗与大规模回归、241 项相关回归和完整 gov quality 通过。
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-12T20:32:26+08:00 — 治理工具在内容指纹前排除 tmp 与 pytest 生成物，避免本地临时文件拖慢质量/同步；共享 Worker 的费用与失败阶段保护、Batch 关闭状态和发布语义不变。

- **Purpose:** 治理工具在内容指纹前排除 tmp 与 pytest 生成物，避免本地临时文件拖慢质量/同步；共享 Worker 的费用与失败阶段保护、Batch 关闭状态和发布语义不变。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 生成物不再进入 implementation 路径，治理模块编译和差异检查通过；后续完整质量将复跑。
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T00:10:24+08:00 — 共享模型清洗路径保留 provider 预算预留账本；新任务显式计数，历史不确定计数拒绝自动调用。

- **Purpose:** 共享模型清洗路径保留 provider 预算预留账本；新任务显式计数，历史不确定计数拒绝自动调用。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-model-workflow-r3-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T00:12:03+08:00 — 共享模型清洗路径保留 provider 预算预留账本；新任务显式计数，历史不确定计数拒绝自动调用。

- **Purpose:** 共享模型清洗路径保留 provider 预算预留账本；新任务显式计数，历史不确定计数拒绝自动调用。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-model-workflow-r3-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T00:15:42+08:00 — 仅格式化已验证的共享模型测试夹具与账本断言；行为边界、实现范围和远端门禁不变。

- **Purpose:** 仅格式化已验证的共享模型测试夹具与账本断言；行为边界、实现范围和远端门禁不变。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u06-fixture-r3-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T00:27:01+08:00 — 记录共享模型测试的 U06 质量证据和未完成治理门禁；不改变模型调用或远端边界。

- **Purpose:** 记录共享模型测试的 U06 质量证据和未完成治理门禁；不改变模型调用或远端边界。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-model-workflow-r3-20260913.xml; .codex/state/quality.json
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T00:37:01+08:00 — 治理指纹仅排除动态运行证据，保留业务源文件；canonical 覆盖已补齐。

- **Purpose:** 治理指纹仅排除动态运行证据，保留业务源文件；canonical 覆盖已补齐。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u11-dynamic-scope-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T02:02:51+08:00 — 新 reservation 保存 call ID、文档、transport、定价快照；realtime 仅结算匹配 reservation，缺 usage 继续保留预留，历史不完整账本 fail-closed。Batch 未结算预留继续占用预算。

- **Purpose:** 新 reservation 保存 call ID、文档、transport、定价快照；realtime 仅结算匹配 reservation，缺 usage 继续保留预留，历史不完整账本 fail-closed。Batch 未结算预留继续占用预算。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-model-workflow-round6-20260913.xml=101 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T02:54:41+08:00 — 共享模型清洗语义保持历史账本 fail-closed；本轮只新增咨询派生字段对无依据付费条件的本地丢弃规则。Batch 运行行为、费用和远端验收未执行。

- **Purpose:** 共享模型清洗语义保持历史账本 fail-closed；本轮只新增咨询派生字段对无依据付费条件的本地丢弃规则。Batch 运行行为、费用和远端验收未执行。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-consultation-after-20260913.xml: 37 passed; pytest-u02-u04-u07-u09-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T03:19:58+08:00 — 仅修正测试在执行时生成 future manifest 时间；未启用 Batch、未改变模型或费用行为。

- **Purpose:** 仅修正测试在执行时生成 future manifest 时间；未启用 Batch、未改变模型或费用行为。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-time-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T03:21:49+08:00 — future manifest 测试在执行时构造；仅格式化，模型、Batch、费用和远端行为不变。

- **Purpose:** future manifest 测试在执行时构造；仅格式化，模型、Batch、费用和远端行为不变。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-time-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T03:33:01+08:00 — 共享测试 helper 在工作区临时目录写入并清理 env/manifest；未改变 Batch、模型、费用或远端行为。

- **Purpose:** 共享测试 helper 在工作区临时目录写入并清理 env/manifest；未改变 Batch、模型、费用或远端行为。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-default-temp-after-20260913.xml: 12 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T03:57:08+08:00 — 本轮未改变 Qwen Batch 行为、Batch 开关或 provider transport；仅新增咨询 advice 主体反例和隔离镜像不可变 ID 启动前检查，Batch 仍关闭。

- **Purpose:** 本轮未改变 Qwen Batch 行为、Batch 开关或 provider transport；仅新增咨询 advice 主体反例和隔离镜像不可变 ID 启动前检查，Batch 仍关闭。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-u08-boundary-full-after-20260913.xml: 58 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T03:59:05+08:00 — 仅格式化隔离 manifest 测试夹具；未改变 Qwen Batch 行为、开关或费用边界。

- **Purpose:** 仅格式化隔离 manifest 测试夹具；未改变 Qwen Batch 行为、开关或费用边界。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-u08-boundary-full-after-20260913.xml: 58 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T04:52:01+08:00 — 共享模型清洗改为 UTF-8 保守输入预算预留，并拒绝耗尽 Worker lease 的精确重试，保持每文档两次 provider 调用上限。

- **Purpose:** 共享模型清洗改为 UTF-8 保守输入预算预留，并拒绝耗尽 Worker lease 的精确重试，保持每文档两次 provider 调用上限。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T05:47:26+08:00 — 精确 retry 释放旧 delivery lease，当前 token 围栏 reservation、文档写入和完成；provider 两次上限仅由文档计数和账本决定。

- **Purpose:** 精确 retry 释放旧 delivery lease，当前 token 围栏 reservation、文档写入和完成；provider 两次上限仅由文档计数和账本决定。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review5-worker-regression-20260913.xml: 7 passed; pytest-review5-affected-full-r3-20260913.xml: 153 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T06:22:22+08:00 — 共享 realtime Worker 在限流后续租并让 lease 丢失绕过修复与失败写入；Batch 业务语义未变。

- **Purpose:** 共享 realtime Worker 在限流后续租并让 lease 丢失绕过修复与失败写入；Batch 业务语义未变。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T07:17:50+08:00 — 保留历史 Batch 验收记录，但禁止用过期 10 条、review fallback、清理或 Batch 条款驱动当前共享代码或 jx 操作。

- **Purpose:** 保留历史 Batch 验收记录，但禁止用过期 10 条、review fallback、清理或 Batch 条款驱动当前共享代码或 jx 操作。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review7-doc-contract-r2-20260913.xml: 13 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T08:07:57+08:00 — Documented the current superseding ledger boundary: Batch remains disabled and incomplete historical provider accounting is fail-closed without mutating history.

- **Purpose:** Documented the current superseding ledger boundary: Batch remains disabled and incomplete historical provider accounting is fail-closed without mutating history.
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`

## 2026-09-13T09:17:54+08:00 — Shared model worker now rejects reservations outside the task batch and carries cross-file final-content candidates only to exact remote verification; no Batch service is enabled.

- **Purpose:** Shared model worker now rejects reservations outside the task batch and carries cross-file final-content candidates only to exact remote verification; no Batch service is enabled.
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review8-model-full-r5-after-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_release_acceptance_contract.py`

## 2026-09-13T09:18:14+08:00 — Shared model worker now rejects reservations outside the task batch and carries cross-file final-content candidates only to exact remote verification; no Batch service is enabled.

- **Purpose:** Shared model worker now rejects reservations outside the task batch and carries cross-file final-content candidates only to exact remote verification; no Batch service is enabled.
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review8-model-full-r5-after-20260913.xml
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_release_acceptance_contract.py`

## 2026-09-13T09:31:42+08:00 — Cross-file shard planning now selects a matching verified candidate instead of only the newest row; provider cost summaries validate batch document ownership.

- **Purpose:** Cross-file shard planning now selects a matching verified candidate instead of only the newest row; provider cost summaries validate batch document ownership.
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_release_acceptance_contract.py`

## 2026-09-13T10:38:23+08:00 — 跨文件Dify shard将远端ID明确区分为候选、共享或当前来源拥有；删除、replacement和延迟清理仅在无其他live本地引用时作用于远端对象。Provider reservation、日/累计费用按task batch的完整document集合对账，任何漏账历史fail-closed。

- **Purpose:** 跨文件Dify shard将远端ID明确区分为候选、共享或当前来源拥有；删除、replacement和延迟清理仅在无其他live本地引用时作用于远端对象。Provider reservation、日/累计费用按task batch的完整document集合对账，任何漏账历史fail-closed。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review10-focused-r5-after-20260913.xml; pytest-review10-affected-full-r2-20260913.xml (182 passed)
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_release_acceptance_contract.py`

## 2026-09-13T11:22:39+08:00 — 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。

- **Purpose:** 跨来源共享远端 shard 的删除保护改为只查询 live DifyPublicationShard；继续保证最后本地引用才精确删除远端成果，未改变 Batch=false 运行边界。
- **Affected modules:**
  - `model-cleaning-policy — 模型清洗策略规划`
  - `model-cleaning-orchestration — Batch 生命周期与轮询`
  - `model-cleaning-contract-tests — 模型清洗契约测试`
  - `jx-cleaning-acceptance — jx 清洗验收证据`
  - `activity-chatflow — 活动意图与 Chatflow`
  - `strategy-contracts — 三类策略契约与发布`
  - `cleaning-contract-tests — 模型清洗契约回归`
  - `jx-acceptance-docs — jx 验收与文档`
- **Before:** 共享引用 guard 包含普通 replacement 历史行，可能混淆 post-shard ownership 与历史关联。
- **After:** guard 仅基于 live shard，跨文件复用、replacement 与延迟删除仍按最后引用保护。
- **Impact / Notes:** None
- **Validation:** pytest-review10-activity-cleanup-r1 4 passed；pytest-review10-affected-full-r3 202 passed
- **Changed paths:**
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `dify/商业顾问_统一修复版_Dify-1.13.3.yml`
  - `dify/商业顾问（当前使用版本）.yml`
  - `knowledge_cleaner/dify.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_business_advisor_unified_dsl.py`
  - `tests/test_dify_activity_contract.py`
  - `tests/test_large_scale_cleaning.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_release_acceptance_contract.py`
