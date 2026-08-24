# AI 管理服务当前状态与后续测试交接

> 更新时间：2026-08-22
> 用途：保存最新线上事实、回滚点、数据状态和开放事项。新任务先读 `AGENTS.md` 与 `docs/README.md`，再按功能域读取本文顶部最新状态。  
> 本文不是需求或设计基线；权威顺序以 `docs/V2决策记录.md` 和 `docs/README.md` 为准。第 1～8 节为历史过程记录，不再作为当前执行指令。

## 0.76 2026-08-22 本地千问 Batch 上传门禁与运行策略（当前任务）

- 策略已先在文档中固定：本地 `.env` 使用 `BAILIAN_BATCH_ENABLED=true`；达到 `CLEANING_BATCH_CANDIDATE_THRESHOLD=100` 或 `CLEANING_BATCH_TOKEN_THRESHOLD=200000` 时，四个内容模型策略可选择百炼 Batch；`recent_activity` 仍固定 realtime。
- 上传入口限制与百炼 Provider Batch 输入限制分开：`MAX_UPLOAD_FILE_MB=200`（单文件）、`MAX_UPLOAD_FILES=100`（单批文件数）、`MAX_UPLOAD_BATCH_MB=200`（单批上传总大小）；`CLEANING_BATCH_MAX_BYTES` 仍为 Provider Batch JSONL 分片上限，当前代码默认 `400MiB`，不因上传门禁改为 200MB。
- 本次本地 `.env` 已按该策略写入；jx 已完成 `.env` 备份、精确配置更新和六个相关核心服务的 `--force-recreate`。备份目录为 `/opt/ai-management/backups/20260822-100452-qwen-batch-upload-config`；运行容器实际为 `MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=200`、`BAILIAN_BATCH_ENABLED=true`，候选/Token 阈值为 100/200000。API、普通 Worker、realtime/Batch Worker、publication Worker、Scheduler 均在同源镜像 `jx-now-ai-management:20260822-072927-timezone-beijing` 上运行；本次未修改数据库、Redis、Dify 数据、上传卷或 OCR 服务。不修改代码默认、不修改 `.env.example`；生产/已批准基线仍为 Batch 默认关闭、单批 10 文件/500MB。
- 当前执行状态：本地配置解析检查通过；本地 Docker 因缺少外部 Dify `docker_default` 网络未重建，且本机没有对应 Dify Compose/容器，不能用临时空网络冒充成功。jx 运行态复核通过：`docker_default` 存在，六个相关核心服务均为 Up 且本次重建后 `restart=0`，API 容器内 `/health/ready` 返回 `ready` 且 `database/redis/dify` 均为 `true`；经 Nginx 的 `/ai-management-test/` 返回 HTTP 200。本地 Compose 尝试因外部网络缺失在创建服务前失败，没有生成本地容器或改动数据。
- 策略选择烟测：在 jx Batch Worker 容器内以 `batch_enabled=true`、候选数 100 调用 `choose_call_mode`，`recent_activity` 返回 `realtime`，个人商业咨询、微信公众号、知识星球、小报童均返回 `batch`。本结果只验证当前代码的模式选择和运行配置，不代表已发起真实 Provider Batch 或完成新样本清洗。
- jx 全链路限制核对：测试页经 Dify Nginx 的 `/ai-management-test/` 访问，Nginx 当前 `client_max_body_size=128M`；测试页容器的 `AI_MANAGEMENT_BASE_URL=http://ai-management-api:8000`，上传从 UI 服务到 API 走 Docker 内部网络。可信后端直连 `172.30.157.197:8000` 不经过该 Nginx；若调用方经 Nginx 的 HTTP 上传桥接，则 128M 可能先于应用的 200MB 门禁生效。
- jx 运行态证据：配置切换后的复核确认 API、普通 Worker、realtime/Batch Worker、publication Worker、Scheduler 使用同一核心镜像，相关服务重建时间为 `2026-08-22T02:04:52Z`，关键日志错误计数为 0；API `/health/ready` 为 `ready`，数据库、Redis、Dify 均可达。Nginx 的 `client_max_body_size=128M` 未修改，因此经 Nginx 的 HTTP 上传路径仍可能先于应用层 200MB 门禁拒绝大请求；可信后端私网直连路径不经过该限制。

### 本节变更记录

- 修改原因：按本轮配置切换复核结果，更新 jx Batch 开关、上传门禁、相关服务重建状态和本地 Docker 网络阻断，避免继续依据过时的“远端待写入/Batch=false”事实操作。
- 修改前：本文记录 jx `BAILIAN_BATCH_ENABLED=false`、单批总大小 500MB 且远端写入/重建待执行；未记录本轮 jx 容器实际环境回读和策略选择烟测。
- 修改后：记录 jx 运行容器实际为 Batch 开启、单批 200MB，API ready/依赖可达，六个相关服务同源运行；记录本地外部网络缺失导致 Compose 未启动，并明确未进行重复远端写入。
- 修改原因：按用户确认继续执行远端变更，并把备份、服务范围、健康检查和 Nginx 中间层限制写入可复用交接。
- 修改前：jx 仅完成只读预检，`BAILIAN_BATCH_ENABLED=false`；`.env` 尚未建立本轮专用配置备份，服务未重建。
- 修改后：完成 `/opt/ai-management/backups/20260822-100452-qwen-batch-upload-config` 配置备份；更新并回读 `MAX_UPLOAD_FILE_MB=200`、`MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=200`、`BAILIAN_BATCH_ENABLED=true`、`CLEANING_BATCH_MAX_REQUESTS=40000`、`CLEANING_BATCH_MAX_BYTES=419430400`；重建六个相关核心服务并通过 API ready、依赖可达、Nginx 测试页 HTTP 200 验证。Compose 的 orphan warning 未执行清理，OCR/test-ui 未纳入本次重建范围。
- 修改原因：明确本地百炼 Batch 验收的开关、候选阈值、上传文件数/总大小与 Provider JSONL 分片上限，避免下次把不同层级的 100/200 约束混用。
- 修改前：本文最新状态仅记录远端样本和代码/质量事实，本地 `.env` 为 `MAX_UPLOAD_FILES=10`、`MAX_UPLOAD_BATCH_MB=500`，未显式设置 `BAILIAN_BATCH_ENABLED`。
- 修改后：新增本地与 jx 目标策略、当前 jx 差异和远端变更前置门禁；代码默认和生产基线不变。

## 0.75 2026-08-21 接管中断会话、清除旧数据并完成 15 个新样本验收（当前最新）

- 已按用户授权清除旧数据且不做备份：旧 `general` Dify 文档清空为 `0`；ai-management 数据库旧业务数据、清洗批次、模型任务、发布分片、会话/画像/消息、系统事件等均清除；Redis DB 5 旧键在停写复核后清空为 `0`；旧上传目录、转换缓存和 12 个 ai-management 容器日志文件已清空。数据库 schema/migration 结构保留，课程 Dataset 原本为 `0` 且未触碰。
- 清除完成后仅处理本轮指定的 7 个策略和 15 个样本。最终保留 7 个已完成批次、15 个文件（13 个成功、2 个策略排除）、96 个本地条目（83 个成功、13 个排除）、4 个模型任务（全部 completed）、72 个发布分片（全部 succeeded）；无 active、failed 或 review_required 条目。
- `general` Dify 最终为 `83` 篇：83/83 `indexing_status=completed`、83/83 enabled、无重复 ID；本地成功条目的远端 ID 集合与 Dify 全量 ID 集合一致；所有成功条目的父/子分块计数均大于 0；按策略实际字段回读 metadata，83/83 均有清洗策略、规范版本、内容格式和来源等字段。课程 Dataset 仍为 `0`。
- 样本结果：个人商业咨询 `3/3` 文件成功、5 个条目成功；知识星球 `3/5` 文件成功、2 个文件策略排除、3 个条目成功；微信公众号 `1/1` 文件成功、46 个条目成功、7 个条目排除；小报童 `2/2` 文件成功、23 个条目成功、4 个条目排除；最近活动 `1/1` 文件成功、3 个条目成功；资料钩子 `2/2` 文件成功；书籍 `1/1` 文件成功。
- 测试中发现并统一处理：批次汇总出现 `partially_completed` 但子任务仍运行的轮询误判已修正；另一个外部上传客户端在测试完成后重复提交，已停止 Dify 网关写入、按精确批次 manifest 删除 4 个重复批次及 3 篇重复 Dify 文档，再恢复网关。最终观察窗口未发现新的重复上传请求。
- 当前健康状态：`health/live=ok`、`health/ready=ready`（database、Redis、Dify 均可达），测试 UI HTTP `200`。当前 Redis 中的清洗缓存、Celery 结果和 Kombu 绑定键均为本轮新处理产生的运行时数据，不属于已清除的旧数据；当前 `system_event_log` 的 40 条也是本轮新处理审计记录，旧日志已清除。
- 脱敏问题记录：[20260821 general reset/sample cleaning issues](var/reports/20260821-general-reset-sample-cleaning/issues.jsonl)。未执行 Git push、无关资源发布或课程数据修改。

### 本节变更记录

- 修改原因：接管中断会话后，按用户最新授权清除 ai-management 旧数据库/Redis/日志和 `general` 知识库旧文档，并完成指定 15 个未来样本的清洗、发布和最终回读；同步记录轮询误判与重复上传问题的统一处理结果。
- 修改前：本文最新状态仅记录本地代码/质量完成，远端旧数据清理、指定样本处理和 Dify 全量验收均未完成。
- 修改后：新增 0.75，记录旧数据清除边界、当前新数据计数、Dify 83 篇全量验收、服务健康、问题清单和课程 Dataset 未触碰的事实。

## 0.73 2026-08-19 文档清洗策略数据与知识库精确清理（历史）

- 上一轮只完成了只读计划和备份，未进入删除阶段；本轮已修正 `scripts/jx_prebaseline_clean.sh`，清理后额外执行 release `snapshot`，当 manifest 覆盖某个 Dataset 的全部文档时，回读非零即失败，不再把“计划通过”当作清理完成。
- jx 使用精确 `manual_incident_cleanup` manifest 清理：general Dify 文档 `35 → 0`；course Dify 文档保持 `0`；清理批次 `23` 个及其文件 `51`、文档 `70`、模型任务 `16`、发布分片 `5` 均为 `0`。
- `ai:cleaning:*` Redis 键为 `0`，上传卷顶层清洗目录为 `0`；Redis 总键数仍为 `448`，其中包含业务/运行时键，未执行整库清空。`health/ready` 为 `ready`，数据库、Redis、Dify 均可达。
- 本次未删除商业会话、画像、消息、`AdminSetting`、样式配置或非 Dify 外部删除任务；备份为 `/opt/ai-management/backups/20260819-175300-general-cleaning-domain-reset`，执行报告为 `/opt/ai-management/var/reports/v2/20260819-183500-general-cleaning-domain-reset`。

## 0.74 2026-08-20 模型契约失败人工审批与跨源去重修复（当前开发状态）

- 模型拆分/发布名称现在始终使用原始文件安全 stem 作为前缀；同一知识库、策略和规格内按规范化最终正文跨源/跨批次去重，重复项进入 `excluded` 并保留去重范围审计；发布规划和源文件发布共享契约级锁。
- 模型结果未通过契约校验时写入服务端 `audit_root/model-review` 审核产物，保留 finalize 前原始模型 payload、实际请求上下文（模型/Prompt 版本/请求块）指纹、原始文件名、模型结果指纹、稳定错误码和字段路径；审核目录/文件权限分别收紧为 `0700/0600`，单文件上限 16 MiB。条目保持 `review_required`，源 Markdown 仅保存在本地待审批状态，不自动进入 Dify。显式 approve/exclude 仍复用现有内部复核入口，对外接口未新增字段；review 行使用数据库锁，exclude 对已有远端 ID 建立精确补偿删除任务。
- Batch 轮询不再把供应商 `cancelling` 视为终态；管理员取消、提交和轮询在任务状态边界使用任务行锁，上传后/创建前/最终切换前复检取消状态，取消获胜时精确清理已创建的供应商 Batch 和输入/输出/错误文件，提交或修复异常恢复不会复活已取消任务。
- 本地修复后的聚焦清洗/发布/活动回归、全量单元/集成回归和文档索引检查已通过；首次测试暴露的旧自动兜底断言、旧 SHA-256 断言和文档索引过期问题已集中修复。jx 清理、部署和真实样本验收尚未在本节宣称完成，测试数据按用户要求保留。
- 项目级 `gov quality` 的最新通过结果以 `.codex/state/quality.json` 为准；本轮取消/轮询恢复、审核请求指纹和 HTTP/WORKERS 索引修复均已纳入最终质量流程。当前本地 wheel 已构建并通过 ZIP 完整性检查，SHA-256 为 `a25dcb2bc5a88b6e47406dee2c84fd0f02e49bcdd1b83076d09fa5823481a6c6`；jx 发布和真实样本验收仍未开始。

### 本节变更记录

- 修改原因：修复模型契约失败自动兜底入库、模型标题覆盖原始文件名、跨源/跨批次重复发布，并按用户要求保留审核材料。
- 修改前：契约失败会恢复源 Markdown 并进入发布队列；模型拆分发布可能使用模型标题；重复保护仅覆盖同源/同批次边界。
- 修改后：契约失败进入 `review_required` 并生成策略+原始 stem 审核产物；发布名称固定原始 stem；同契约正文跨源去重并加锁；本地回归和文档索引通过。

### 本节变更记录

- 修改原因：接收独立复核后补齐审核材料原始模型结果、权限崩溃安全、历史分片命名一致性，并重新完成质量门禁准备。
- 修改前：finalize 阶段会移除原始模型 payload，审核材料只能稳定记录路径和摘要；审核文件权限设置晚于原子替换；历史分片可能先用旧摘要生成 Dify 名称。
- 修改后：原始 payload 在进程内安全暂存并写入审核 artifact，目录/临时/目标权限为 `0700/0600` 且有 16 MiB 上限；历史分片先规范化并重算摘要后再生成名称；最终质量指纹为 `7f196f54aa0fa2d8b2562b699ba3e7283e9908a8a70a35f7263150c3c642680f`。未执行远端写入、清理或发布。

### 本节变更记录

- 修改原因：继续修复独立复核发现的 Batch 取消生命周期竞态与审核临时文件权限创建窗口。
- 修改前：供应商 `cancelling` 会被过早移出轮询；取消请求可能与提交交错产生孤儿 Batch；临时审核文件先按默认权限创建再 chmod。
- 修改后：`cancelling` 保持 active；提交/取消使用任务行锁并在远端上传/创建边界复检，异常恢复不会覆盖已取消状态；审核临时文件使用创建时 `0600` 的文件描述符。最终质量指纹为 `6e8fe21bfb9ba15948d1554c7559e7faaed1a170c314166334e2986883473a56`，wheel SHA 为 `a25dcb2bc5a88b6e47406dee2c84fd0f02e49bcdd1b83076d09fa5823481a6c6`。未执行远端写入、清理或发布。

### 本节变更记录

- 修改原因：用户反馈知识库未清除；核实上一轮删除未执行后，补充 Dify 清理后回读门禁并重新执行精确清理。
- 修改前：本文最新状态为 0.72；清理脚本没有在包装层记录并强制校验 Dataset 清空结果，上一轮停留在只读计划/备份阶段。
- 修改后：新增 0.73；记录脚本修复、35 篇 general 文档删除、清洗数据库/Redis/上传目录归零、服务健康和保留边界。

## 0.72 2026-08-19 ai-management 系统事件日志精确清理（历史）

- 已确认本地与 jx `/opt/ai-management` 均存在 `scripts/jx_clear_system_event_logs.py`，SHA-256 为 `0d48642fe93bbc0c834c56f448c52da05a478cd6e11ee2f5fdef287d7bff7355`。该脚本的范围仅为 `system_event_log` 表，不清理 Docker stdout/stderr、宿主机 journal、Redis、Dify 或业务数据。
- jx 清理前 `system_event_log` 为 `227` 条；停用写入该表的 ai-management 应用/Worker 后，脚本备份并删除 `227` 条，删除后复核为 `0`。备份目录为 `/opt/ai-management/backups/20260819-clear-system-event-logs-pre`，目录权限 `700`，备份文件权限 `600`。
- 清理期间第一次恢复命令遗漏 Compose `test` profile，导致容器短暂退出；未改动数据。随后使用 `--profile test` 恢复 API、全部 Worker、Scheduler、测试 UI 及两个 methodology Worker，容器均为 `running`；API 容器内 `/health/ready` 为 `ready`，数据库、Redis、Dify 均可达。
- 本次未清理 Docker/宿主机日志，也未删除数据库、Redis、Dify、上传目录或持久卷。脱敏执行记录：[20260819 日志清理报告](var/reports/20260819-clear-system-event-logs/README.md)。

### 本节变更记录

- 修改原因：按用户要求确认并执行 ai-management 日志清理，记录脚本范围、备份、删除计数、恢复异常及最终线上状态。
- 修改前：本文最新状态为 0.71，未记录本次系统事件日志清理和恢复过程。
- 修改后：新增 0.72；明确仅清理 `system_event_log`，记录 `227 → 0`、备份位置、服务恢复和未触碰的其它日志/业务数据边界。

## 0.71 2026-08-19 文档清洗策略熔断、咨询 PDF OCR 修复与 jx 回归（历史）

- 根因已闭环：课程报错来自旧的显式课程上传调用方，不是用户选择 general 策略后被错误映射；此前兼容入口仍允许写课程知识库。现在线上课程知识库和 `course_information`、`learning_route`、`course_route_catalog` 均在创建批次前返回 `409 STRATEGY_DISABLED`，不创建本地批次/文件，不调用课程解析、模型或 Dify。存量排队课程任务也由 Worker 入口熔断，不再进入课程策略。
- 咨询 PDF 含图片不再因为“有图片”触发人工隐私确认；咨询图片统一进入 OCR，OCR 失败、质量失败、残余隐私信息仍保持阻断。历史 7 条仅由旧图片人工确认门禁产生、且文件与知识库文档均已成功的 open 事件已逐条通过既有 resolve 接口收敛；没有把真实 OCR/隐私失败标记为已解决。
- jx 已发布完整 wheel release `20260819-cleaning-course-1405`；发布前备份为 `/opt/ai-management/backups/20260819-cleaning-course-1405-predeploy`，发布报告为 `/opt/ai-management/var/reports/v2/20260819-cleaning-course-1405-deploy`。API、清洗/模型/发布 Worker、Scheduler、UI、OCR 使用本 release；health/ready、数据库、Redis、Dify 可达性和运行时导入通过。
- 发布过程中的两项环境问题已统一处理：首轮暂存漏传既有 `k7b8c9d0e1f2_consultation_methodology_registry.py`，补齐后未产生服务副作用；第二轮被资源保护检测到暂时的 D 状态 I/O 进程并自动回滚，资源恢复后重试成功。没有跳过资源门禁或强制切换。
- 用户确认的课程/路线旧数据已按精确课程域执行清理，执行前备份为 `/opt/ai-management/backups/20260819-143035-course-document-data-reset`。清理后课程 Dify 文档、课程清洗批次、课程/路线/集合、相关 Redis 键和上传目录均为 `0`；general Dify 文档保留 `34`，未清理 general 或业务数据。
- 指定个人商业咨询批次已通过线上 retry 接口重试并完成：批次 `completed`、文件 `succeeded`、清洗文档 `succeeded`、Dify 文档 ID 已持久化、模型任务 `completed`、重试后总尝试次数为 `2`、无错误码。线上合成课程写入探针返回 `409 STRATEGY_DISABLED` 且无批次 ID。
- 本地 `gov quality` 与独立静态审查均通过；本次没有 `dify/` DSL 变更，不需要同步导入或发布 DSL。运行结果和远端报告均不写入密钥、Token、源正文、真实用户标识或受保护映射。

### 本节变更记录

- 修改原因：完成文档清洗今日错误的全链路根因定位、代码修复、统一测试问题处理、jx 完整发布、课程旧数据精确清理、指定咨询批次重试和历史 PDF 门禁事件收敛。
- 修改前：本文最新状态为 0.70，未记录课程入口熔断、咨询 PDF OCR 策略、指定批次重试、课程域清理和本次发布报告。
- 修改后：新增 0.71；记录根因、线上行为、备份/发布/清理证据、测试问题及处理结果，并明确 DSL 未变更。

## 0.70 2026-08-19 咨询会议方法论 v3 18 份样本页面全链路验收（历史）

- 本次在用户已登录 Chrome 的项目测试页完成 6 批上传，共 18 份咨询会议总结；18/18 文件成功，生成 18 个 `consulting_case`、13 个 `methodology_candidate`，共 31 个成功清洗文档，31/31 获得 Dify 文档 ID。
- Dify 文档详情已回读候选方法 Metadata；商业顾问 Chatflow 已发布。测试页 5 个查询均完成，显式要求方法论+案例的产品、产品交付、社群活动问题均返回结构化方法与案例回答；普通产品问法返回行动步骤，但未显示可见的内部方法/案例标记。
- 本轮没有 `validated_universal_methodology`。候选方法必须跨不同来源文档合并、通过冲突/证据/去重检查并达到阈值后，才晋升为通用方法论；不能从单份会议总结或单批次结果人工改名得到。跨批次 Registry 会自动合并候选，不要求一次性处理几千份历史文档。
- Dify 日志页对 5 个查询均显示 `Success`，但日志详情只提供输入和最终回答，未提供节点级执行轨迹。因此当前证据结论为“答案级链路已验证、节点级路由未由日志独立展示”；不把回答文本当作节点命中证据。画布已包含方法论检索、咨询案例检索、聚合和证据压缩节点。
- 一次模型契约校验失败已通过条目详情重试修复；页面上传控件的上一批选择残留问题已通过重新认领页面、确认空状态并重新上传最后一批解决。最终 18/18 成功。所有相关 jx 服务已用修正后的完整 release 重建，ready、迁移、运行时导入和 Worker 稳定性复核通过，重启次数为 0。
- 本轮数据和样本按用户要求保留，未做清理。用户修改的 ai-management 配置未读取、未输出、未写入报告。脱敏验收报告：[咨询会议方法论 v3 页面验收报告](var/reports/consulting-methodology-v3/e2e-20260819-redacted.md)。
- 本地 `gov quality` 已通过；独立审查 Agent 因容量、线程限制或超时未产生可用结论，治理 `gate-status` 仍为 `BLOCKED`。这只表示治理证据尚未取得，不代表功能测试失败；不得把本轮写成治理全门禁通过。

### 本节变更记录

- 修改原因：按项目 `AGENTS.md` 补录咨询会议 v3 从页面上传到商业顾问测试的真实结果，并明确候选方法、通用方法论和 Dify 证据边界。
- 修改前：本文最新状态只覆盖 2026-08-18 的其它功能，未记录本轮 18 份样本页面验收、跨批次晋升结果和上传/部署排障。
- 修改后：新增 0.70；同步记录 6 批、18 文件、31 文档、13 候选方法、0 通用方法论、Dify Metadata 回读、Chatflow 发布和答案级测试结果；未写入密钥、Token、原始正文、原始敏感文件名或受保护映射。

## 0.66 2026-08-18 最近活动同名更新安全替换发布与回归（当前最新）

- `recent_activity` 已修正两项问题：Dify 名称使用活动名称作为可读前缀，并追加稳定 `event_id` 与内容版本摘要；同名活动内容/状态更新采用“新文档先发布、旧文档后删除”，旧文档删除失败由精确外部删除任务接续，不丢失新版本；同源重复 `event_id` 在发布前进入明确终态失败。
- 本地验证通过：活动策略、发布契约、替换成功/失败、旧文档删除失败、提交后崩溃恢复、重复 `event_id`（含通用内容去重前置场景）、版本摘要 provenance 隔离和外部删除重试退避及 Dify/DSL 契约测试通过；最终 `gov quality` 通过，指纹为 `31954761677612922f1ae40259f7a2e91d695841f369a7d45a3bcf19a721947d`。DSL 未变更，不需要同步 Dify。
- jx 已发布最终 release `quick-20260818-050347-ab578b3727b3`，源指纹为 `ab578b3727b30aa8b8a9f6a4922a2c985d67329ecfd45de028a5674d61b35598`；报告为 `/opt/ai-management/var/reports/v2/quick-20260818-050347-ab578b3727b3-quick-deploy`，发布前备份为 `/opt/ai-management/backups/quick-20260818-050347-ab578b3727b3-predeploy`。发布前 OCR 安全门禁曾阻止只读计划，随后仅以进程级 `JX_OCR_DEPLOYMENT_ENABLED=true` 完成计划与发布，未写入服务器 `.env`。
- jx 合成回归通过：同一活动名称和稳定 `event_id` 的 v1/v2 两个批次均 `completed`；v2 将状态由正常改为取消，Dify 回读名称符合“活动名称前缀 + event_id + 版本摘要”，更新后旧版本不可见、新版本保留。精确清理后两个批次的本地记录、文件、文档、分片、外部删除任务、Redis 键和上传目录均无残留；课程/路线目标为 `0`，正式 general/course 数据未删除；`health/ready` 仍为 `ready` 且数据库、Redis、Dify 可达。
- 当前仍按 ADR-036 执行边界：课程/学习路线不是新商业顾问聊天和测试页的验收对象，历史管理/显式兼容接口仍保留；本轮日志中出现课程/路线内容不能反推本功能已恢复或必须扩测，需先区分历史数据/旧接口调用与新链路。

## 0.65 2026-08-17 朋友圈与日常写作案例目录 jx 发布交接（历史）

- `tool-examples` 已发布到 jx：release `quick-20260817-081805-b051c4d3304f`，源指纹为 `b051c4d3304f...`；报告为 `/opt/ai-management/var/reports/v2/quick-20260817-081805-b051c4d3304f-quick-deploy`，发布前备份为 `/opt/ai-management/backups/quick-20260817-081805-b051c4d3304f-predeploy`。发布使用 `--keep-data`，没有清理数据库、Redis、上传文件或 Dify 数据。
- 发布前发现 Windows `Compress-Archive` ZIP 在服务器 Python 3.12 安全校验中呈现反斜杠路径，已改为显式 POSIX ZIP 路径并通过远端 CRC/安全校验；随后发现服务器 `knowledge_cleaner/` 源码落后于现有 wheelhouse，已先备份 `/opt/ai-management/backups/quick-20260817-081332-knowledge-cleaner-before`，再从已验证 wheelhouse 对齐源码。第一次失败发布未切换运行容器，第二次发布通过。
- 发布过程仅进程级设置 `JX_OCR_DEPLOYMENT_ENABLED=true` 以通过既有 OCR 安全门禁，没有把该开关写入服务器 `.env`；OCR 定时器发布后仍为 `enabled/active`。Core/UI/Worker/Scheduler 容器均运行本 release，API `health/ready=200`、测试页 `HTTP 200`。
- jx 端回归通过：用户 `moments` 案例接口返回 3 个默认案例，管理员 `writing` 案例接口返回 3 个默认案例；临时自定义案例完成创建、修改、启用、删除，未保留烟测数据。DSL 文件未变更，不需要 Dify 导入或发布。
- 本地 `ai_management.zip` 已重建为服务器兼容的 POSIX 路径包，SHA-256 为 `0F14670A45FC7ECCC1BC606D79BABA5078087A82353A8EA3DA937A3C2A7F3E6E`；canonical 全套测试的 6 项既有缺失脚本/样本失败仍保持原记录，未归因于本 Feature。本节及报告均不写入密钥、真实用户标识或受保护映射。

## 0.64 2026-08-17 朋友圈与日常写作案例目录本地实现交接（历史）

- 本地已完成 `tool-examples`：朋友圈顾问和日常写作各提供 3 个完整默认案例；用户读取、管理员 CRUD/恢复接口、Streamlit 卡片一键生成和管理维护 UI 已实现。
- 案例按 `moments/writing` 写入现有 `AdminSetting` JSON；删除不自动回填，恢复接口只补缺失默认项并保留自定义项。Dify DSL 未修改，运行时案例不需要重新导入或发布 Dify。
- 本地相关测试、路由契约、文档索引、feature-bound quality、format/lint/compile 已通过；Streamlit smoke 已验证两个工具的完整 request 展示、点击后复用既有 `/run`、以及空目录仍可手动输入。因本地未启动 Dify 网关，生成阶段记录了外部依赖 502；API/UI 进程已停止，测试案例已恢复默认。
- canonical 全套测试仍有 6 项既有基线失败：2 个工作树缺失的清理脚本、4 个缺失的咨询样本文件。本轮未伪造或补建这些无关资料。
- 发布归档已从当前源码重建：根目录 `ai_management.zip` 已通过 58/58 Python 文件逐文件匹配和解压导入检查，旧 ZIP 已保留在本地报告目录；尚未上传 jx，仍需完成最终 release gate。
- jx 尚未部署本 Feature；该历史快照形成时仍待发布门禁、远端备份和精确命令确认。本文不写入密钥、App ID、会话 ID、真实用户标识、原始正文或受保护映射。

## 0.62 2026-08-17 最近活动功能完成与新对话交接（历史）

- 本任务已完成：`recent_activity` 已接入现有清洗/发布链路，jx 已部署 release `20260816-activity-59d963`，当前 feature `recent-activity` 的治理状态为 `done`，完成门禁 `ready=true`。
- 当前 jx 已实际使用用户同步的商业顾问 App Key；运行中容器配置与服务器 `.env` 的脱敏指纹一致，`health/ready=200`、Dify `/parameters=200`，核心服务仍为同源 release。密钥明文、App ID、会话 ID、真实用户标识和原始样本不写入本文。
- 当前 Chatflow 已发布并生效：活动请求使用动态中国时区三十天窗口；正常活动按时间区间和状态过滤，取消/延期按状态过滤；延期新日期和延期未定均不复用旧日期；结果最多三条、按时间排序并去重；案例请求优先于活动请求。
- 测试页真实全链路已完成：活动样本正常 `2/2`、状态样本 `4/4`；正常/取消/延期、普通商业问题负例、会议真实案例重叠负例均返回最终答案，诊断确认 `message_end.status=completed`。延期结果覆盖新日期与延期未定，未泄露旧日期。
- 本轮合成测试会话已精确清理：删除 `10` 个，剩余 `0`，删除失败 `0`；未删除正式数据、Dify Dataset、应用或持久卷。
- 本地质量与治理检查已通过：完整 `gov quality`、活动/DSL 聚焦测试、DSL 构建检查、项目索引检查和差异检查均通过。后续对话若要继续开发，先读取 `AGENTS.md`、`docs/README.md` 和本文本节；当前没有未完成的最近活动发布或验收待办。

## 0.63 2026-08-17 最近活动测试知识库数据精确清理补录（历史）

- 用户反馈 0.62 只清理了会话，知识库中仍可看到前一日“最近活动”测试数据。本轮按测试页精确清理标记盘点到 `4` 个 `recent_activity` 测试批次、`9` 条本地逻辑文档和 `8` 个关联 Dify 文档 ID；其中 `4` 个 Dify ID 在执行前已不存在，仍存在的 `4` 个已删除。
- 清理后复核：`CleaningBatch`/`CleaningFile`/`CleaningDocument`/`DifyPublicationShard` 的本轮目标均为 `0`；目标批次的 `8` 个精确 Redis 清洗键均不存在；对应上传目录已为空并移除；`health/ready=200`。
- Dify Dataset 对象和正式数据未删除；当前 `general` Dataset 保留非目标文档，`course` Dataset、课程/路线、应用、持久卷均未改动。清理前已完成 AI 数据库、Dify 数据库、Redis RDB、上传卷和状态快照备份并通过恢复校验。
- 根因修正：此前仅核对了会话级数据，没有执行清洗批次 → 本地文档 → Dify 文档 → Redis/上传目录的联合清理；同时将远端已不存在的 Dify ID 与本地残留记录区分处理。

## 0.61 2026-08-16 App Key 生效、Chatflow 案例优先修复与最终验收（历史）

- jx 已实际读取用户同步后的 `DIFY_BUSINESS_APP_API_KEY`：`.env` 与运行中 API 容器的脱敏指纹一致；`health/ready=200`，Core/UI/Worker/Scheduler 仍为 `20260816-activity-59d963` 同源 release，Dify `/parameters=200` 且保持 4 个 Start 输入。
- 复核发现当前绑定 Chatflow 的 `quick_intent` 仍缺少案例优先排除条件，导致“最近有哪些会议真实案例？”误入活动分支。已在当前绑定 App 的代码节点补充 `is_case_request`，并令活动意图同时满足“非分析、非案例”；Dify 控制台显示自动保存后已发布更新。
- 发布后测试页全链路通过：正常、取消、延期查询分别返回对应活动；延期结果同时覆盖新日期和延期未定，未泄露旧日期；“如何提高活动报名转化率？”返回普通商业分析，不返回活动列表；“最近有哪些会议真实案例？”返回咨询案例，不返回活动结果。各测试均显示最终回答，展开诊断抽查确认 `message_end.status=completed`。
- 本轮使用的 10 个合成测试用户会话已按精确用户范围删除，删除 `10` 个会话、剩余 `0`、删除失败 `0`；未删除正式数据、Dify Dataset 或应用。
- 当前功能的代码、清洗入库、Metadata、Dify 发布、App Key 生效和最终正负向 SSE 验收均已闭环；feature 可进入完成门禁。旧应用和发布前配置仍保留作回滚，临时公开 DSL 地址已撤回。

## 0.60 2026-08-16 最近活动清洗实现、jx 发布与 Dify DSL 发布（历史）

- 本轮已将 `recent_activity` 接入现有清洗/发布管线：活动按逻辑条目拆分，实时调用千问，仅读取 `BAILIAN_API_KEY`；Dify 投影包含受控活动状态、时间数值、来源标题、地点、形式和报名链接等业务 Metadata，不写入源文件名、批次、哈希、内部 ID 或密钥。
- 活动状态已固定为正常、取消、延期。延期有新日期时只保留新日期；延期未定时不泄露旧日期。日期解析已覆盖完整日期、中文范围省略结束年月、跨年和 ISO 单日结束时间；反向日期范围失败关闭。
- jx 最终 release 为 `20260816-activity-59d963`，wheel SHA-256 为 `59d963a1f25240123d6fd87151d76ca70b32f7e971cb9a70844ab15ccb9d23fc`；备份为 `/opt/ai-management/backups/20260816-activity-59d963-predeploy-v2`，报告为 `/opt/ai-management/var/reports/v2/20260816-activity-59d963-deploy`。最终 Core/UI/OCR 同源、迁移 head、API `health/ready=ready`、数据库/Redis/Dify 连通性和 OCR timer 均通过。
- 测试页真实清洗链路已通过：正常活动样本 `2/2` 成功；状态样本 `4/4` 成功，数据库回读确认正常、取消、延期新日期和延期未定四种状态，延期未定没有写入旧日期。每条活动均有 Dify 文档 ID 和 Metadata 回读结果。原样本再次精确上传被历史去重保护拒绝，未通过模糊删除绕过。
- 受控 Dify 管理会话已导入并发布新版隔离 Chatflow；编辑器可见活动意图、动态时间窗口、状态路由、正常/取消延期检索、聚合、排序去重和回答节点。用于导入的临时公开 DSL 地址已撤回并验证不再提供 DSL 文件。
- 当前 jx 运行态仍绑定发布前的商业顾问 App，因为新版 App 的 API Key 尚未创建并同步到 jx；因此新版 Chatflow 尚未完成服务侧切换。切换前旧 App 的正常/取消/延期查询可以返回结果，但普通活动分析问题仍被旧路由带入活动上下文，说明最终负向路由验收尚未通过。（该历史待办已由 0.61 完成。）
- 历史结论：在 App Key 同步、容器重建、Dify 发布更新和正负向 SSE 验收完成前，不得将本功能标记为 done。

## 0.59 2026-08-14 商业顾问移除课程/路线发布完成（历史）

- 当前线上行为已切换：新聊天不读取课程目录或学习路线状态，不发课程卡、路线或专家见解 SSE；DSL 保留一个主 LLM，以及“明确案例请求才检索咨询会议案例”的非课程边界。历史/管理接口仍为兼容旧消息调用目录快照，用户已明确旧数据自行清理。
- jx 完整恢复基线为 `20260814-business-general-2`（数据库、Redis、上传卷、配置均已校验），同源 release 为 `business-general-20260814-1`。新 Dify 隔离应用已发布；旧应用与发布前配置仍保留回滚，未删除课程、路线、知识库或历史聊天数据。
- 发布时控制台校验发现 Dify 1.13.3 `variable-aggregator` 的下游变量误写为 `result`；已改为实际输出名 `output`、补充 DSL 测试后重新导入发布。不得跳过该校验或强制发布。
- 配置切换后已强制重建 API、Worker、模型 Worker、发布 Worker 和 Scheduler；随后发现按需 OCR Worker 仍停在旧的 exited 镜像，已通过资源门禁后按精确服务重建为本 release，OCR autoscaler timer 保持 active。`health/ready=ready`，数据库、Redis、Dify 均可达；Dify `/parameters` 实测只含 4 个新 Start 输入。
- 真实合成商业咨询 SSE 返回 `message_end.status=completed`，只含请求、状态、回答和结束事件，无课程推荐、课程卡片或学习路线事件；测试会话已按精确 ID 删除。入口配置中 `business_advisor.course_recommendation=false`。

## 0.57 2026-08-11 商业顾问精确阶段课程与专家见解发布（当前最新）

- 产品行为：已落实 ADR-034/035。已激活学习路线仅返回其已确认“当前阶段”的全部启用且已审核课程，按阶段官方顺序去重；不再混入下一阶段或使用 5 门截断。直接课程、课程集合和最新课程推荐仍维持最多 5 门。跨会话续问按持久化路线状态处理：`pending_stage` 继续询问阶段，`active` 续接当前阶段，`completed` 提示明确重新开始；Dify 没有可解析决策时也先经过服务端唯一路线状态兜底。
- Dify：当前 DSL 已导入并发布为隔离 Chatflow 应用 `4c157b1e-aa9d-49c2-99d2-a16ec9706886`，jx 已绑定该应用。Dify `/parameters` 回读的 9 个 Start 输入包含 `route_state_context_json` 与 `authoritative_course_context_json`；课程与混合课程检索均有 `status=active` Metadata 条件。旧应用 `cda3f459-c4eb-4a86-8d48-f0aa92d6695c` 保留作回滚，未删除。
- jx 代码：最终 release `20260811-0912-route-resume-fix` 已切换，wheel SHA-256 为 `89bc4d244cc3330f5462f033ec73e8ad5e4bbf95f1552fa69b78af5bf26c31b5`；备份为 `/opt/ai-management/backups/20260811-0912-route-resume-fix`，报告为 `/opt/ai-management/var/reports/v2/20260811-0912-route-resume-fix-deploy`。前一版 `20260811-0846-route-exactstage` 仍可通过其备份回滚。
- 接口兼容：既有 `course_cards.items[]`、`course_recommendation_note` 事件及其 `{content}` 形状保持不变；课程卡片完成后仅发送一次批次级专家见解。`recommendation_note` 只保留兼容字段，对新写入和历史对外读取一律为空，前台及管理端均不展示推荐理由。
- 验证：本地相关回归、编译、文档索引和差异检查通过。jx `health/ready` 为 `ready`，数据库/Redis/Dify 均可达；真实合成用户 SSE 验证通过：精确当前阶段卡片、唯一且在卡片之后的专家见解、正文不含“推荐理由/匹配依据”，`message_end.status=completed`。2026-08-11 复核并精确删除了 3 条合成测试用户画像和 3 条关联审计事件；会话、消息、阶段进度、Redis、Dify 会话和外部删除任务均为 0，未影响正常数据。
- 限制：完整 `pytest -q` 仍受工作区既有缺失 reset 脚本和受保护样本夹具阻塞；本次未伪造、补建或删除这些用户材料，相关定向回归已覆盖本次改动。

## 0.56 2026-08-10 商业顾问路线门禁发布与真实 SSE 验收（历史）

- Dify：当前 DSL 已导入并发布为隔离 Chatflow 应用 `cda3f459-c4eb-4a86-8d48-f0aa92d6695c`；Dify `/parameters` 返回 `200`，应用含 7 个 Start 输入变量。
- jx 代码：release `20260810-route-gate-da35a0` 已切换。API、Worker、Scheduler、模型 Worker、发布 Worker 使用同一核心镜像；OCR 使用同 release OCR 镜像；测试 UI 保持未改动的旧 UI 镜像。发布前备份为 `/opt/ai-management/backups/20260810-route-gate-da35a0-predeploy`，报告为 `/opt/ai-management/var/reports/v2/20260810-route-gate-da35a0-deploy`。
- 配置生效：发现 `.env` 与已运行容器的 Dify Key 指纹短暂不一致（`c419a4cb895c` vs `41ebdcfb44b2`）；按 DEPLOY-013 强制重建 `api/worker/scheduler` 后两者一致。密钥明文未写入报告或文档。
- 运行态：API `/health/ready` 为 `ready`，database/Redis/Dify 均可达；Alembic 保持 `j6a7b8c9d0e1 (head)`；OCR Worker 运行，autoscaler 未被本次代码发布关闭。
- 真实三轮 SSE（合成用户，会话已按精确会话 ID删除）通过：路线初问 `awaiting_route_confirmation`、`course_cards=0`；确认路线后 `awaiting_stage_confirmation`、`course_cards=0`；确认“入门阶段”后 `active`、按路线顺序返回 `5` 张当前+下一阶段课程卡，三轮 `message_end.status=completed`。脱敏结果见报告 `route-gate-e2e.json`。
- 发布问题：快速部署首次在切换前导入烟测因服务器旧 `knowledge_cleaner` 源码缺少 `markdown_table_cleaner` 失败并自动回滚；改用完整 wheel（SHA-256 `DA35A0BA79A5C7B3F4EC6BCE25DB206DEE4C1F80E2A9EFD8396AC357ECE76467`）后构建、切换和验收通过。该问题已沉淀为 DEPLOY-025。

## 0.53 2026-08-09 jx 知识库意外重现的根因、阻断与精确清理（当前最新）

- 根因已确认：为小程序联调临时开放的 Nginx `/ai-service/` 通用反向代理把 `/internal/v1/admin/knowledge-bases/{course,general}/upload-batches` 一并暴露。Nginx 记录到固定半小时的三次上传请求；07:00 的一次成功调用创建了 3 个本地批次，并写入课程知识库 22 篇、通用知识库 4 篇。该现象不是 Dify、Redis 或数据库从备份自动恢复。
- 调用方的具体进程未能从现存证据可靠归属：Nginx 仅保留 Docker 网关转发地址且请求没有用户代理，主机 cron、systemd unit/timer 与现有容器名称检索均未命中。该限制已记录，不能把调用方猜测为某个业务服务。
- 知识库管理接口的目标通道为私网 `172.17.28.83 → 172.30.157.197:8000` 直连，路径为 `/internal/v1/...`，并继续使用内部和管理员双 Token。Dify Nginx 已恢复任务开始前对上传路径的 `403` 阻断规则；端口 `80` 不再是知识库管理通道。jx API 私网绑定、精确 `/32` 主机防火墙规则、回程路由、无文件 `422` 本机探针和 API ready 均已验证；但 2026-08-10 调用方实测 TCP `8000` 超时，且 jx 防火墙计数为 `0`，当前阻塞在实例外的阿里云安全组或调用方实际源地址，尚未完成端到端验证。
- 清理前已备份 Dify/AI 数据库、Redis RDB、上传卷和配置至 `/opt/ai-management/backups/20260809-073700-unexpected-knowledge-cleanup` 并通过校验。使用新增 `manual_incident_cleanup` v3 manifest，仅覆盖确认的 3 个批次、26 个 Dify 文档、21 门课程、1 条路线、精确 Redis 键和对应上传目录；没有按名称、时间窗口或 Dataset 总数删除，也没有直接修改 Dify 数据库。
- 清理后：Dify `general/course` 均为 `0`；本地清洗批次/文件/文档/模型任务/发布分片为 `0/0/0/0/0`，课程/路线为 `0/0`，清洗 Redis 键和精确课程快照 key 为 `0`，上传卷入口为 `0`；API ready、全部命名服务与测试页均正常。报告：`/opt/ai-management/var/reports/v2/20260809-073700-unexpected-knowledge-cleanup`。
- 后续：任何仍需要临时公网 `/ai-service/` 的发布，必须先安装并启用该 guard；完整移除临时桥接或改为 HTTPS/私网访问仍需单独评估小程序联调依赖，不能在本次事故处置中擅自中断。

## 0.54 2026-08-10 咨询会议 RAG 清洗实现、jx 发布与样本验收（当前最新）

- 本地实现：`personal_business_consultation` 已接入共享 `consultation-rag-v1` 契约，按“背景与目标相同才成案”拆分；同源独立案例生成稳定 `case_id`，本地 Markdown 不保留 Dify 分隔标记，分隔标记只在发布请求阶段注入。Metadata 继续沿用现有投影，新增字段仅为适用的 `record_type=consulting_case`、`case_id` 和已映射的既有 `topic`，没有新增 Dify 自定义字段。
- 模型边界：结构/证据/PII/来源块未知引用仍严格失败并走整份脱敏源文档兜底；对千问常见的“建议混入背景/问题”与“无 advice 证据却填写 reference_advice”只做安全删除，不改写事实、不生成新案例。模型失败、契约失败、证据失败仍不会被误报为模型成功。
- 本地验证：咨询 RAG 新增与回归测试通过；相关清洗、模型、Dify、发布契约测试除既有冻结文档摘要断言外均通过。`test_miniapp_interface_document_is_frozen_for_internal_cleaning_changes` 仍因工作树已有摘要与冻结值不一致而失败，本轮未修改该无关文档；全量 `pytest` 仍按既有约 124 秒超时记录，未宣称全量通过。
- jx 发布：最终 release `20260810-consultation-rag-v1-safe-normalize` 使用 wheel SHA-256 `37c7bd35254d4589955b05a3cd13a8f0429f97a35384c7d8744711180e6f7a51`；备份为 `/opt/ai-management/backups/20260810-consultation-rag-v1-safe-normalize-predeploy`，报告为 `/opt/ai-management/var/reports/v2/20260810-consultation-rag-v1-safe-normalize-deploy`。Core/UI/OCR 镜像统一切换，API live/ready、关键包导入和 OCR timer `enabled/active` 通过。
- 样本验收：使用 `/opt/ai-management/tmp/模型/咨询会议总结/` 的 4 份 Markdown 覆盖重跑，最终批次 `019fe85b-31dc-7180-a1c9-9fcdfc0263ae` 为 `completed`，4 个源文件均成功、0 failed、0 review；生成 7 个成功远端文档（4 个源条目 + 3 个同源独立案例子条目），模型实际用量回读为 input `6333`、output `3587`。2 个源条目通过模型契约并生成案例子条目，2 个源条目因模型输出仍违反契约而按设计走整份源文档兜底；所有文档均完成索引、父子块和 Metadata 回读，正文未含 Dify 标记。
- Dify 状态：本地商业顾问 DSL 已加入“案例问题 → `record_type=consulting_case` 检索 → 共享证据压缩 → 最终回答”的静态分支，YAML、节点引用和嵌入代码检查通过。Chrome 已确认登录并可访问 `/apps`；Dify 导入弹窗的隐藏 `input[type=file]` 不触发高层 filechooser，已通过标签页级 CDP 临时显示该控件，再用标准 filechooser 自动上传（无需人工上传、无需开启本地文件 URL 权限）。正式导入创建隔离应用 `eaa840e6-ec85-47e4-89a5-5cf0678f7ac3` 并显示“最新发布/已发布”；`/parameters` 返回 `200`，真实 Dify Chat API 返回 `200` 且带 `retriever_resources` 元数据（本次测试 Dataset 为空，召回数为 `0`）。旧商业顾问应用保留作回滚，不删除。
- jx 应用绑定：已将 jx `.env` 的 `DIFY_BUSINESS_APP_API_KEY` 切换到上述新发布应用（旧 `.env` 备份 `/opt/ai-management/backups/20260810-055542-consultation-dify-key-predeploy/env.backup`，只记录指纹不记录密钥），按 DEPLOY-013 强制重建 `api/worker/scheduler`。重建后 `health/ready=ready`、Dify 可达；真实“相似案例/类似经验”请求收到 `200`、SSE `message_end.status=completed`，测试会话已按精确用户标识删除。
- 本轮踩坑：Windows stage 文本与二进制依赖必须分开处理；一次尝试对整个 stage 执行换行归一化，损坏了 wheel，离线构建报 `Invalid argument`。已重新上传原始依赖、重新备份并完成最终 release；长期规则见 `docs/troubleshooting/deployment.md` 的 DEPLOY-023。

## 0.55 2026-08-10 原计划剩余门禁续测（当前最新）

- 本地回归：使用仓库 `.venv`、仓库根目录 `PYTHONPATH` 和工作区隔离 `--basetemp`；当前收集 `397` 项，`396` 项通过，唯一失败是工作树已有的小程序接口冻结摘要与测试旧值不一致。该接口文档带 assume-unchanged 标记，未回滚、覆盖或改写。
- 直接相关分组在排除该冻结摘要断言后 `67` 项通过；为使 EXIT trap 回归在受限 Windows/Git Bash 环境可复现，测试 harness 补齐了工作区 `PATH` 和模拟应用目录，生产脚本逻辑未改。
- compileall、功能索引 `--check`、目标测试 Ruff、40 条黄金集解析和全部 Bash 脚本语法检查通过。黄金集本轮只完成本地结构校验，未把它冒充真实 Dify Recall/人工语义门禁。
- jx RAG 续测已在通道恢复后完成，未改动正式 Dataset：黄金集检索 `33` 条，general `10/10` 命中、course `0/23` 命中，aggregate `hit@5=0.3030`、critical `hit@5=0.2500`，检索门禁未通过。续测前后配置 Dataset 盘点为 general `7`、course `110`；课程黄金集使用的 synthetic course/route ID 与当前课程目录不是同一数据基线，因此不能把该失败归因于本次代码，也没有清空或替换现有课程数据。临时通用 fixture 已精确删除，按名称匹配残留为 `0`。
- jx Dify 无写入合约已通过：商业 Chatflow 两轮 SSE 均包含 `message_end`，朋友圈、日常写作、摘要画像 Workflow 均 `succeeded`；知识库只读探针无错误但两库各返回 `0` 条。商业顾问轻量 E2E `6` 例中 `2` 例通过，全部请求正常完成；失败项集中在当前课程/路线目录与黄金期望不一致，未知课程与虚构路线防护用例通过，测试会话按精确会话 ID 清理。
- jx 续测后复核：最终 release `20260810-consultation-rag-v1-safe-normalize` 的 API `/health/ready` 为 `ready` 且 database/Redis/Dify 配置与连通性均为 `true`，关键包导入通过，测试页 HTTP `200`，`jx-ocr-autoscaler.timer` 已启用并有活动调度，7 个 ai-management 命名容器均运行。续测产生的容器/主机临时文件已按精确路径清理。
- 原计划剩余的生产 Dataset 破坏性演练、Redis/PostgreSQL/Dify/Worker 真实恢复、小程序真实客户端、40 条人工评分、灰度和 HTTPS/证书切换，继续保持未完成；课程完整目录基线需另行提供并明确授权使用。脱敏续测报告为 [`var/reports/20260810-plan-continuation/README.md`](var/reports/20260810-plan-continuation/README.md)。

## 0.52 2026-08-09 jx ai-management 应用日志已清理（历史）

- 按项目“日志清空”的既定口径，仅删除 AI 管理数据库 `SystemEventLog`，不删除 Docker stdout/stderr、systemd/journal、Dify 日志或主机日志。
- 清理前日志数为 `130`，事务删除后为 `0`；清理前备份为 `/opt/ai-management/backups/20260809-clear-system-event-logs-pre`。
- 操作期间短暂停止 8 个 ai-management 写入服务，删除后已恢复；最终 API `/health/ready` 为 `ready`，数据库、Redis、Dify 配置和连通性均为 `true`，8 个命名 ai-management 容器均运行。
- 本次只清日志表，没有使用全量文档归零脚本，没有删除 `general/course` 文档、课程、Redis 业务缓存、Dify 数据或持久卷。

## 0.51 2026-08-09 jx 测试文档、课程域和本次 release 残留已清理（历史）

- 核心问题已定位：前几次清理只依据本地 `CleaningBatch`/旧 manifest 关联关系，未先盘点 Dify Dataset 全量文档。复核时 `general` 有 `263` 篇文档，其中 `237` 篇没有对应本地 `CleaningDocument`/发布分片记录，属于直接调用 Dify 上传接口产生的孤立测试文档；因此本地批次归零并不代表 Dify 知识库归零。重启期间还曾有外部验收客户端通过 `shequn-admin` 请求短暂重建批次，最终清理前已冻结写入并在观察窗口确认无新写入。
- `business-course-test`、`business-general-test`、配置的 `course` 和 `general` Dataset 均已通过只读盘点后按精确文档目标清理，最终文档数均为 `0`。Dataset 配置本身保留，删除的是测试文档，不是知识库配置或 Dataset 容器。
- 最终使用完整 `general` Dataset 文档 ID 集合加上全部 general 本地批次/文件/清洗文档/模型任务/发布分片/Redis/上传目录的 v3 精确 manifest，通过现有清理链路完成删除；报告为 `/opt/ai-management/var/reports/v2/20260809-143906-general-document-cleanup`。课程域备份为 `/opt/ai-management/backups/20260809-143417-course-document-data-reset`，未直接修改 Dify 业务数据库。
- 最终核对：AI 数据库清洗批次/文件/文档/模型任务/发布分片为 `0/0/0/0/0`，课程和路线为 `0/0`，清洗 Redis 键为 `0`，精确课程快照 key 为 `0`，批次上传目录为空；无关的外部删除任务、会话/配置和审计日志未纳入本次文档测试数据清理范围。
- 服务已恢复并稳定：API `/health/ready` 为 `ready`，测试页 HTTP `200`，OCR timer 为 `enabled/active`，Celery active 任务为空；清理后观察窗口内没有新的 `upload-batches`/Dify 文档上传日志。
- 后续规则：不是每次代码部署都要清空知识库。默认无写入契约、健康检查和业务 SSE 测试不产生 Dify 文档，只清理各自的测试会话；只有完整清洗验收、测试上传或课程/路线测试导入等写入型测试才必须按精确范围收尾。正式知识库、正式课程和已授权保留数据默认不清理。

## 0.50 2026-08-09 jx 完整清洗验收测试文档来源与清理边界（历史过程，结论已由 0.51 更新）

- 本次用户看到的新增知识库文档不是部署备份恢复，而是完整清洗验收主动上传的测试样本。发布前备份发生在验收上传之前，备份中的 Dify dump 只是恢复材料；普通重启、AI 容器重建和代码 release 切换不会自动把备份内容重新写入 Dify。
- `full_cleaning_acceptance.py` 会向 general 知识库上传测试样本，并生成 `/opt/ai-management/var/private-cleanup-manifests/20260809-125124-full-e06470c-baseline/acceptance-cleanup-manifest.json`。本次 manifest 的 general 目标为 `71` 篇；清理前 general 为 `341` 篇，清理后为 `270` 篇，71 个精确目标全部不再存在。course manifest 目标为 `0`，当前 course 知识库共 `22` 篇，未按本次 manifest 删除 course 文档。
- 根因是验收脚本只生成 manifest，不自动执行 `jx_release_data.py plan-clean/clean`；本次部署完成后遗漏了显式清理阶段。随后已使用同一 manifest 完成精确清理：删除 Dify general 文档 `71` 篇、清洗批次 `19` 个、测试会话 `1` 个，并按精确目标清理 Redis/上传目录。未来只有重新运行完整清洗验收或显式恢复 Dify dump 时才会新增/恢复文档，普通重启/重构不会新增。
- 清理报告为 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-cleanup`；API ready、测试页 HTTP `200`、OCR timer `enabled/active` 均复核通过。脱敏问题记录见 [`var/reports/jx-deploy-20260809/issues.jsonl`](var/reports/jx-deploy-20260809/issues.jsonl)，长期规则见 [`docs/troubleshooting/data-cleaning.md`](docs/troubleshooting/data-cleaning.md) 的 DATA-033。

## 0.49 2026-08-09 jx 使用最新代码重新构建发布与回归结果（历史）

- 发布基线：本地 `work` 分支 HEAD `e06470c05fbe3e08d789d7e37e2f7743670c01ea`；本次按当前权威工作树构建完整 wheel，release 为 `20260809-125124-full-e06470c`，wheel SHA-256 为 `32a7a6faf63e4569b79724b041336ec2cbe4b0c341f57abab70d58102ff52657`。未执行 Git commit/push。
- 发布前已完成备份与校验：备份目录 `/opt/ai-management/backups/20260809-125124-full-e06470c-predeploy`，发布报告 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-deploy`。本次未发布 Dify DSL/App，未改对外 HTTP/SSE 契约，未做非清单范围的数据删除。
- 当前运行态：Core、普通/实时/Batch 模型 Worker、发布 Worker、Scheduler 统一使用 `jx-now-ai-management:20260809-125124-full-e06470c`；测试 UI 使用同 release 的 UI 镜像；OCR 使用 `jx-now-ai-management-ocr:20260809-125124-full-e06470c`。API `/health/ready` 为 `ready`，数据库/Redis/Dify 均可达，Alembic 为 `j6a7b8c9d0e1 (head)`，测试页 HTTP `200`，`jx-ocr-autoscaler.timer` 为 `enabled/active`，发布后资源守卫通过。
- 包内容门禁：首次用干净 HEAD 归档构建的 wheel 缺少工作树中的 `ai_management.acceptance_budget`，完整清洗验收立即报模块缺失；本次改用当前权威工作树构建并验证 wheel 内容、容器内 `acceptance_budget_import=ok` 后重新发布。该模块闭包检查已加入 `docs/troubleshooting/deployment.md` 的 DEPLOY-020。
- 部署问题：第一次上传 Bash 文件因 CRLF 导致远端 `pipefail` 语法错误，未发生切换或数据副作用；随后对远端 stage 文本统一去除回车并 `bash -n` 后重试成功。该问题已加入 DEPLOY-019。脱敏问题记录见 [`var/reports/jx-deploy-20260809/issues.jsonl`](var/reports/jx-deploy-20260809/issues.jsonl)。
- 远端完整清洗验收：报告 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-baseline`，退出码为 `1`；19 个 case 中 `12` 通过、`7` 未通过，3 个 Chat 检查通过，预算摘要为 `0.00/5.00`。失败集中在 Dify 文档命名/发布条目终态与数量校验、Batch submission 未确认，以及课程目录 synthetic runner 异常；不是 API 无法启动。精确清理 manifest 和安全摘要已生成，临时验收容器已移除。
- 当前课程目录状态为 `Course=21、LearningRoute=1、CourseCollection=0、CourseCollectionMember=0`，不是完整目录基线；本轮没有擅自恢复或清理正式课程数据。完整目录验收需要另行提供并授权使用完整课程源样本。
- 最新 release 的补充回归：Dify 无写入契约检查中商业 Chatflow 两轮 `message_end`、朋友圈/写作/摘要画像 Workflow 均成功；实时业务场景 4 组 SSE 均收到 `message_end`，停止接口返回 `200` 且会话已删除；清理复核无遗留测试会话。全量本地 `pytest tests -q` 在约 124 秒无输出后超时，未宣称全量通过；发布、课程/路线、文档、清洗与预算相关定向测试均通过。
- 完整可执行流程、备份/发布/回滚门禁与避坑清单见 [`docs/jx完整代码重建部署与避坑手册.md`](docs/jx完整代码重建部署与避坑手册.md)。

## 0.48 2026-08-09 jx 已有 Markdown 表格清洗隔离输出实测（历史）

- 按 0.47 的入口结论执行：服务器实际 CLI 仍不支持 `--clean-tables`，本轮在现有 OCR 镜像 `jx-now-ai-management-ocr:20260809-125124-full-e06470c` 内调用共享 `knowledge_cleaner.markdown_table_cleaner.clean_markdown_tables()`；未重建服务、修改配置、写数据库/Redis/Dify 或调用 PDF 转换 CLI。
- 输入目录 `/opt/ai-management/tmp/pdf-markdown-refine/output/` 保持原样，共 5 个 Markdown；结果隔离写入 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260809-130843-markdown-table-clean/output/`，报告为同级 `table-clean-report.json`，未覆盖输入文件。
- 实测结果：5/5 输出非空，2 个文件内容变化；共识别 8 张表，`tables_converted=0`、`tables_demoted=8`、`rows_skipped=0`。8 张表均因 `empty_header` 无法可靠对齐，按安全策略降级为线性单元格文本；结果文件权限为 `0600`。
- 验证：清洗结果二次运行幂等（5/5 通过）；当前隔离输出保留供人工复核，不应直接作为正式知识库发布结果。若要验证“表格转语义句”路径，需要另用脱敏且带规范表头的 GFM/HTML 表格样本。

## 0.47 2026-08-09 jx Markdown 表格清洗服务器隔离验证（历史）

- 当前只读核对：jx API、普通 Worker、Scheduler、发布/模型 Worker 和测试 UI 均为运行状态；API `health/ready` 返回 `ready`，测试页 HTTP 为 `200`。本轮未执行启动、重建、发布、数据库/Redis/Dify 写入或数据清理；服务何时由谁恢复不在本轮范围内。
- 入口确认：服务器实际 `/opt/ai-management/tmp/pdf-markdown-refine/pdf_markdown_refine.py --help` 只有 PDF 转换参数和 `--force`，没有 `--clean-tables`。单独清洗 Markdown 应调用当前 OCR 镜像内的共享 `knowledge_cleaner.markdown_table_cleaner.clean_markdown_tables()`，不要把本地较新的 CLI 参数直接复制到服务器。
- 合成样本：可靠 GFM 表格转换为语义句；空表头表格安全降级为线性单元格文本并记录内部 issue；代码围栏内表格保持原样。
- 真实样本：服务器既有 Markdown 输出目录共 5 个文件，其中 2 个含表格、共 8 张；8 张均因 `empty_header` 降级，0 行被猜测转换，输出无残留 Markdown/HTML 表格，容器内测试通过。输入正文未回显，临时输出目录已自动删除。
- 本地证据：`tmp/markdown_table_cleaner.py --self-test` 通过；`.venv` 下共享清洗、集成和 PDF CLI 定向用例全部通过。`tmp/markdown_table_cleaner.py` 是未跟踪的独立诊断脚本，服务器正式入口仍是共享清洗模块。
- 边界：本轮只证明服务器当前共享实现可运行及现有样本的安全降级行为；现有真实样本没有可可靠识别的表头，因此不能据此证明复杂表格能转换为语义行。若要验证转换路径，应另用合成或脱敏的规范 GFM 表格输入。

## 0.46 2026-08-09 jx 服务停止根因定位（历史）

- 根因已定位：8 月 7 日 18:27～18:28 执行的是服务器专用 `jx_reset_course_document_data_test.sh`，不是仓库中会自动恢复服务的正式脚本。该测试脚本的设计就是“清理成功后保持服务停止”，要求关闭旧上传客户端后另行执行 `start`；本次未发现后续 `start` 或命名 AI 容器的启动事件。
- 证据链：测试脚本在 18:27:38 被写入/更新；`/opt/ai-management/backups/20260807-182804-course-document-data-reset/course-document-data-reset/poststop/execution.safe.json` 于 18:28:22 记录 `status=completed`、课程 Dify 文档 `22`、清洗批次 `1`、课程 `21`、路线 `1` 已清理；18:28:23 的 postcheck 只证明数据计数归零。随后 8 个命名 AI 容器仍为 `Exited (0)`，API 的 `StartedAt` 没有进入新的启动周期。
- 关键验证误区：该测试脚本的 `verify_reset` 使用 `docker compose run --rm` 创建一次性 API 容器做数据复核，即使命名 `api` 已停止也能生成 `0` 计数；它没有检查命名服务是否运行、`/health/ready` 或 Nginx 测试页。因此“数据清理成功”被误当成“服务恢复/任务完成”。
- 故障链：归零脚本主动停止写入 Worker/UI，再停止 API → 数据备份和清理完成 → 脚本按设计不启动服务 → 没有后续 `start`、无 Compose/systemd 服务状态看门人 → Nginx 仍在但上游 `ai-management-test-ui:8501`/`ai-management-api:8000` 无容器监听 → `/ai-management-test/` 返回 `502`。`unless-stopped` 不会抵消显式 `docker compose stop`。
- 排除项：API 是正常 `Shutting down`，不是代码崩溃；`OOMKilled=false`；主机内存、磁盘、Dify 容器和 Nginx 均正常。OCR autoscaler 的失败是 AI Compose 停止后的派生告警；Docker 缓存维护门禁失败也发生在 8 月 9 日，不是 8 月 7 日停服原因。
- 防复发方案与分阶段验收见 [`var/reports/jx-availability-20260809/root-cause-and-remediation.md`](var/reports/jx-availability-20260809/root-cause-and-remediation.md)。本轮仍未在 jx 执行启动或修改；恢复服务需要单独授权。

## 0.45 2026-08-09 jx 服务可用性、Git 与部署版本检查（历史）

- 检查时间：服务器本地时间 `2026-08-09 11:53` 左右；本轮只读检查，未在 jx 执行启动、重建、清理或发布。
- Git 分支：本地仓库当前分支是 `work`，`HEAD=e06470c05fbe3e08d789d7e37e2f7743670c01ea`，提交时间 `2026-08-08 04:07:09+08:00`，相对 `origin/main` 为 `ahead 7`；工作树存在大量未跟踪文件。jx `/opt/ai-management` 不是 Git 工作树，服务器没有可直接报告的 Git 分支，线上使用 release/镜像工件。
- 部署版本：jx `.env` 的 `BUILD_VERSION`、Core/UI/OCR 镜像均为 `20260807-1455-route-collections`。该 release 早于本地 `work` 的 2026-08-08 HEAD，且服务器源码与本地 HEAD 的 `conversations.py`、`course_route_catalog.py`、`knowledge_cleaner/pdf_convert.py`、`pyproject.toml` SHA-256 均不一致，因此当前不是本地 Git HEAD 的最新代码。
- 服务状态：主机 SSH 正常；根分区约 `229 GiB` 可用、`MemAvailable` 约 `26 GiB`。Dify 容器仍运行且健康；`ai-management` Compose 项目为 `exited(10)`，API、普通/模型/发布 Worker、Scheduler、UI、OCR 共 8 个容器均为 `Exited (0)`，`OOMKilled=false`，没有崩溃证据。
- 访问故障：Nginx 仍运行并把测试页转发到 `ai-management-test-ui:8501`、AI API 转发到 `ai-management-api:8000`；公网和服务器本机访问 `/ai-management-test/` 均为 HTTP `502`，原因是上游 AI 容器已停止。API 最后一次日志先通过 `/health/live`、`/health/ready`，随后正常记录 `Shutting down`；不是健康检查失败导致的 502。
- 停服线索：root 历史中存在全量文档归零/验收流程的显式 `docker compose ... stop`；8 月 7 日 13:40 的一次归零日志记录了成功恢复，但 18:28 左右这次停服没有找到对应的“恢复完成”证据。具体触发脚本当时尚未从现有证据确认，详见顶部 0.46。
- 其它告警：8 月 9 日 Docker 缓存维护因私有缓存约 `22.97 GB` 超过 `20 GiB` 门槛而安全失败；OCR autoscaler 因 AI Compose 已停而连续失败。这两项不是本次 502 的根因，但说明不能只恢复 Nginx 或只看定时器。
- 待授权恢复：应从 `/opt/ai-management` 按合并 Compose 明确启动 AI 服务，再依次核对 `docker compose ... ps -a`、容器内 `/health/ready`、测试页 HTTP 200 和关键日志；本次未执行该写入操作，当前线上仍是不可访问状态。脱敏证据见 [`var/reports/jx-availability-20260809/issues.jsonl`](var/reports/jx-availability-20260809/issues.jsonl)。

## 0.44 2026-08-07 jx 发布、目录批次与页面全链路复测结果（历史）

- 本轮实际处理的问题：恢复 `course_route_catalog` 原有逐文档增量清洗/同步，修复同名课程合集按全局关系串线、阶段展示缺少“课程合集 → 课程名称”、路线确认后不推进、路线展示不完整，以及课程卡后专家点评缺失时没有安全兜底的问题；同时保留原有 PDF 清洗边界，不把无正文 PDF 强行降级为可入库内容。
- 本地完成：真实 Excel 解析 `105 课程 / 5 路线 / 33 合集 / 103 成员`；同名合集跨路线、连续块混入其它路线、阶段展示裁剪均有回归；全量 `pytest` 为 `379 passed`，Python 编译、DSL YAML 解析、功能索引写入/检查、`git diff --check` 均通过。
- jx 发布：release `20260807-1455-route-collections` 已成功切换，健康检查 `ready`；API、Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 均使用同一新镜像。Dify DSL 本轮未导入、未保存、未发布；未增加 HTTP 路径、请求字段或 SSE 事件。
- 清洗批次：页面上传 `课程信息与学习路线.xlsx`，批次 `019fdb04-2cd5-7c2a-81b4-a4e645424d6f`，`110/110` 条目成功、`0` 失败、`0` 待复核；服务端统计源样本为 `105/5/33/103`。
- 页面真实验收：使用隔离用户完成“首问 → `我确认学习路线：AI高手提效课` → `我目前在：入门阶段`”。首问展示完整两阶段、阶段内课程合集及课程名称；路线确认进入 `awaiting_stage_confirmation` 且不重复路线；阶段确认返回课程卡。诊断中确认 `course_recommendation_note` 事件紧随课程卡事件，仅一条，正文为“建议按课程卡顺序完成当前阶段内容，并做一次对应练习；完成后告诉我进度，我再安排下一步。”，无“匹配理由/专家建议”前缀，随后仍有既有完成阶段引导。
- 专家点评边界：本次页面证据确认的是服务端门禁后的单条下一步建议事件；由于按确认范围没有发布 Dify DSL，无法把远端模型是否生成 `expert_insight` 作为本轮证据。模型字段缺失/不合规时由服务端生成上述安全默认建议，保证不增加模型调用、不阻断课程卡。
- 目录基线未完成项：远端在本批次前已有来源 `course-catalog-20260807-150001.xlsx`，保留 `21` 门旧课程；因此当前数据库总数为 `Course=126、LearningRoute=5、CourseCollection=33、CourseCollectionMember=103`。本轮没有删除该来源，也没有执行历史迁移；若验收要求“仅保留本次 105 门样本”，需另行确认后做精确来源清理并重新验收。
- 其它样本未在本次最新 release 页面重跑：个人咨询、知识星球、资料钩子、书籍的既有批次/历史页面结果保留在历史记录中；本条记录不把它们冒充为本 release 的新证据。若要求完整样本矩阵，仍需在当前 release 逐项上传并记录批次。
- PDF 计划状态：共享 PDF→Markdown 的文字层优先和边界告警代码/回归已完成；但本次最新 release 尚未用用户列出的 3 个资料钩子 PDF、1 个书籍 PDF 在页面重新跑完矩阵。`PDF_TYPE_REVIEW_REQUIRED` 对 image-only、纯扫描 OCR 失败、正文为空或咨询图片隐私未确认仍是预期硬门禁，不应为追求“成功”强行放行。
- 踩坑与解决：第一次发布传入的 wheel SHA 少 1 位，校验阶段安全中止且服务未切换；修正 64 位 SHA 后成功。OCR 默认暂停，本次仅用进程级 `JX_OCR_DEPLOYMENT_ENABLED=true` 完成既有离线构建，未改 `.env`，定时器发布前后均 `enabled/active`。回退课程目录全量替换后，两个旧运维脚本会导入已删除函数，已从新 release 安装清单移除。
- 交接页面：测试页保留在 jx 页面，当前会话可复核上述路线、课程卡和点评诊断；未执行 Git commit/push、未迁移历史数据、未修改真实小程序标题。

## 0.43 2026-08-07 jx 发布与完整目录页面复测进行中（历史过程）

- 本轮处理问题：学习路线可能带入其它路线的课程合集/课程信息；课程路线展示缺少“课程合集 → 课程名称”的层级；此前 jx 还运行残缺目录，导致路线、课程卡和专家点评链路无法作为验收证据。
- 已完成：本地修复、文档与索引更新、全量 `pytest` 379 项通过；发布 release `20260807-1455-route-collections`，健康检查为 ready，API/Worker/模型 Worker/发布 Worker/Scheduler/UI 均已切换同一新镜像；Dify DSL 未导入/保存/发布。
- 页面复测：完整 Excel 已上传，批次 `019fdb04-2cd5-7c2a-81b4-a4e645424d6f`，确定性解析为 110 个候选（105 课程+5 路线）；实时模型 110/110 成功、0 失败、0 待复核，当前仍在逐条发布。此时数据库仍为旧 `21/1/0/0`，不能提前宣称目录或商业顾问页面验收通过。
- 踩坑与解决：第一次发布传入的 wheel SHA 少 1 位，发布在校验阶段安全中止且服务未切换；修正 SHA 后成功发布。OCR 部署开关默认暂停，按本次已授权发布仅使用进程级 `JX_OCR_DEPLOYMENT_ENABLED=true` 完成既有离线构建，未改 `.env`，定时器发布前后均为 enabled/active。此前回退课程目录全量替换后，部署清单中的两个旧运维脚本会导入已删除函数，已从发布清单移除并未随新 release 安装。
- 已完成：批次已终态并完成页面路线、阶段、课程卡、单条综合点评验收；最终证据和目录基线差异见 0.44。

## 0.42 2026-08-07 学习路线合集跨路线串线修复（历史记录）

- 新增问题根因：课程合集展示原先按全局合集成员关系派生，且同名连续块使用全局名称 ID；在部分重复/残缺目录中，一个路线阶段可能看到其它路线的合集成员。
- 修复范围：恢复 `course_route_catalog` 原有逐文档增量同步，不做源级全量替换；解析层只对同名连续合集增加路线/阶段作用域，连续块中明确属于其它路线的行不进入当前合集；成员变化时常规合集 ID 保持稳定，重复同名块才使用冲突后缀。
- 商业顾问 `learning_route` 新增可选展示字段 `course_collections: [{"name": "课程合集", "course_names": ["课程名称"]}]`；它只从当前阶段 `course_ids` 与既有快照交集派生，避免把合集中的其它路线课程带入。原 `course_ids`、`course_names`、`course_collection_names` 保持兼容；未新增 HTTP/SSE 事件、数据库字段或 Redis Key。
- 回归证据：同名合集跨路线、单个连续块混入其它路线、阶段合集展示裁剪均新增测试；`tests/test_course_route_xlsx.py`、`tests/test_course_collections.py`、`tests/test_course_route_catalog.py` 定向通过。真实样本仍为 105/5/33/103。
- 部署状态：上述修复尚未重新发布到 jx，也未重新上传课程 Excel；待全量测试、索引检查通过后部署，并在页面复验“路线首问 → 路线确认 → 阶段确认 → 课程卡 → 一条综合点评”。

## 0.41 2026-08-07 样本页面复测发现目录残缺（历史记录）

- 用户清空数据后重新用样本在 jx 测试页验收。个人商业咨询 3/3、资料钩子 3/3、书籍 1/1 成功；知识星球普通帖成功、红包帖按策略排除。指定 DOCX 文件名在本地不存在，本轮使用同目录实际存在的备份文件代测，输入不一致已记录。
- 课程路线样本尚未能在页面上传：远端测试 UI 仍为旧版本，目标知识库只暴露 `general`，无法选择课程知识库/`course_route_catalog`。页面课程测试读到的远端目录为 `Course=21`、`LearningRoute=1`、`CourseCollection=0`，路线只有一个“入门”阶段；这不是用户提供的完整 Excel。
- 根因已定位：本轮新增的课程目录源登记/路线结构冲突门禁拒绝了完整源上传（日志为“课程目录存在未登记完整源的路线结构冲突”），随后残缺 21/1 文件被写入；没有有效路线时商业顾问自然不会发送 `learning_route`、课程卡或 `course_recommendation_note`，不是 SSE 事件被删除。已在本地移除该额外门禁和全量替换调用，恢复原 `course_route_catalog` 逐文档增量同步路径；路线方向/目标的原中性兜底也已恢复。商业顾问仅保留活动路线短追问修复和 `learning_route` 的课程合集名称装饰。
- 本地原始 Excel 解析结果为 105 门课程、5 条路线、33 个合集、103 条成员关系；SHA-256 为 `88d2fa9f63297c059bb7e714ae84245745f2ec19763d354334712e7fcb0b90e4`。该完整源必须通过新版测试页上传并在同一批次全部成功后再验收，不能用远端 9KB 的 21/1 文件替代。
- 当前状态：代码已统一收敛，课程/路线目录与合集定向测试 `23 passed`，模型清洗工作流 `38 passed`；尚未重新部署本轮修复，也尚未取得完整目录上传后的页面 SSE 证据。下一步是按发布手册先跑全量本地回归，再部署并重新上传完整样本，最后验证 `learning_route → course_cards → course_recommendation_note` 顺序。

## 0.40 2026-08-07 课程目录恢复、路线兼容修复与 jx 最终验收（当前最新）

- 已发布 release `20260807-1345-route-activefix`；wheel SHA-256 `4bce62dd6add14503c870f4dfc855e7d942493fed5de979d6350d1e58c563e5c`。发布前备份为 `/opt/ai-management/backups/20260807-1345-route-activefix-predeploy`，报告为 `/opt/ai-management/var/reports/v2/20260807-1345-route-activefix-deploy`。Core/UI/Worker/模型 Worker/发布 Worker/Scheduler 同源运行，`health/live`、`health/ready`、Alembic `j6a7b8c9d0e1 (head)`、容器导入烟测通过；OCR 按 autoscaler 按需运行。
- 通过测试页上传原始完整 `course_route_catalog` 样本，批次 `019fda86-9df8-71fa-99c1-c7f0b620205c` 的 110 个候选文档全部成功；显式恢复 dry-run/apply 通过，线上目录现为 `Course=105`、`LearningRoute=5`、`CourseCollection=33`、`CourseCollectionMember=103`。恢复只删除已验证旧目录行和其精确 Dify 文档清理任务，不删除/重置历史 `LearningRouteProgress`；发现旧阶段 ID 与新模型阶段 ID 不一致后，按已确认的入门阶段语义修复当前测试进度指针。
- 根因修复：短阶段追问可能被 Dify 标为 `general_qa` 但仍携带已选路线 ID，原服务端只在 `route_recommendation` 分支补充 `course_names`/`course_collection_names`，造成该分支暴露原始 ID且活动阶段不发课程卡。现只要路线通过权威校验就统一应用完整路线装饰、进度门禁和课程卡；路线刷新按阶段名称/顺序尽量保留稳定 `stage_id`，避免后续进度指针失效。
- 页面隔离验收：新用户完成“路线首问 → `我确认学习路线：AI高手提效课` → `我目前在：入门阶段`”，页面展示完整两阶段、阶段内课程合集名称、课程卡；课程卡后只出现一条无“匹配理由/专家建议”前缀的综合下一步建议，并带既有完成阶段引导。未新增 HTTP/SSE 事件或字段；`course_ids` 仍是机器字段，用户展示使用同序 `course_names`，不改真实小程序标题。
- 本地最终验证：文档契约 `11 passed`，课程/路线定向 `21 passed`，全量 `pytest tests -q` 共收集并通过 `379` 项；仅有现有 Starlette/httpx 弃用警告。功能索引生成/检查和 `git diff --check` 通过。
- 发布过程中第一次尝试因资源看门狗检测到瞬时不可中断 I/O 等待而在 OCR 构建前失败，未切换服务；第二次重试成功。发布脚本已清理复用 release ID 的旧失败标记，避免报告误报。未导入/发布 Dify DSL，未迁移历史批次，未执行 Git commit/push。

## 0.39 2026-08-07 回退至 20260806-172003-full-23162b2（历史）

- 已按用户要求将 jx AI 管理服务切回 `20260806-172003-full-23162b2`；API、普通/实时/Batch 模型 Worker、发布 Worker、Scheduler、OCR Worker、测试 UI 均使用目标镜像并处于 running。
- 目标 wheel SHA-256 为 `03c2b6073ffab328fb9d08ef67ae680ec6c9d6cccb7d8c8282660d3299590f92`；已保存本次切换前 `.env`、wheel 和 Compose 配置备份：`/opt/ai-management/backups/rollback-20260807-122550-to-20260806-172003-full-23162b2`。
- 验证：`health/ready=ready`，数据库/Redis/Dify 均可达，Alembic 为 `j6a7b8c9d0e1 (head)`，核心模块导入通过，测试页 HTTP 200，OCR autoscaler timer 为 `active`。
- 边界：本次只切换应用镜像与 wheel；未执行数据库迁移，未修改数据库、Redis、上传卷或 Dify 应用，未执行 Git commit/push。核验记录：`/opt/ai-management/var/reports/v2/rollback-20260807-122550-to-20260806-172003-full-23162b2`。

## 0.37 2026-08-06 学习路线阶段课程集名称与 jx 完整发布（历史）

- 已按用户确认补充 `learning_route` SSE 事件的阶段字段 `course_collection_names: string[]`。字段只返回课程集名称，不返回课程名称或 ID；名称按该阶段现有 `course_ids` 的原始目录顺序首次出现去重，没有课程集时返回空数组。实现从既有 `ai:course:snapshot` 的 `collections` 运行时派生，不新增 Redis Key、数据库字段或迁移。
- 本地验证：课程/路线定向回归 `22 passed`，商业顾问/路由/应用回归 `13 passed`，全量 pytest 收集 `339 passed`；目标代码 Ruff、Python 编译、功能索引检查和 `git diff --check` 通过。
- jx 快速发布第一次在核心导入 smoke 阶段发现旧运行时缺少 `knowledge_cleaner.markdown_table_cleaner`，未切换 release，自动回滚并确认旧服务 readiness 正常；随后改用包含 `ai_management` 与 `knowledge_cleaner` 的完整 wheel 发布成功。release `20260806-195815-course-collections`，wheel SHA-256 `d53b6603d0d467e1f397f33c3a0f2af21ba8c3d83ef59f8fdffd9eab037a6692`，发布前备份 `/opt/ai-management/backups/20260806-195815-course-collections-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260806-195815-course-collections-deploy`。
- 发布后 API、Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 使用同源新镜像；`health/ready=ready`，数据库/Redis/Dify 均可达，迁移为 `j6a7b8c9d0e1 (head)`，测试页 HTTP 200，OCR autoscaler timer 为 `active`。队列空闲时 OCR Worker 按 autoscaler 退出，不作为发布失败。
- 远端直接 SSE 验收通过：`learning_route` 事件存在，路线 `AI高手提效课` 返回 2 个阶段；每个阶段均有数组字段，示例阶段课程集名称为 `公众号AI爆款`、`AI写作课`/`AI智能体写作`/`AI阅读博主`/`AI知识库`。合成会话已删除；未清理正式课程、路线、Dify 数据、Redis 整库或持久卷，未执行 Git commit/push。

## 0.38 2026-08-07 商业顾问学习路线、综合点评与课程目录全链路验收（历史）

- 已按本轮确认完成商业顾问服务端改造：路线确认优先读取当前轮/摘要信息，明确回复“我确认学习路线：…”时直接恢复权威路线并进入阶段确认；`learning_route` 返回完整阶段、按原始顺序的 `course_names` 与去重后的 `course_collection_names`；阶段确认后返回课程卡，并在卡片之后只发送一条 `course_recommendation_note` 综合专家点评，缺少合规模型点评时由服务端生成安全默认建议。未增加 HTTP 路径、请求字段或 SSE 事件；既有 `learning_route` 仅增加向后兼容的展示数组字段，既有客户端可忽略未知字段。
- 全量课程目录已通过 `course_route_catalog` 完整替换导入并重置旧进度：数据库当前 `Course=105`、`LearningRoute=5`、`CourseCollection=33`、`CourseCollectionMember=103`；目录导入批次 `019fd8cc-232a-79ac-bd34-9ee6fab49dff` 完成，Dify 课程知识库分页总量为 115 条。验收结束后已删除本轮合成会话并清除其唯一进度记录，正式课程/路线/Dify 数据未清理。
- 本轮导入先发现并修复两类真实问题：Dify 对已不存在文档返回 404 会阻断源删除，现按删除语义幂等处理并保留其他错误；课程路线表中缺少独立方向/目标会触发整批原子阻断，现对路线使用中性的默认方向/目标，不猜测收益或承诺。失败批次和重复上传状态均保留审计，不回写历史业务数据。
- jx 最终 release `20260807-0450-route-fallback`，wheel SHA-256 `43127dcba6ea28be37f15c38be88499588fc48d3e785fe480db268cd0c7233f4`；发布前备份 `/opt/ai-management/backups/20260807-0450-route-fallback-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260807-0450-route-fallback-deploy`。API、Worker、模型 Worker、发布 Worker、Scheduler、测试 UI 均使用同源镜像且 running；`health/ready` 返回 `ready`，数据库/Redis/Dify 配置和连通性均为 true。
- Dify DSL 已在真实 Dify 页面完成发布，页面状态为“最新发布/已发布”；本地 DSL 与远端发布文件 SHA 一致。未增加模型调用、LLM 节点、SSE 事件或对外接口；未修改真实小程序源码/卡片标题，标题仍由前端另行改为“下一步建议”。
- 真实页面验收：路线首问展示 `AI高手提效课` 的完整两阶段及课程合集名称；回复路线确认后不重复路线/画像问题并进入阶段确认；回复“我目前在：入门阶段”后返回课程卡，卡后出现唯一一条无“匹配理由/专家建议”前缀的综合点评，并追加“完成后回复：我已完成：入门阶段”的下一步引导。
- 本地验证：首次全量测试仅因功能索引在源码变更后未重新生成而失败，已运行 `scripts/generate_project_index.py --write/--check` 修复；随后定向回归 `tests/test_project_documentation_contract.py tests/test_course_route_catalog.py tests/test_business_chat_dedupe.py tests/test_dify_client.py tests/test_model_cleaning_workflow.py` 全部通过。索引更新后最终全量 pytest 为 `372 passed`，仅有既有 Starlette/httpx 弃用警告；`tk` 命令在本机不存在，按项目规则回退使用虚拟环境 Python。
- 真实验收页面保持在 jx 测试页供复核；验收完成后会关闭 Dify/测试临时页，仅按用户需要保留测试页交接，不执行 Git commit/push。

## 0.36 2026-08-06 帖子知识单元契约强化与最终发布（历史）

- 在 0.35 的链路修复基础上，进一步收紧 `wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post` 的模型契约：`keep/review` 且存在保留来源块时必须返回证据绑定知识单元；只返回 `keep/drop` 的旧式结果不再被当作“已提取成功”。契约失败按新批次源 Markdown 降级，不阻塞入库。
- 本地最终回归：相关清洗/分片/模型工作流/PDF/发布契约/文档契约/快速发布契约测试通过；目标代码 Ruff、功能索引检查和 diff 检查通过。
- jx 最终完整发布成功：release `20260806-172003-full-23162b2`，wheel SHA-256 `03c2b6073ffab328fb9d08ef67ae680ec6c9d6cccb7d8c8282660d3299590f92`；发布前备份 `/opt/ai-management/backups/20260806-172003-full-23162b2-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260806-172003-full-23162b2-deploy`。API、普通 Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 使用同源新镜像；readiness 为 `ready`，迁移为 `j6a7b8c9d0e1 (head)`，OCR autoscaler 为 `enabled/active`，容器内最终契约和资料钩子烟测通过。
- 第一次 SSH 会话在发布启动前断开，未产生发布报告且服务仍保持上一 release；未重复启动并发发布，随后单次重试成功。未修改对外 HTTP/SSE 接口、数据库模型或迁移；未回写用户提供的历史批次，未执行 Git commit/push。

## 0.35 2026-08-06 清洗链路根因修复、日志增强与完整发布（历史）

- 已修复资料钩子 `lead_magnet` 必然重试的根因：非模型策略之前在本地正文预写 `<<<DIFY_PARENT>>>` / `<<<DIFY_CHILD>>>`，随后被正文边界校验误判为用户输入，导致文件停在 pending。现在本地保留源 Markdown，发布 Worker 按保存的父子块配置交给 Dify 处理；已有批次不回写，新上传批次使用修复后的逻辑。
- 已核对用户提供的测试批次：`zsxq_post` 批次曾完成实时模型调用，2 个条目中 1 个被模型判定为纯红包/节日祝福而排除，另 1 个成功；它属于旧 release，模型只做保留/排除判断，未执行新的知识单元提取。新代码已把帖子模型契约扩展为“问题、结论、推理、条件、行动、风险、来源证据”的知识单元，并支持每个知识单元独立本地/Dify 文档。
- `lead_magnet` 旧批次的 `CLEANING_FAILED` 原因是上述 Dify 分隔符误判，不是模型问题；`book` 旧批次是 `CONTENT_EMPTY_AFTER_CLEANING`，PDF 转换返回 `PDF_TYPE_REVIEW_REQUIRED`，没有可安全使用的 Markdown 源，因此没有模型任务可降级。两批次按既定“历史批次不静默改写”规则保留原状态。
- 错误事件已记录文件/批次/策略/知识库/扩展名/尝试次数/阶段/异常类型/脱敏消息和 traceback；不记录正文、密钥、敏感原始文件名或完整路径。
- 本地验证：清洗、分片、模型工作流、发布契约和 PDF 策略相关测试通过；目标代码 Ruff、Python 编译、功能索引检查和 diff 检查通过。全量测试仍受当前工作树的脚本导入路径/依赖和长套件等待影响，未将其误报为通过。
- jx 完整发布成功：release `20260806-165915-full-23162b2`，wheel SHA-256 `c40174f3d84def665cf4d5b34ed8230c2aab8ad519e6f9ddc7c4f39dda5fc0d7`；发布前备份 `/opt/ai-management/backups/20260806-165915-full-23162b2-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260806-165915-full-23162b2-deploy`。API、普通 Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 使用同源新镜像；readiness 为 `ready`，Dify/数据库/Redis 可达，迁移为 `j6a7b8c9d0e1 (head)`，OCR autoscaler 为 `enabled/active`。容器内资料钩子和异常详情烟测通过。
- 未修改对外 HTTP/SSE 接口、数据库模型或迁移；未清理或改写上述历史测试批次；未执行 Git commit/push。保留发布备份用于回滚。

## 0.34 2026-08-06 新批次源 Markdown 降级、看门狗与完整 wheel 发布（当前最新）

- 已按用户确认修复清洗全链路：新建 `cleaned` 批次（`course_route_catalog` 除外）保存 `source_markdown_fallback_enabled` 策略快照；PDF/DOCX 转换后的 Markdown、上传 Markdown/TXT 规范化后的 Markdown，先经过统一表格清洗和最终无表格门禁。模型 `review`、契约失败、无响应、任务级异常只要源 Markdown 非空，就保留内部原因、明确忽略模型返回值，恢复源 Markdown 为普通 `pending` 或文章 `shard_pending` 发布；空正文、转换失败和 Dify 硬约束失败仍为 `failed`。历史批次缺少策略快照时 fail-closed，课程目录保持严格原子路径。
- 多文件发布统一进入 `ai_publication` 单并发队列；文章分片遵守 `DIFY_SHARD_MAX_ITEMS`（默认 200）和字节上限。Dify 仍按单个最终文档调用 `create-by-text`，返回的 `batch` 仅用于索引轮询；没有增加或调用未确认的多文档外部接口。每 5 分钟看门狗会重新调度长期未更新的 pending、模型处理和 Dify 分片，并刷新文件/批次聚合，避免依赖人工逐条解卡。
- 错误日志已补充 `file_id/batch_id/strategy_code/knowledge_base_code/extension/attempts/phase/exception_type/脱敏 exception_message` 等定位上下文；不记录正文、密钥、原始敏感文件名或完整路径。模型源 Markdown 降级、清洗文件失败、Dify 分片失败分别有可检索事件码。
- 本地验证：项目 `.venv` 全量 `pytest -q` 通过；Python 编译、功能索引生成/检查、文档/清洗/发布契约测试通过。未修改对外 HTTP/SSE 接口、数据库模型或迁移。
- jx 快速发布第一次按门禁自动回滚：`quick-20260806-141531-d5397dcb7e5f` 在核心导入 smoke 发现旧运行时缺少 `knowledge_cleaner.markdown_table_cleaner`，没有切换数据或 Dify；回滚后 readiness 和旧 release 恢复正常。
- jx 完整发布成功：release `20260806-144000-full-9afc8b78bc35`，wheel SHA-256 `9afc8b78bc351602391e500ee9e1179881a7f3f626f15da9e51b0b56807c8a46`；发布前备份 `/opt/ai-management/backups/20260806-144000-full-9afc8b78bc35-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260806-144000-full-9afc8b78bc35-deploy`。API、普通 Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 同源运行，readiness 为 `ready`，Dify/数据库/Redis 可达，迁移保持 `j6a7b8c9d0e1 (head)`，OCR autoscaler 为 `enabled/active`。
- 真实远端合成回归：`test_markdown` 通过 API→清洗→Dify `create-by-text`→索引/Metadata/状态聚合，批次 `019fd5c2-02c6-74f1-bbe0-d69ce23fa5ff` 文件和条目均 `succeeded`，Dify 文档计数为 1；随后按精确 file ID 调用源删除，HTTP 200、删除 1 个文档，测试数据已清理。发布后相关 Worker 近 5 分钟错误/异常关键字计数均为 0。
- 未修改 `AGENTS.md`、小程序后端和既有外部接口；未执行 Git commit/push；保留完整发布备份和脱敏报告用于回滚/审计。

## 0.33 2026-08-06 review_required 开关、空正文修复与最终 jx 验收（历史）

- 已增加配置 `CLEANING_REVIEW_REQUIRED_ENABLED`：默认值为 `true`，jx 测试环境为 `false`。配置按上传批次快照生效，不改变已创建批次；只作用于四个内容模型策略，不作用于 `course_route_catalog`。
- 开关关闭时，非空正文的短文本、隐私/来源复核和内容模型 `review` 不再阻塞后续模型/发布；原复核原因只保存在内部审计，不写公共错误码或知识库 Metadata。空源文件、清洗后为空、模型无正文和 Dify 硬约束仍保持 `review_required`；模型契约失败、第二次无响应和发布异常仍记录为 `failed` 并跳过，不重试契约失败。
- 本轮发现并修复空正文边界：知识星球/小报童策略抛出的“没有可清洗的正文”未被 Worker 识别，导致先重试后变成 `CLEANING_FAILED`；修复后明确归入 `CONTENT_EMPTY_AFTER_CLEANING`/`review_required`，非空异常仍按原规则处理。
- 本地最终验证：目标清洗、模型工作流和契约套件通过；重新生成 `docs/功能与接口索引.md` 后，全量 `pytest tests -q` 全部通过，索引检查、Python 编译和 diff 检查通过。
- jx 完整 wheel 发布：release `20260806-111200-full-8f59f14169c9`，wheel SHA-256 `8f59f14169c912c6b16ee035f72c76bcf2f759287e15f4f540080d8305d64d3b`；报告 `/opt/ai-management/var/reports/v2/20260806-111200-full-8f59f14169c9-deploy`，发布前备份 `/opt/ai-management/backups/20260806-review-switch-fix-predeploy`。API/Worker/模型 Worker/发布 Worker/Scheduler/UI 使用同源新镜像；`health/ready=ready`，Dify 可达，OCR autoscaler timer enabled/active；`BAILIAN_BATCH_ENABLED=false` 未改变。
- 测试页最终验收：空文档新批次 `019fd511-11ea-718b-bfed-0ba266f08d4d` 为 `completed`，文件 `review_required`、`CONTENT_EMPTY_AFTER_CLEANING`，无模型任务和发布分片；唯一非空合成帖子批次 `019fd515-596b-78b2-8733-05cb5ab460c4` 为 `completed`，文件/条目 `succeeded`，实时模型完成，Dify 分片 `succeeded`，点击“读取条目”后出现条目详情入口。
- 测试问题已脱敏记录于 `var/reports/20260806-review-switch/issues.jsonl`：空正文误归类、快速发布缺少共享模块、SSH 会话在发布期间断开、HTML 不支持咨询策略、咨询样本被模型明确 `exclude`、浏览器控件时序超时；已分别修复、确认预期门禁或通过重试验证。未修改小程序后端和既有 AI 管理接口，未做数据库迁移，未提交或推送 Git。

## 0.32 2026-08-06 清洗失败隔离策略发布与测试页入库验收（历史最新）

- 清洗策略已按用户确认执行：`personal_business_consultation`、`wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post` 的模型技术失败按文档隔离为 `failed` 并继续兄弟文档；仅无数据、最终清洗正文少于 200 字、隐私/来源风险或服务端强制配置进入 `review_required`。模型只有无可用返回值才对该文档重试一次；非空但契约不合法不重试。`course_route_catalog` 保留严格/来源原子语义。
- 失败记录新增只读接口 `/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/failed-items`，支持 JSON 分页、CSV、状态和错误码筛选；终态批次/失败记录保留 7 天。未修改小程序后端和既有 AI 管理接口，仅增加该管理查询接口。
- 本地验证：`.\.venv\Scripts\python.exe -m pytest tests -q` 全部通过；Python 编译、功能索引和 diff 检查通过。服务端策略为批次快照，客户端 Metadata 不能放宽门禁。
- jx 全量发布：release `20260806-065957-full-bd1c4762d48d`，wheel SHA-256 `bd1c4762d48d46911f79530f832ffc3a5033f630f8d8f055fb3b0412e4178e9e`；报告 `/opt/ai-management/var/reports/v2/20260806-065957-full-bd1c4762d48d-deploy`，发布前备份 `/opt/ai-management/backups/20260806-065957-full-bd1c4762d48d-predeploy`。API/Worker/Scheduler/UI/模型/发布服务同源，API ready、Dify 可达、测试页 HTTP 200、OCR autoscaler timer enabled/active；队列为空时 OCR Worker 不常驻。
- 测试页真实验收：选择 `general` 知识库和 `zsxq_post` 策略上传合成 Markdown，批次 `019fd42f-7599-7cc0-a4ce-6eacc1063dc1` 最终 `completed`，文件/条目 `succeeded`，模型为 `qwen-plus`、模式为 `realtime`，发布分片 `succeeded`；通过“读取条目详情”确认最终 Markdown 和 `record_type`、`source_title`、`topic` Metadata 已入条目。
- 测试期间问题已脱敏记录于 `var/reports/20260806-cleaning-policy/issues.jsonl`：OCR 显式授权门禁、快速发布缺少共享清洗模块导致的导入失败、测试页隐藏文件控件操作方式；均已处理并重跑成功。测试源文件和知识库测试条目按项目规则保留，未执行模糊名称清理。

## 0.31 2026-08-05 统一文档清洗链路发布与新数据验收（当前最新）

- 根因已闭环：正式上传链路原先的 PDF 入口与 `scripts/pdf_markdown_refine.py` 使用的共享核心不一致；`raw` 分支显式跳过表格清洗；转换产物保存早于表格清洗；策略/模型/人工复核后的正文没有统一最终无表格门禁；Dify 名称仍沿用原扩展名；文章人工复核还会错误地走普通单文档发布。上述问题共同造成原始 PDF/DOCX 名称、原始正文或表格进入知识库的风险。
- 当前统一链路：用户选择策略并上传后，先按策略支持矩阵校验扩展名；PDF/DOCX/Markdown/TXT 等输入统一得到 Markdown，再清洗 Markdown 表格；`cleaned` 继续走对应策略和模型/复核，`raw` 跳过策略语义处理但不跳过统一转换和表格清洗；父子分隔符、Metadata、内容哈希和最终正文在发布前再次校验。Dify 统一使用 `create-by-text`，正文为最终清洗后的 Markdown，`content_format=markdown`。
- 表格规则：可靠表格转为语义文本；无法可靠对齐的表格保留线性单元格文本，不猜测列关系；`table_review` 只进入内部审计，不作为用户可见 warning；最终进入知识库的正文不含 Markdown/HTML 表格。
- 发布命名与元数据：原始文件名只保留 stem，Dify 文档统一为 `.md`；同名文档按 `__001.md` 递增；系统 Metadata 不可被策略覆盖，策略 Metadata 覆盖公共 Metadata，文件身份/哈希等私有字段不发送到 Dify。文章策略保留合并 shard，并在 shard 层写入分隔符、父块限制和 shard Metadata。
- jx 完整发布：release `20260805-cleaning-unify-0d1125af5d56`，wheel SHA-256 `0d1125af5d5624428fca0dd920be73bdb9433401e438250ea4f4b5fd68aabdf1`；报告 `/opt/ai-management/var/reports/v2/20260805-cleaning-unify-0d1125af5d56-deploy`，发布前备份 `/opt/ai-management/backups/20260805-cleaning-unify-0d1125af5d56-predeploy`。API、Worker、模型 Worker、发布 Worker、Scheduler、UI、OCR 均为同一 release，API ready、迁移 `j6a7b8c9d0e1 (head)`、测试页 HTTP 200、OCR timer active。
- 新数据标准文档验收：PDF、DOCX、Markdown 三个 `standard_document` 案例均 `succeeded`；本地正文无表格、Dify 名称匹配 Markdown 规则、Metadata 边界通过。验收产生的 3 个批次、3 个 Dify 文档和 Redis 键已按 manifest 精确清理，未触碰历史正式数据。
- 新数据 raw 验收：PDF、DOCX、Markdown 三个 `raw` 案例均 `succeeded`；统一转换、最终无表格、Dify 名称和 Metadata 边界通过。验收产生的 3 个批次、2 个 Dify 文档和 Redis 键已按 manifest 精确清理，专用输入目录已删除，正式历史输入保留。
- 全量代表性验收另有 7 个非本链路失败：个人咨询模型严格复核/身份审计门禁和课程目录验收 runner 异常；已通过的 11/18 案例、3/3 聊天检查无表格或存储清洗失败。个人咨询 `review_required` 仍是安全终态，不为全绿而绕过策略门禁；该批次的 18 个新批次、8 个 Dify 文档已按 manifest 精确清理。
- 本地回归：全量 pytest `337 passed`；随后新增发布回滚契约测试通过，Ruff、Python 编译、功能索引和文档契约通过。发布过程中发现的 OCR 早期失败恢复边界已修复并同步远端；未修改 `AGENTS.md`，未提交或推送 Git。

## 0.30 2026-08-05 PDF 表格清洗与个人商业咨询批次入库（当前最新）

- 根因已闭环：`table_review` 是 PDF 转换非阻断告警，不是批次未入库的原因；原批次真正进入 `review_required` 的原因是 `personal_business_consultation` 严格模型契约拒绝 `decision_units[0].risks_or_limits` 的事实状态。生产链路此前还运行旧镜像，未包含共享 Markdown 表格清洗模块。
- 当前规则：正式链路先完成 PDF→Markdown，再清洗 Markdown 表格；可靠表格转语义句，坏行跳过并按表聚合一条脱敏日志，结构无法可靠对齐的完整表格块跳过，前后正文继续。`scripts/pdf_markdown_refine.py --clean-tables` 可复现该阶段；Markdown 围栏内容不处理。
- 已增加窄范围模型重试接口：仅允许唯一候选、已完成、`realtime`、`MODEL_CONTRACT_INVALID` 条目重新排队；严格契约不放宽、不自动改写、不启用 Bailian Batch。`BAILIAN_BATCH_ENABLED=false` 保持不变。
- jx 最终完整发布：release `20260805-pdf-table-final-b87e4ef9fb46`，wheel SHA-256 `b87e4ef9fb46d4025585278a37ccc37f0d72b6f8d302245d7f7ae7f3d6131c2c`；报告 `/opt/ai-management/var/reports/v2/20260805-pdf-table-final-b87e4ef9fb46-deploy`，发布前备份 `/opt/ai-management/backups/20260805-pdf-table-final-b87e4ef9fb46-predeploy`。SSH 曾在备份后断开，但远端发布进程继续完成；未并发启动第二个发布。
- 发布后证据：API、普通 Worker、realtime/batch model Worker、publication Worker、Scheduler、UI、OCR 均运行最终 release 对应镜像；API `/health/ready` 为 `ready`，数据库/Redis/Dify 均可达；OCR Worker 已按精确服务名强制重建并为 running。
- 指定批次 `019fcfe1-b610-7157-ba27-913a98cbb612` 已闭环：批次 `completed`，文件/文档 `succeeded`，文档错误码为空；转换 Markdown 非空、`blocking_reasons=[]`，保留 1 条 `table_review` 告警；Dify 文档存在、启用、索引 `completed`，父块 1、子块 4。未重传源文件、未按名称清理数据。
- 任务记录中的 `result_counts.succeeded=0` 属于修复统计逻辑部署前已完成的历史任务快照，不回填、不重复调用模型；文件/文档聚合状态和 Dify 回读是本批次入库结果依据。后续任务使用已修复的统计逻辑。
- 本地最终回归：从仓库根目录设置 `PYTHONPATH` 后，当前 pytest 收集 328 项，328 项通过；索引和排障文档契约通过。历史交接中的“384 项”是旧快照，已记录为测试报告问题，不作为当前数量。
- 本轮问题已记录于 `var/reports/20260805-full-test-jx-fix/issues.jsonl`；未修改 `AGENTS.md`，未提交或推送 Git。无待处理的本轮产品阻断项；受保护样本、跨域批量等旧环境阻塞仍按历史记录独立管理。

## 0.29 2026-08-05 商业顾问目录名称恢复与学习路线顺序修复（当前最新）

- 已处理用户可见文案：`ai_management/stream_filter.py` 不再对已审核目录中的课程、路线、阶段 ID 做一律删除，而是按目录快照转换为正式名称；同时覆盖中文/英文标签、裸 ID、括号形式、短 ID、跨 SSE 分片、旧占位词、错误名称与有效 ID 并存、课程列表占位项和已知机器字段名。
- 已处理课程/路线链路：`conversations.py` 对流式回答、历史正文/标题、推荐说明、专家建议和引用资源可见字符串使用同一目录映射；`courses.py` 保留决策 JSON、`course_cards`、`learning_route`、签名跳转链接、停止/反馈字段所需机器 ID。引用资源的 ID 元数据保持结构但不作为用户文案展示。
- 已处理路线顺序与确认：后端按“新手必看 → 入门阶段 → 高手进阶”排序并规范快照序号，保留已有 `stage_id`；快照版本升级后会自动丢弃旧缓存，避免线上继续使用旧阶段顺序。补充接受小程序实际使用的“我确认学习路线：路线名称”确认文案，确认路线后进入阶段确认，确认阶段后才生成课程卡片。未知阶段不猜测名称，保留原顺序并进入后续目录复核。
- 已处理知识库路线材料：`course_import.py` 生成路线资料时同时写入课程正式名称和内部 ID，供 Chatflow 决策使用；本轮不删除 Chatflow 决策字段，也未重新发布 Dify DSL。
- 本地回归：课程路线套件 `14 passed`；本次相关组合回归两组分别为 `3 passed`、`23 passed`，快速部署契约 `11 passed`，目标代码 Ruff、Python 编译、功能索引检查通过；Starlette/httpx 仅有既有弃用警告。一次完整跨文件长测曾超出工具等待窗口，未将其误报为全量通过。
- 远端配置与发布：`/internal/v1/entry-config` 已返回恰好 3 条固定问题且文案完全匹配；最终 `ai-management` release 为 `quick-20260805-142049-ddc348e62453`，源码指纹为 `ddc348e62453aff31e27c872e6a248cd0c170a3846a9c61892ec1b91b55b39fd`，报告 `/opt/ai-management/var/reports/v2/quick-20260805-142049-ddc348e62453-quick-deploy`，发布前备份 `/opt/ai-management/backups/quick-20260805-142049-ddc348e62453-predeploy`，模式为 `--keep-data`。API readiness、Dify 可达、测试 UI HTTP 200、API/Worker/UI/OCR 均通过。
- 真实 SSE：三条固定问题均 HTTP 200；课程/路线/阶段名称正常展示，可见回答和推荐说明未出现 ID、`course_`/`route_`/`stage_`、旧占位词；课程卡片 `redirect_url` 有效。连续路线链路已验证 `awaiting_route_confirmation → awaiting_stage_confirmation → active`，阶段确认后返回 5 张课程卡片；4 个合成用户会话删除接口均返回 HTTP 200。
- 部署问题已统一修复：首次成功路径因在 `trap - EXIT` 前未执行 OCR 恢复，导致原本 running 的 OCR worker 保持退出；已在本地/远端部署脚本中补充成功路径恢复并加契约测试，远端脚本原文件备份为 `/opt/ai-management/backups/20260805-142023-quick-deploy-script-before-ocr-restore-fix/`，最终 release 已验证 OCR 自动恢复为 running。
- 小程序侧开放项：当前仓库没有小程序页面源码，无法在本轮修改“已开始返回最终答案”、耗时/token、首屏问题悬浮和底部安全区遮挡等纯前端展示；后端已确保这些状态/机器字段不进入用户回答正文，页面是否渲染仍需在小程序仓库按现有固定接口核对。
- 仍需处理的测试环境项：文档契约 11 项中 10 项通过，唯一失败是测试配置 `_IncludedRouter` 缺少 `path`；`test_app_routing.py` 在本机 120 秒内未返回终态。它们不涉及本次课程名称、路线确认或用户可见文本代码，保留为独立环境/测试兼容问题，未伪报全量通过。
- 本轮不修改固定接口文档：`docs/小程序后端与 AI 管理服务接口对接交互版本.md`、正式接口标准和 `docs/API与SSE说明.md` 保持不作为本轮编辑目标；不修改 `AGENTS.md`，不提交或推送 Git。

## 0.28 2026-08-05 商业顾问首屏问题与用户可见内部标识过滤（历史）

- 已处理首屏问题：`dify/商业顾问（当前使用版本）.yml` 的 `workflow.features.suggested_questions` 固定为 3 条，均聚焦现有 `AI高手提效课`：系统学习 AI 工具、公众号运营入门、已有基础后的 AI 写作进阶课程选择。本轮只改首次进入页面的问题，不改回答后的推荐问题，也未新增接口。
- 已处理用户可见文本：`ai_management/stream_filter.py` 过滤课程、路线、阶段的中文/英文标签、完整或短内部 ID、`course_`/`route_`/`stage_` 裸标识、括号形式标识和旧占位词，并支持标识跨 SSE 分片出现；流式回答、历史正文/标题、推荐说明、专家建议和引用资源的可见字符串均接入同一规则。
- 已保留机器字段：Chatflow 决策 JSON 中的候选 ID 不删除；`courses.py` 仍负责 ID 校验、课程链接查询和签名卡片生成；`course_cards`、`learning_route` 及停止/反馈所需字段保持结构兼容。测试页在缺少路线名称时显示通用文案，不回退展示 `route_id`。
- 已同步稳定文档：`docs/API与SSE说明.md`、`docs/Dify创建导入绑定与契约测试.md`、`docs/小程序后端与AI管理服务接口对接标准.md` 和 `docs/小程序后端与 AI 管理服务接口对接交互版本.md` 已明确公开文本过滤边界、结构化机器字段兼容边界和首屏 3 条问题的配置/发布/缓存说明。
- 已修复文档契约问题：排障条目 `DEPLOY-013` 补齐必填“证据”字段；文档字段契约检查已通过。剩余失败不再是文档字段缺失，而是路由静态索引测试读取 `_IncludedRouter.path` 的兼容问题。
- 本地验证已完成：流过滤与统一 DSL 定向回归 `13 passed`；商业顾问流式/历史及课程卡片回归 `3 passed`；路线确认/阶段进度回归 `2 passed`；测试页回归 `13 passed`；优化版 DSL 回归 `9 passed`。目标代码和测试 Ruff、Python 编译、统一 DSL 构建检查、功能索引检查及 diff 检查均通过。
- 仍需处理的线上配置项：本轮未执行 Dify 保存/发布或远端写入，因此仓库 DSL 不是线上已更新的证据。需由有权限的发布人员在 Dify 商业顾问 App 中保存并发布后，核对 Dify `/parameters`、jx `/internal/v1/entry-config` 和 3 条问题的实际 SSE；入口缓存最长约 300 秒，需缓存失效后复核。
- 仍需处理的测试项：`tests/test_app_routing.py::test_modular_app_keeps_public_routes` 与 `tests/test_project_documentation_contract.py::test_static_route_index_matches_test_configured_fastapi_app` 当前在 `_IncludedRouter` 缺少 `methods`/`path` 处失败，未涉及本轮首屏或文本过滤代码，需单独诊断运行环境/路由测试兼容问题；全量 pytest、跨域批量测试和受保护清洗样本缺失仍沿用现有脱敏阻塞记录，不能据此宣称全量通过。
- 本轮未修改 `AGENTS.md`，未发布 Dify、未执行远端写入、未提交或推送 Git；上述未完成项已与代码已处理项分开记录。

## 0.27 2026-08-05 两份执行计划合并执行完成（当前最新）

- 本轮已在本地完成 PDF 共享核心迁移：`knowledge_cleaner/pdf_markdown_refine.py` 统一承载布局/目录/段落/表格/OCR 文本恢复，`scripts/pdf_markdown_refine.py` 保留为薄 CLI；上传 PDF 仍统一进入 `ai_ocr` 队列。OCR 目标配置为最多 50 页、任务超时 3600 秒。
- PDF 上传链路已按“告警继续、硬阻断复核”分级：`PDF_CONVERSION_WARNING`/`PDF_CONVERSION_BLOCKED` 复用 `SystemEventLog`；新增问题中心查询和精确 resolve 接口，`open` PDF 事件不受普通日志清理影响。问题中心不保存正文、原始文件名或密钥。
- 商业顾问适配层已加入跨 SSE chunk 的即时提示精确前缀去重：仅当最终答案开头（忽略前导空白）与已发送即时提示逐字相同时剥离；不匹配和正文中间重复原样保留；历史只保存最终答案。未修改 Dify DSL。
- 验收累计预算已增加机器配置 `JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN`，默认 5.00 元；`full_cleaning_acceptance.py` 在新的 Batch 付费任务审批前 fail-closed 累计估算费用。
- Dify PDF Workflow 和 Sandbox 临时探针已全部退役：jx 已从 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre` 恢复 Sandbox/SSRF/超时基线，临时 override 已删除；退役前快照为 `/opt/ai-management/backups/20260805-pdf-workflow-retire-pre`。本地测试 DSL 和探针材料已删除，历史报告保留。
- 验证结果：本地定向相关套件最终通过（发布/清理/策略/问题中心/预算/流式去重等 47 项重点回归，扩展契约组 63 项）；样本集清单为 31 个文件（22 PDF、8 Markdown、1 DOCX），非 PDF 9/9 成功，PDF 代表性文字/扫描/混合样本均按预期进入转换或复核状态。完整全量 pytest 和部分跨域批量测试因超时/缺失受保护夹具而保留脱敏问题记录，未将环境阻塞误报为代码通过。
- jx 第二次完整 wheel 发布为 `20260805-merge-fix`，报告 `/opt/ai-management/var/reports/v2/20260805-merge-fix-deploy`；API、Worker、Scheduler、UI 使用同源镜像，`/health/ready` 为 `ready` 且 `dify_reachable=true`，OCR 默认 50 页/3600 秒，机器费用上限回读 5.00 元，公网测试页 HTTP 200。
- 远端代表性 PDF 实测上传→`ai_ocr`→清洗→Dify 发布成功，随后通过 v3 精确清单删除 1 个批次、1 个 general Dify 文档和对应 Redis 键；首轮发现的 file-scoped PDF 事件孤儿已修复清理脚本并第二轮重跑，问题中心 `status=open` 现为 0。历史首轮告警按精确 ID resolve，未触碰正式数据。
- 商业顾问真实 SSE 实测 HTTP 200、10 个事件、3 个答案 delta、`message_end=completed`，会话精确删除返回 200，未检测到最终答案精确前缀重复。机器预算预检 `0.80/5.00` 通过。
- 测试阶段全部问题集中记录于 `var/reports/20260805-plan-merge/issues.jsonl`；已统一修复并重跑，未提交或推送 Git。
- 未完成项（环境/输入阻塞，不是本轮已确认的产品缺陷）：全量 `pytest` 在工具等待窗口内未返回终态；跨域批量测试仍有超时；`tests/test_consultation_v2.py` 依赖的受保护清洗样本缺失。已按测试规范保留脱敏证据，未生成替代样本、未把这些阻塞写成通过。既有排障模板缺少“最后验证”字段也未改写历史条目。

## 0.26 2026-08-05 商业顾问分段流式发布与 jx 测试页验收（历史）

- jx 最终发布号为 `quick-20260805-020249-034af4811733`；Core、UI、OCR Worker 均使用该发布号，发布模式为 `--keep-data`。发布前备份为 `/opt/ai-management/backups/quick-20260805-020249-034af4811733-predeploy`，报告为 `/opt/ai-management/var/reports/v2/quick-20260805-020249-034af4811733-quick-deploy`。
- 发布后 API `/health/ready` 为 `ready`，database、Redis、Dify 应用/知识库配置和 Dify 可达性均为 `true`；公网测试页 `http://112.74.37.111/ai-management-test/` 返回 HTTP `200`。Core/UI/OCR 镜像指纹和迁移 head 已由发布脚本核对。
- 商业顾问真实合成 SSE 验收：同一流收到 `progress_answer_started` → 1 个即时提示 `answer_delta` → `final_answer_started` → 2 个最终答案 `answer_delta`，无 `error`；即时提示长度 20、最终段长度 184，仅最终段写入历史。浏览器测试页已实测出现独立“即时提示”和“最终回答”区域，且请求可完成。
- 失败与修复：首次部署暴露服务器 `knowledge_cleaner/pdf_convert.py` 落后导致 OCR 预检导入失败；第二次暴露 UI 冷启动瞬时 502；随后补齐服务器已提交基线、部署脚本页面重试和 OCR Worker 无 systemd/完整 Compose 恢复逻辑，最终发布通过。失败报告保留在 `quick-20260805-013308-8408d2e634e4-quick-deploy` 和 `quick-20260805-014156-8408d2e634e4-quick-deploy`。
- 测试清理：本轮 3 个合成用户创建的本地会话及关联 Dify 会话均按精确 ID 删除，删除接口均返回 200；未清理正式课程、知识库、Redis 整库或上传卷。
- 当前修改入口：`ai_management/conversations.py` 处理 Dify `progress_answer` 的 `node_finished.data.outputs.answer` 并复用 `answer_delta`；`ai_management/test_ui.py` 按 status 边界分栏；节点来源/推荐问题/专家建议/朋友圈与写作引导语见 `docs/Dify创建导入绑定与契约测试.md`。

## 0.25 2026-08-04 PDF→Markdown Dify Code 节点用户自测窗口（历史记录）

- 按用户最新指令，远端 Dify Sandbox 当前保持自测配置：镜像 `jx-dify-sandbox-pdf-probe:20260804`，PyMuPDF/RapidOCR/ONNX Runtime 可导入，`WORKER_TIMEOUT=180`，API `CODE_EXECUTION_READ_TIMEOUT=180`，OCR 所需 syscall 白名单已临时放宽。
- SSRF 代理仅临时放行 `112.74.37.111:80` 供 Dify 文件签名 URL 下载；没有开放通用外网。Dify API、Sandbox 均 healthy，四个 OCR 依赖的导入烟测通过。
- 该配置是用户自测窗口，不是生产配置；结束后应恢复 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre` 中的 Sandbox/SSRF 基线，并把 API 读取超时恢复为 60 秒、Sandbox Worker 超时恢复为 15 秒。

## 0.24 2026-08-04 PDF→Markdown Dify Code 节点临时验证（历史记录）

- 新增本地测试 DSL `dify/dsl/pdf_to_markdown_code_test.yml`，仅用于 Dify 1.13.3 Workflow 的 Start→Code→End 验证；未修改 jx PDF 转换代码、API、Worker、数据库、Redis、正式 Dify 应用或知识库。
- 远端曾临时使用自建 Sandbox 镜像和受限测试文件服务；文本样本 Code 执行结果为 `succeeded/pymupdf/1 页`，扫描样本为 `succeeded/rapidocr/3 页`、平均置信度 `0.9969`。默认 seccomp 首次阻断 ONNX Runtime，临时扩大 syscall 白名单并把 Sandbox/API 执行超时调为 180 秒后成功。
- 首轮验证结束后曾恢复 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre` 对应的 Dify Sandbox 镜像、`WORKER_TIMEOUT=15`、API `CODE_EXECUTION_READ_TIMEOUT=60`、原始 SSRF 模板和原始网络/代理设置；恢复后最小 Code 回归输出 `pdf-probe-rollback-ok`，Sandbox 中 PyMuPDF/RapidOCR/ONNX Runtime 均不可见。随后按用户要求重新开启 0.25 自测窗口。
- 当前开放项：Dify 控制台导入/运行尚未完成（管理员会话不可用）；生产采用前仍需评审 syscall 放宽、CPU/超时、下载域名白名单、镜像和许可证维护。不得把本次 Workflow 验证结果写成生产转换策略已替换。

## 0.23 2026-08-04 RapidOCR 发布、缓存治理与测试页回归（历史记录）

- jx 已完成受控 RapidOCR 发布，当前正式 release 为 `20260804-rapidocr-semantic-78f9a1`；发布报告为 `/opt/ai-management/var/reports/v2/20260804-rapidocr-semantic-78f9a1-deploy`，发布前备份为 `/opt/ai-management/backups/20260804-rapidocr-semantic-pre`。`health-ready.json` 的 database、Redis、Dify 配置和可达性均为 `true`，Core/UI/OCR 镜像均通过导入/运行烟测。
- OCR 当前使用 `rapidocr_onnxruntime`、模型目录 `/opt/ai-management/var/ocr-runtime`、2 线程和 600 秒空闲停止；`jx-ocr-autoscaler.timer` 已启用。实测扫描 PDF 入队后 Worker 按需启动并完成处理，队列清空后按策略停止；空闲时不常驻 OCR Worker。
- jx Docker 存储治理已完成最终精确清理：私有可回收 BuildKit cache 约 19.9 GiB，根分区可用约 233.8 GiB；当前/上一版本 Core/UI/OCR 镜像保留，旧 jx 镜像已按精确标签清理；`jx-docker-storage-maintenance.timer` 已启用，每周日 03:30（随机延迟不超过 15 分钟）运行。Dify 镜像、卷、数据库、Redis 和上传卷未清理。
- 测试页 `http://112.74.37.111/ai-management-test/` 已使用指定的 3 个个人商业咨询样本完成一轮真实回归：最终批次状态 `completed`，3 个文件中 1 个 `succeeded`、2 个 `review_required`、0 个 `failed`；复核项由严格咨询 v2 证据状态门禁保留，未自动发布不可靠模型结果。模型任务预估费用约 `0.006863`，在本次 5 元上限内。
- 本次受控测试数据已在最终验收后完成 force reset：本地清洗表、精确清洗 Redis 键、测试上传目录和外部删除任务均归零；仅按恢复备份中确认的测试文档映射执行了 Dify 精确删除，正式内容未触碰。不得按名称正则删除正式 Dify 内容。
- 用户指定的六类样本已完成本地策略矩阵核验：12 个文件生成 142 个候选文档，0 个转换异常；`awaiting_model`、`pending` 和 `review_required` 均为预期工作流状态。脱敏统计见 `var/reports/20260804-pdf-rapidocr-docker/sample-matrix.json`。
- 本地定向 PDF/清洗/发布/缓存契约回归通过；完整 `test_model_cleaning_workflow.py` 仍受测试夹具固定的 `127.0.0.1:6399` Redis 前置缺失影响，已记录为环境阻塞，未发现应用断言失败。远端 jx Redis/Celery 与测试页模型验收均已独立通过。
- 脱敏问题与证据：`var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`；当前开放项为两个咨询样本需人工复核，不是 OCR 或服务失败。

## 0.22 2026-08-03 jx 测试环境 Dify 页面 pending 根因修复（历史记录）

- 已确认根因：jx 与 Dify 共用 `docker_default` 网络，jx 的 `api` 服务别名与 Dify 的 `api` 服务别名冲突；Dify Nginx 及内部配置访问 `http://api:5001` 时，Docker DNS 会同时返回 jx API、Dify API 副本和其他同网服务，导致部分请求命中错误端口或异常副本，表现为 502、重试或长期 pending。
- 已完成远端修复：Dify API 增加专用 `dify-api` 网络别名；Nginx、Dify 内部 API 地址统一切换到 `dify-api:5001`；API healthcheck 增加 `curl --max-time 3`；重建 Dify API、Worker、插件服务并 reload Nginx。jx `test-ui` 已恢复并改用 `ai-management-api:8000`，避免测试页再次命中 `api` 冲突别名。
- 远端配置备份：`/opt/ai-management/backups/20260803-193603-dify-pending-root-cause-fix`；jx UI 配置备份：`/opt/ai-management/backups/20260803-194249-jx-ui-api-alias-fix`。未修改 Dify 数据库、Redis、知识库或上传卷，未执行 `--remove-orphans`。
- 已验证：Dify 两个 API、两个 Worker 均 healthy；Dify `/apps` 连续 5 次 HTTP 200，`/console/api/system-features` 连续 5 次 HTTP 200，`/v1/parameters` 连续 5 次快速返回 401；jx `/ai-management-test/` HTTP 200；测试页容器通过 `ai-management-api:8000/health/ready`，ready 且 Dify 可达。已在登录态 Dify 预览页完成最小 Chatflow 对话：工作流 draft HTTP 200、`advanced-chat/workflows/draft/run` 返回 HTTP 200 `text/event-stream`，流完成 41765 bytes，输入框清空且无浏览器 Console 错误；最近 10 分钟 Nginx 无新的上游连接错误。
- 防复发要点：同一 Docker 网络内不得让 jx 与 Dify 依赖同名 `api`；新增服务或修改 Compose/Nginx 前必须检查 `getent hosts api` 是否出现跨项目服务，并使用项目专用别名；健康检查命令必须有客户端超时；不要用 `--remove-orphans` 清理历史测试容器。

## 0.21 2026-08-03 OCR 暂停与主机资源防护（当前最新）

- jx 已恢复 SSH；本次审计未执行重启。上一启动周期的日志和资源快照确认：无速率限制的 OCR 离线大包传输触发内存压力、Swap 换入换出和磁盘 I/O 饥饿，随后 Docker、Dify、ai-management 健康检查和 SSH 超时；未发现磁盘已满、文件系统硬件错误或 OOM-killer 证据。
- 当前 OCR worker、OCR timer、OCR 镜像构建和 OCR 大包传输均暂停；不执行 `PDF_OCR_ENABLED=true`，不启动 `ai-ocr-worker`，不安装 OCR autoscaler。此前不完整的 `/opt/ai-management/var/ocr-runtime` 已仅按精确目录清理，未触碰 Docker cache、Dify 数据库、Redis、上传卷或持久卷。
- jx 当前资源门禁已安装为 `/opt/ai-management/scripts/jx_resource_guard.sh`；Docker 构建上下文排除无关 `var/`、OCR 归档和分片。恢复 OCR 或任何大包构建前必须先通过资源门禁；失败时只停止本次任务，不停止核心服务。
- 重启后首次只读检查：根分区约 43% 使用、MemAvailable 约 26.52 GiB、Swap 0 MiB、I/O wait 0%、D 状态进程 0；Docker build cache 约 83.19 GiB（未清理，禁止将其与 Docker 持久卷混同）。
- 当前仍有独立开放项：`docker-api-1` 健康检查超时，且 healthcheck curl 缺少客户端超时；本任务不重启/杀掉 Dify API 容器，见脱敏问题记录 `OCR-DEPLOY-003`。测试页在重启后也需单独核对 `ai-management-test-ui-1` 状态。

## 0.18 2026-08-03 统一改造 fix8、页面回归与开放阻塞（历史当前快照）

- jx 已发布统一改造 fix8；发布前备份 `/opt/ai-management/backups/20260803-unified-fix8-pre`，报告 `/opt/ai-management/var/reports/v2/20260803-unified-fix8-deploy`，应用 wheel SHA-256 为 `a228e10d44a4083f3f123bd2c162ab08f455e4e2cf74f252a039314cba56c2ce`。`/health/live` 为 `ok`、`/health/ready` 为 `ready`，Alembic 为 `j6a7b8c9d0e1 (head)`，各服务使用同一 fix8 版本；容器实际回读 `BAILIAN_BATCH_ENABLED=false`。
- 公开测试页固定使用 `http://112.74.37.111/ai-management-test/`。六种当前清洗策略已逐一完成上传、异步清洗、复核/发布、状态回读和精确源删除；公众号正确 GB18030 样本、知识星球、小报童、资料钩子成功到条目详情；课程路线目录保持预期结构复核门禁；个人咨询保持模型契约复核。兼容策略 `course_information`、`learning_route` 未展示、未测试。
- 页面修改功能与接口覆盖已完成：课程推荐/路线确认/阶段确认内部标识已脱敏，课程入口和点击统计通过；商业顾问、朋友圈顾问、日常写作有正常终态/提示；商业顾问反馈与停止任务返回 200；管理接口读操作、删除错误确认校验、日志和诊断页均通过。未执行真实用户数据删除。
- 本地隔离回归：核心/API/UI `39 passed`，OCR/咨询/课程 `27 passed`；compileall、Ruff、脚本语法、功能索引和统一 DSL 检查通过。远端只读 Dify 契约应用调用通过，但两个知识库检索为 0；商业顾问轻量语义 E2E 为 `1/6`。
- 开放事项：个人咨询实时模型输出未通过既有 schema；课程目录需业务复核后才能决定发布；候选统一 DSL 尚未导入/发布，缺少安全 Dify 管理员会话或受控 Enterprise API/HTTPS 管理通道。不得把当前状态宣称为完整业务验收通过。

## 0.19 2026-08-03 清洗本地状态强制归零（当前最新）

- 已在现有归零脚本上新增受控 `force FORCE_RESET_LOCAL_CLEANING_STATE` 模式；普通 `execute` 仍拒绝活动批次。强制模式先停止本地清洗/模型/发布写入服务，拒绝活动模型供应商任务，完成可恢复备份后只清除本地清洗表、精确 `ai:cleaning:*` 键和批次 UUID 上传目录。
- jx 实际强制归零成功：清理前 `34` 个批次、`621` 条清洗文档、`37` 个文件、`15` 个模型任务、`5` 个发布分片、`34` 个 Redis 清洗键；清理后上述本地清洗数据和上传目录均为 `0`，无关 `external_deletion_task` 保持 `61`，Dify 删除操作为 `0`。
- 备份与恢复校验通过，备份目录为 `/opt/ai-management/backups/20260803-132240-forced-cleaning-local-reset`。清理后 API `ready`，测试页直连和 Nginx 均为 HTTP `200`。
- 清理未删除 Dify 知识库；清理后只读回读为 `general=6`、`course=110` 个文档，知识库内容由管理员自行处理。后续重新上传相同文件前，应先由管理员在 Dify 中清除对应测试文档。
- 本地脚本语法检查和清理契约/强制模式测试 `18/18` 通过；本次仅更新服务器运维脚本和操作说明，未改变服务镜像或接口。

## 0.20 2026-08-03 OCR 离线运行时准备（远端部署待恢复）

- 本地已完成 OCR 专用离线镜像、模型 SHA-256 启动校验、`ai_ocr` 队列按需启动/停止、systemd service/timer、发布/快速发布保护和相关契约测试；未修改 `ai_management/` 或 `knowledge_cleaner/` 业务 Python 代码。OCR 配置目标为 `PDF_OCR_ENABLED=true`、专用 `AI_OCR_IMAGE`、`/app/.paddlex/official_models` 和对应 manifest。
- jx 旧版常驻 OCR Worker 停止前已核对 `ai_ocr` 队列为 `0`；停止结果为正常退出。新的 OCR Compose、镜像、systemd 和 `.env` 开关尚未在 jx 生效，不能把本节视为 OCR 已部署或已验收。
- 离线模型与 Python 运行时已在本地生成并建立锁文件；传输到 `/opt/ai-management/var/ocr-runtime` 的大包中途导致 jx SSH/HTTP 应用层不返回 banner/响应。服务器可 ping，当前应先恢复主机管理面，再核对远端目录是否为完整包；不得直接使用不完整目录构建镜像。脱敏问题证据：`var/reports/ocr-deployment-20260803/issues.jsonl`。
- 本地已通过 OCR/发布契约、文档契约、功能索引 `--check`、Bash 语法和相关测试；远端构建、OCR 真实处理、自动发布、按需停止、失败保留队列和回滚验收均待完成。

## 0.17 2026-08-03 商业顾问应用访问 Key 切换（当前）

- jx 的 `DIFY_BUSINESS_APP_API_KEY` 已按用户最新指令切换到新的商业顾问 Dify 应用访问 Key；Key 明文未写入仓库、报告或终端输出。
- 修改前已备份服务器 `.env` 至 `/opt/ai-management/backups/20260803-024021-business-app-key-sync` 并验证副本一致；未修改 DSL 文件、Dify 数据库、知识库、PostgreSQL 或 Redis。
- 已强制重建 `api`、`worker`、`scheduler`；容器配置哈希与目标一致，商业顾问 Dify `/parameters` 返回 HTTP `200`，AI 服务 `/health/ready` 为 `ready` 且全部依赖检查通过，三个服务均为 running；本次未触发回滚。

## 0.16 2026-08-03 商业顾问应用访问 Key 再同步（当前）

- 用户在执行前把目标从另一候选 Key 改回上一轮指定的商业顾问应用访问 Key；jx 已按最新指令重新同步 `DIFY_BUSINESS_APP_API_KEY`，未写入被撤回的候选 Key。
- 修改前已备份服务器 `.env` 至 `/opt/ai-management/backups/20260803-023502-business-app-key-sync` 并验证副本一致；未修改 DSL 文件、Dify 数据库、知识库、PostgreSQL 或 Redis。
- 已强制重建 `api`、`worker`、`scheduler`；容器配置哈希与最新目标一致，商业顾问 Dify `/parameters` 返回 HTTP `200`，AI 服务 `/health/ready` 为 `ready` 且全部依赖检查通过，三个服务均为 running。

## 0.15 2026-08-03 商业顾问应用访问 Key 同步（当前）

- jx 的 `DIFY_BUSINESS_APP_API_KEY` 已同步到用户指定的商业顾问 Dify 应用访问 Key；Key 明文未写入仓库、报告或终端输出。
- 最新一次修改前已备份服务器 `.env` 至 `/opt/ai-management/backups/20260803-022555-business-app-key-sync`，并验证备份与原文件一致；本次未修改 DSL 文件、Dify 数据库、知识库、PostgreSQL 或 Redis。
- 已强制重建 `api`、`worker`、`scheduler`；容器内配置哈希与目标一致，商业顾问 Dify `/parameters` 返回 HTTP `200`，AI 服务 `/health/ready` 为 `ready` 且全部依赖检查通过，三个服务均为 running。
- 2026-08-03 02:25 的最新轮换已直接通过全部门禁且未触发回滚；更早一次切换中的一次性校验脚本问题已自动回滚并在修正后完成，不构成应用或 Dify 故障。

## 0.14 2026-08-02 Batch 开关、实时回归与课程样本入库（当前）

- 本地最终相关回归：模型清洗工作流 `27/27`、课程样本 `9/9`、AI 管理 `34/34`；课程/发布契约/测试页合并 `35/35`，最新 Batch 开关/课程字段兜底定向回归通过，compileall、Ruff、项目索引和发布脚本语法检查通过。接口交互文档保持冻结，未改 HTTP 路径、字段或枚举。
- jx 当前核心/UI 版本为 `20260802-course-catalog-context-fix-db4f37c71fd8`，7 个服务同源运行；`BAILIAN_BATCH_ENABLED=false`、实时价格快照 `qwen-plus-cn-realtime-2026-08-02`（输入 0.8、输出 2.0，单位为每百万 token）已由容器实际回读，`SAVE_CONVERSION_MARKDOWN=false`；近 20 分钟无 ERROR、CRITICAL、Traceback、OperationalError 或权限错误。发布前备份：`/opt/ai-management/backups/20260802-catalog-context-fix-pre`；发布报告：`/opt/ai-management/var/reports/v2/20260802-course-catalog-context-fix-db4f37c71fd8-deploy`。
- 真实样本批次已使用实时 Worker 完成：110/110 模型候选成功，0 失败、0 待复核；随后 110/110 Dify 文档发布回读成功，文件聚合为 `succeeded/completed`，未产生 Batch provider ID。输出包含 105 个课程实体和 5 条路线，正式库总量由基线 `9/2` 变为 `114/7`；课程/路线文档 Dify 引用 110 个，正文和 Metadata 均未含 URL，文档名均为原文件名加三位序号。
- 同名/同内容重复保护和精确源删除已现场验证：历史测试占位和失败批次均按精确 file ID 删除并释放去重；删除接口现在同步把已无活动文件的批次聚合为 `completed`。当前成功源的原始上传文件已按成功清洗生命周期删除，但未执行源级“重新上传”删除，因此同名再次上传仍会被保护，符合“先删除才能重传”。
- 真实样本暴露并已修复两个链路缺口：模型输出描述为空时仅使用输入来源字段组成事实摘要，不补造业务事实；路线模型请求补充确定性阶段/课程上下文，方向/路线目标仅在有来源值时兜底，源和模型均为空仍按同源原子门禁阻断。结构解析仍固定为 137 条源行、105 课程、5 路线，名称、链接、ID、路线归属和顺序未交给模型选择。

## 0.13 2026-08-02 课程目录自动链路部署与样本重传（历史快照）

- 本地目标套件第二轮通过：课程解析/模型工作流/发布契约共 `60/60`，AI 管理与路由 `47/47`；Ruff、compileall、项目索引检查通过。接口交互文档 SHA-256 保持冻结值，未改 HTTP 路径、字段或枚举。
- jx 已发布核心/UI 版本 `20260802-course-auto-3638cef104ce`，7 个服务统一该版本；`/health/ready` 的数据库、Redis、Dify 应用/知识库和可达性均为 true，近 5 分钟 API/Worker 无 ERROR、CRITICAL、Traceback、panic 或权限错误。发布前备份：`/opt/ai-management/backups/20260802-course-auto-pre`；报告：`/opt/ai-management/var/reports/v2/20260802-course-auto-3638cef104ce-deploy`。
- 旧课程目录源文件已通过精确源文件删除接口完成删除，数据库文件状态为 `excluded/source_deleted`，远端文档/分片引用为 0，重复保护已释放；正式课程/路线数量删除前后仍为 `9/2`。
- 同源样本已重新上传为 110 个候选（105 课程 + 5 路线）。自动费用门禁已通过，模型任务预估上限约 `0.056907`；当前百炼 Batch 状态为 `in_progress`（总 110、完成 0、失败 0），本地批次为 `model_batch_running`，尚未进入 Dify 发布阶段。该状态是供应商 Batch 调度等待，不是应用错误；终态和发布回读完成后需在本节追加更新。

## 0.12 2026-08-02 转换前置 Markdown 可配置持久化（当前最新）

- PDF/DOCX 在“转换完成、清洗策略处理前”可由 `SAVE_CONVERSION_MARKDOWN` 独立控制是否保存；开启不受 `APP_ENV` 限制。开启后 API 与清洗 Worker 共享 `/app/var/conversion-markdown`，jx 宿主机目录为 `/opt/ai-management/var/conversion-markdown`。
- 文件名为 `批次ID__文件ID__规范化原文件名.md`，文件权限 `0600`、目录权限 `0700`。开启时跨服务重启保留，默认保留 7 天并由夜间维护清理；关闭时不再写入，并在 API 启动及夜间维护时删除旧产物。只影响新上传或重试的 PDF/DOCX，不回填历史文件，也不提供 HTTP/测试页下载入口。
- jx 测试环境当前已关闭：API 与 Worker 均回读 `save=false`；共享 bind mount、目录属主和 7 天保留配置仍保持可随时开启。合成 DOCX 走真实转换/保存函数的历史验证仍有效；测试产物已精确删除，未调用上传 API、未写数据库或 Dify。
- 已发布核心/UI 版本 `20260802-conversion-md-be372c68d875`；7 个服务运行同一批版本，`/health/ready` 的数据库、Redis、Dify 配置和可达性均正常，近 5 分钟 API/Worker 无 ERROR、Exception、Traceback 或权限错误。发布前备份：`/opt/ai-management/backups/20260802-conversion-markdown-pre`；发布报告：`/opt/ai-management/var/reports/v2/20260802-conversion-md-be372c68d875-deploy`，校验和通过且未触发回滚。
- 正式发布脚本仍固定默认 `SAVE_CONVERSION_MARKDOWN=false`；如把 jx 从测试用途切换为正式环境，应显式关闭并重建 API/清洗 Worker。发布脚本已修复为从待发布镜像读取运行 UID/GID 后创建挂载目录，避免 `root:root 0700` 导致容器启动失败。

## 0.9 2026-08-02 当前源码 ZIP 快速部署复验（当前最新）

- 当前核心/UI 镜像：`quick-20260802-001946-4f369596b756`；上一回滚版本：`quick-20260801-230240-513925f01cf9`；Alembic 头未改变。
- 本地当前 `ai_management/` 与新 ZIP 均含 55 个有效文件，源码指纹一致；jx `--plan` 回读同一指纹。新容器内 `ai_management.ops.quick_reset` 文件哈希与本地完全一致，证明压缩包中新增代码已进入运行镜像。
- `--keep-data` 部署成功：7/7 服务运行新镜像，ready/测试页均为 200，近期严重日志为 0，报告和备份校验和通过；待部署 ZIP 成功后已自动删除。
- 部署前后 PostgreSQL 表计数、课程/路线/方向指纹、两个 Dify Dataset 文档指纹和 Redis 枚举键数均一致；未执行清理、未创建页面会话、未上传文件、未写 Dify。
- 浏览器测试页 9 个页签可见，“诊断 → 就绪检查”全部为 ready/true，控制台 0 error/0 warning。
- 发布报告：`/opt/ai-management/var/reports/v2/quick-20260802-001946-4f369596b756-quick-deploy`；备份：`/opt/ai-management/backups/quick-20260802-001946-4f369596b756-predeploy`；证据见 `docs/jx快速代码测试部署验证记录-2026-08-02.md`。

## 0.11 2026-08-02 清洗链路修复与 jx 发布（历史快照）

- 本地完成并部署清洗链路修复：默认单批 10 文件、5 个活动批次、单文件 200MB、批次 500MB、磁盘保留；同内容或同名必须先显式删除；新增源文件级删除入口并同步本地远端删除墓碑；Dify 文件名保留规范化原名，拆分时追加序号。
- PDF/DOCX 转换保持正文、标题、列表、缩进、表格在原顺序；PDF 表格不可靠时线性化并记录 WARNING，只有无正文、OCR 禁用或敏感信息残留才进入 `review_required`。测试环境可用 `SAVE_CONVERSION_MARKDOWN=true` 临时保存转换后/策略前 Markdown，服务重启清空，生产强制禁止。
- `course_route_catalog` 修复过期 XLSX dimension 读取：jx 同源样本重试后进入结构审核，生成 110 行审核表（105 课程 + 5 路线），其中 2 行同链接多名称；未结构确认前没有调用模型。模型只补全语义字段，不能选名称、改链接、ID、路线归属或顺序。

### 2026-08-04：RapidOCR/缓存治理与咨询样本回归交接变更记录

- 修改原因：用户确认实施 PDF 转 Markdown、RapidOCR 按需部署和 Docker 构建缓存治理计划，并要求在 jx 测试页使用指定样本验证、统一处理问题后更新文档。
- 修改前：顶部状态仍记录 OCR 暂停、旧 Paddle/PaddleX 模型路径和 BuildKit cache 未治理；个人咨询模型契约问题保持开放，测试页样本状态未形成最终批次证据。
- 修改后：顶部新增 0.23，记录 RapidOCR release/备份/ready、按需 Worker、缓存维护 timer、精确清理边界和测试页批次终态；相关部署手册、清洗接入说明、三份执行计划、deployment/data-cleaning 排障文档及脱敏问题报告已同步。未写入密钥、样本正文、真实用户标识或受保护 Dify 映射。
- jx 已完成完整 wheel 发布（同时含 `ai_management` 与 `knowledge_cleaner`），当前版本 `20260802-cleaning-fix5-e924d99fd6c0`；健康 ready、Alembic `i5f6a7b8c9d0`、7 个服务运行一致镜像，发布后近期严重日志为 0。发布前备份：`/opt/ai-management/backups/20260802-cleaning-fix5-pre`；报告：`/opt/ai-management/var/reports/v2/20260802-cleaning-fix5-e924d99fd6c0-deploy`。
- 模型分片发布成功后会把本地条目投影同步为实际 Dify 管理名；已对当前 3 个成功分片的 42 个本地条目完成精确回填，公众号分片回读为 `阿猫投资学_20260101-20260531__002.txt`。
- 已按精确批次重试公众号发布分片，29 个有效分片全部成功入库，5 个排除、1 个待复核；旧文件级 `SOURCE_ATOMIC_ROLLBACK` 已在源码修复中清除。小报童一个文件确认没有可清洗正文，未继续自动重试；另一个“等待模型”批次已重新创建模型任务并完成，17 个候选中 12 个成功、2 个排除、3 个待复核。
- 当前未完成：课程结构审核尚需管理员确定 2 个最终名称；小报童模型任务和公众号混合批次需继续观察；不能把待复核/排除项自动发布或自动重试。

## 0.8 2026-08-01 快速代码测试部署（历史快照）

- 当前核心/UI 镜像：`quick-20260801-230240-513925f01cf9`；上一回滚版本：`20260801-rag-cleaning-r20c`；Alembic 头未改变。
- 用户上传的固定 ZIP 已用 `scripts/jx_quick_deploy.sh --keep-data` 部署成功。7/7 服务运行且使用新镜像，ready 和测试页均为 200，近期严重日志为 0，发布报告及发布前备份校验和通过。
- 发布前后 PostgreSQL 表计数、课程/路线/方向指纹和两个 Dify Dataset 文档指纹一致；没有运行测试数据清理。Redis 运行时键数因服务重启/TTL 从 54 变为 53，不作为业务数据清理证据。
- 当前核心和 UI 各只保留本次与上一版本两个项目标签；历史标签已清理。为释放旧 UI `0.1.0`，精确删除了一个 5 天前已退出、无挂载、无端口、无 Compose 归属的回滚容器；未删除卷、Dify 镜像或构建缓存。
- 浏览器打开测试页并执行“诊断 → 就绪检查”通过，9 个功能页签可见，控制台 0 error/0 warning；本轮未发问、未上传、未写业务/Dify 数据。Streamlit 默认匿名使用统计仍会向第三方端点发送页面 URL、版本和匿名标识，是否关闭留作单独配置评审。
- 发布报告：`/opt/ai-management/var/reports/v2/quick-20260801-230240-513925f01cf9-quick-deploy`；备份：`/opt/ai-management/backups/quick-20260801-230240-513925f01cf9-predeploy`；日期化证据见 `docs/jx快速代码测试部署验证记录-2026-08-01.md`。

## 0.7 2026-08-01 R20c 隔离故障回归与外部门禁核验（历史快照）

### 隔离环境故障/补偿回归

- 本地隔离数据库完成 `20/20` 项故障、补偿、精确清理和用户数据边界测试：同源 Dify 文档失败补偿、manifest 完整性门禁、禁止模糊删除、Redis 精确键清理、摘要失败保护和课程聚合保护均通过。
- 这组测试证明代码级故障路径和清理红线有效，但不等同于生产 Dataset、Redis、PostgreSQL 或 Worker 的真实破坏性演练；本轮未停止生产服务、未修改正式 Dify 数据。

### 外部依赖核验

- 当前 SSH 配置只有 AI 服务主机 `jx-server`；`jx-backend` 别名无法解析，无法从小程序后端真实主机发起私网 `172.30.157.197:8000` 请求。因此小程序真实端到端仍不能宣称通过。
- 40 条人工语义门禁缺少已确认的评审样本、评分标准和评审人；自动 RAG 指标不能替代人工语义结论。
- HTTPS 切换缺少正式域名、证书和切换窗口；临时公网 HTTP `/ai-service/` 保持不变，不进入生产发布口径。

### 当前仍未完成

生产 Dataset 破坏性故障注入、Redis/PostgreSQL/Dify/Worker 真实恢复、小程序真实客户端端到端、40 条人工语义门禁、灰度发布和公网 HTTPS 迁移仍未完成。

## 0.6 2026-08-01 R20c 生产门禁复核（历史快照）

### 20 并发流式压测

- 通过临时公网 `/ai-service/` 入口，以 20 个并发合成用户连续运行 600 秒，共 `400` 次请求，`400` 成功、`0` 失败。
- 首事件 P95 约 `503ms`，总耗时均值约 `17.9s`；压测期间 jx `/health/ready` 持续为 ready，近窗口 API/Worker 日志无 ERROR、CRITICAL、Traceback 或 panic。
- 压测用户已通过官方管理接口精确删除，本地画像、会话、消息和分段均为 `0`。官方接口产生的 `external_deletion_task` 仅是本地待处理队列；本轮不执行该队列，不直接删除 Dify 产生的会话数据。

### 仍未完成的生产门禁

生产 Dataset 破坏性故障注入、Redis/PostgreSQL/Dify/Worker 故障恢复、小程序真实客户端端到端、40 条回答人工语义门禁、灰度发布和公网 HTTPS 迁移仍未完成。20 并发压测通过不等于这些门禁通过。

## 0.5 2026-08-01 R20c 清洗链路与最终清理结果（历史快照）

本节覆盖 R20b 后续修复、课程策略复测、jx 线上复核和测试数据清理；前一节 0.4 保留为中途快照，不再代表当前运行事实。

### 发布与验证

- jx 当前核心镜像为 `jx-now-ai-management:20260801-rag-cleaning-r20c`，测试页使用同批构建的 UI 镜像；Alembic 为 `h4e5f6a7b8c9 (head)`。
- 清理后 `/health/ready` 的数据库、Redis、Dify 应用、知识库和 Dify 可达性均为 `true`；API、Worker、模型 Worker、发布 Worker、Scheduler 和测试页镜像一致，近 20 分钟服务日志未发现 `ERROR`、`Traceback`、死锁或 panic。
- 本地测试按文件分组完成全部 `205` 项：大规模清洗 `29/29`、AI 管理服务 `32/32`、模型工作流 `16/16`、模型契约 `2/2`，其余契约/路由/课程/清理/页面测试全部通过。一次合并全量命令超过工具等待上限未回传终态，不能把它作为独立全量证据；分组结果覆盖同一收集清单。

### 真实页面与课程策略

- 公众号认知分享：`128` 个候选中 `106` 个成功、`14` 个排除、`8` 个待复核；百炼输入/输出文件按精确文件 ID 完成清理，成功正文和 Metadata 未发现 URL 或邮箱。排除依据是商业相关性，不是“见广告即删”。
- 个人商业咨询：R20b 后的独立批次完成，重复内容按指纹排除；旧的待复核咨询通过页面复核排除，未直接改库。
- 用户指定的课程信息与学习路线工作簿被正确拒绝：实际文件只含一个不完整表头，缺少 `课程链接`、`阶段`、`学习路线` 等必需列。该结果是输入与规格不一致，不是放宽解析器的理由；计划中“137 行/105 课程候选”的基线不能由此文件复核。
- 使用符合四列契约的合成工作簿完成 `course_route_catalog` 全链路：3 门课程、2 条路线进入模型候选，qwen-plus 返回的标量字段列表被契约层规范化为文本；人工审核表批准并导入后，5 份 Dify 文档均索引成功，父/子块为 `1/1`，正文和 Metadata 均无 URL/邮箱，数据库与路线引用一致。

### 最终精确清理与保留数据

- 清洗数据库中的批次、文件和文档均为 `0`；正式课程 `9` 门、正式路线 `2` 条保持不变。
- Dify 通用知识库为 `0`；课程知识库仅保留正式课程文档，残余非正式课程/通用测试文档已通过精确 manifest 和独立备份清理，未按名称模糊删除。
- Redis 清洗批次键和 Celery 任务结果键均为 `0`；配置/绑定等运行时键保留。
- 通过官方管理接口按精确测试用户删除 `12` 个合成用户及其会话、消息、分段；系统事件日志保留为审计记录，不作为测试业务数据继续清空。

### 未完成生产门禁

20 并发 10 分钟压力测试、生产 Dataset 破坏性故障注入、小程序真实客户端端到端、40 条回答人工语义门禁和灰度上线仍未完成；临时公网 HTTP `/ai-service/` 代理仍不是正式生产安全方案，正式使用前需切换 HTTPS 或恢复私网访问。

## 0.4 2026-08-01 R20b 清洗链路验收进度（历史中途快照）

本轮已完成 R20b 修复、发布和部分真实页面验收；本节只记录当前事实，不代表整轮验收已经结束。

### 发布与本地验证

- jx 当前版本：`jx-now-ai-management:20260801-rag-cleaning-r20b`、`jx-now-ai-management-ui:20260801-rag-cleaning-r20b`。
- 发布前备份、数据库/Redis/上传卷安全快照和恢复校验已完成；Alembic 为 `h4e5f6a7b8c9 (head)`。
- `/health/ready` 显示数据库、Redis、Dify 应用、知识库和 Dify 可达性均为 `true`；API、Worker、模型 Worker、Scheduler 和测试页均为 R20b，无近期错误、Traceback 或死锁日志。
- 本次修复解决普通策略中“模型任务已完成但文件仍停在 `awaiting_model`”的问题：模型任务结束时统一刷新文件状态，使待复核文件能落到 `review_required`，批次能正常聚合为 `completed`。
- 最新修复后的定向模型工作流测试为 `16/16`；第二轮全量回归未出现业务断言失败，仅因接口索引自动区的 Worker 列表未同步出现 2 项索引契约失败，重新生成索引后索引契约测试 `11/11` 通过，索引检查和差异检查通过。索引更新后尚未重新跑一遍完整套件，不能把本记录写成“最终全量回归已重新通过”。

### 真实测试页结果

以下测试均使用独立页面会话；当前新测试数据暂不清理，等待全部策略完成后统一按精确批次清单、备份和复核结果清理。

- 个人商业咨询案例：DOCX 与 PDF 批次已完成；1 个文件 `succeeded` 并写入 Dify（父/子块 `1/3`），1 个文件 `review_required`；实时 qwen-plus 任务已完成。该结果确认 R20b 修复了原先成功后文件状态不刷新的缺陷。
- 知识星球帖子：相关帖子成功入库，无关红包/广告类帖子被 `excluded`；批次已完成，成功内容未发现 URL、邮箱或“加微信/扫码/红包”等广告残留。
- 资料钩子：两个 PDF 均 `succeeded`，Dify 父/子块分别为 `8/26`、`23/73`，无警告，存储内容未发现 URL 或邮箱。
- 书籍：批次和文件均 `succeeded`，Dify 父/子块为 `4/16`；目录识别信息保留在审计字段，存储正文未发现目录标题或点线页码目录项，确认目录未进入知识库正文。
- 小报童帖子：两个文件均 `succeeded`，共生成 6 份 Dify 文档；实时 qwen-plus 任务已完成，存储内容未发现 URL、邮箱或已知广告词。
- 微信公众号认知分享：已解析 2 个文件，共 128 个候选条目；页面已确认费用并启动百炼 Batch，服务端已成功上传输入文件并创建 Batch，当前仍为 `model_batch_running`/轮询中，尚未进入终态。保存进度时仅停止了本地轮询命令，没有取消服务端 Batch；jx Scheduler/Worker 会继续轮询，暂不能清理或判定最终结果。

### 已完成、未完成和下一次继续顺序

- 之前污染页面会话已按精确批次和文档清单清理，正式 `general`、`course`、课程和路线资料未作为清理目标；本轮 R20b 新测试数据仍保留。
- 尚未完成：等待公众号 Batch 终态并核对 128 个候选条目的过滤、Metadata、Dify 入库和文件/批次状态；使用课程信息与学习路线 XLSX 完成 `course_route_catalog` 真实页面测试；全部测试后建立最终精确清理清单并先备份再清理；最后同步计划、策略规范、接口索引、ADR、排障文档和本交接文档。
- 下次继续顺序：先查公众号批次数据库状态和 jx 日志 → 完成课程策略测试 → 对所有本轮测试批次做精确基线和清理 → 复核正式知识库未被误删 → 完成文档同步和最终验收记录。
- Nginx 公网 `/ai-service/` 临时 HTTP 代理、本地/服务器密钥配置本轮未修改。

## 0.3 2026-07-31 临时公网 HTTP 联调入口（当前网络最新）

因小程序后端私网暂时不可达，已按用户确认启用临时公网 HTTP 联调入口：

- 对外基础地址：`http://112.74.37.111/ai-service`
- 访问方式：在原有公网 `80` 端口上由 Dify Nginx 代理到 `ai-management-api:8000`，并移除 `/ai-service/` 前缀后转发。
- API 容器仍绑定 AI 服务器私网 `172.30.157.197:8000`；没有开放公网 `8000`，原有 `DOCKER-USER` 规则仍只允许 `172.17.28.83/32`。
- `X-Internal-Token` 鉴权仍由 AI 服务校验；未带 Token 的公网入口探针返回 `401 UNAUTHORIZED`。
- Nginx 配置校验和 reload 通过；既有代理配置关闭缓冲并保留长超时，适用于 SSE。
- 外部验证：`/ai-service/health/live`、`/ai-service/health/ready` 均返回 `200`。
- 服务侧验证：经 Nginx 代理的入口配置返回 `200`；商业顾问 SSE 以 `message_end.status=completed` 结束；合成测试用户已精确清理。
- 回滚材料：`/opt/ai-management/backups/20260731-093458-temporary-public-http`。

这是临时 HTTP 方案，Token 会经过明文 HTTP 传输，不得作为正式生产方案。正式联调应改为 HTTPS 域名或恢复私网访问；完成后删除 `/ai-service/` 代理文件及其 include，并恢复备份中的私网防火墙脚本。

## 0.2 2026-07-30 小程序后端私网联调网络配置（当前网络最新）

jx 已完成小程序后端 `172.17.28.83` 到 AI 服务 `172.30.157.197:8000` 的服务器侧网络配置；未修改应用镜像、`.env`、数据库、Redis、Dify 网络或公网课程跳转端口。

- 原问题：jx 的 `docker0` 使用 `172.17.0.0/16`，系统会把小程序后端地址误判为 Docker 本地网段，回程路由走 `docker0`。
- 当前路由：为 `172.17.28.83/32` 增加经私网网关、从 `eth0` 返回的精确路由；Netplan 已生成持久配置。
- 当前防火墙：`DOCKER-USER` 仅允许来源 `172.17.28.83/32` 访问宿主机私网地址的 TCP `8000`，其余从 `eth0` 进入的同目标流量被拒绝；规则由已启用的 systemd oneshot 服务在 Docker 启动后恢复。
- Dify 边界：API 仍加入 `docker_default`，容器间访问和 Dify 网络未变。
- 回滚材料：`/opt/ai-management/backups/20260730-204012-miniapp-private-route`；撤销脚本位于该目录，路由与防火墙实际撤销→恢复演练通过。

服务器侧验证：

- 私网 `live` 返回 `ok`；`ready` 的数据库、Redis、Dify 应用、知识库和可达性均为 `true`。
- 入口配置返回三个入口；合成商业顾问请求 HTTP `200`，SSE 以 `message_end.status=completed` 结束。
- 合成用户数据已通过管理接口精确删除，复查剩余会话为 `0`。
- 公网 `/r/{token}` 使用无效签名探针返回 `404`，证明课程跳转入口仍可达且未生成有效点击。

仍需外部完成：

- 必须从小程序后端主机实际请求 `http://172.30.157.197:8000`，才能关闭真实端到端连通验收；服务器本机测试不能替代来源主机测试。
- 阿里云安全组需确认只允许 `172.17.28.83/32` 访问 TCP `8000`；本次只修改 jx 主机内部网络与防火墙。
- `PUBLIC_BASE_URL` 仍为公网 HTTP 地址，正式课程跳转上线前需改为可由用户手机访问的 HTTPS 网关并复验。

日期化证据：[小程序后端私网联调网络配置与验收记录-2026-07-30](docs/小程序后端私网联调网络配置与验收记录-2026-07-30.md)。

## 0.1 2026-07-30 会话记忆与管理审计字段发布（本任务最新）

本任务已发布到 jx，范围仅包含商业会话/管理审计字段、测试页会话状态和对应迁移；未同步 Dify DSL、Prompt 或清洗脚本。

- 核心镜像：`jx-now-ai-management:20260730-user-id-session-77481e5-r2`
- 测试页镜像：`jx-now-ai-management-ui:20260730-user-id-session-77481e5-r2`
- Alembic：`f2a1d6c4b8e9 (head)`
- wheel SHA-256：`1ebf1ba810cb806dbaa4ff1b025c0cbc7d5039963efdbc255cf89a8c0125f7d2`
- 发布前备份：`/opt/ai-management/backups/20260730-user-id-session-r2-predeploy`
- 发布证据：`/opt/ai-management/var/reports/v2/20260730-user-id-session-77481e5-r2-deploy`

已验证：

- 本地按文件分批执行测试共 `100/100` 通过；单次全量命令 900 秒超时，未报告断言失败，已作为既有慢套件问题留在 `var/reports/20260730-user-id-session/issues.jsonl`。
- 迁移在临时 SQLite 数据库完成升级→降级→再次升级；jx PostgreSQL 已从 `e11c7f4a2d90` 升至 `f2a1d6c4b8e9`。
- jx API/Worker/Scheduler/测试页均运行新镜像；live/ready 和测试页健康入口通过。
- 合成用户回归验证第二轮复用本地会话、显式新建产生不同会话、管理列表/详情返回 `user_id` 与课程/见解/token/耗时字段；清理后会话数为 `0`，未写入知识库。

本次清洗格式核对结论：上传接口按所选策略的 `supported_extensions` 校验，不是所有策略都接受全部格式；`.docx` 由文档解析器转为 Markdown，`.pdf` 由 PDF 解析器转为 Markdown，`.md/.markdown/.txt` 读取为文本/Markdown，随后再交给策略自己的解析、脱敏和门禁。具体策略支持矩阵以 `docs/清洗策略接入说明.md` 和服务端策略声明为准。

日期化证据：[会话记忆与管理审计字段发布验收记录-2026-07-30](docs/会话记忆与管理审计字段发布验收记录-2026-07-30.md)。

## 0. 2026-07-30 接口契约与清洗全链路发布结论（最新）

本轮“接口契约、清洗全链路与 jx 发布验收”范围已经完成并通过，旧章节保留为历史记录。

当前线上版本：

- 核心镜像：`jx-now-ai-management:0.2.1-contract-cleaning-90a23f26-r3`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-contract-cleaning-90a23f26-r3`
- Alembic：`e11c7f4a2d90 (head)`
- wheel SHA-256：`90a23f26f51ff3dde4c5c4695e9f4584e4071951c09f896eb0dba99a56456fc2`
- 回滚备份：`/opt/ai-management/backups/20260730-predeploy-r3-contract-cleaning`

已通过的验收：

- 本地最终测试 `85/85`。
- jx 健康、迁移、Dify 契约、业务生命周期、停止生成、业务场景和接口契约共 8 组退出码全部为 `0`。
- 接口契约 `37/37`。
- 真实样本清洗 `15/15`，公开聊天与引用 `3/3`。
- 小报童确定性拆分 `3+3`；公众号确定性拆分 `29+84`。
- RAG：通用 Recall@5 `1.0`、课程 Recall@5 `0.9203`、聚合 Recall@5 `0.9444`，Hit@5 与关键问题命中率均为 `1.0`。
- 咨询 DOCX/PDF、身份脱敏、跨 SSE 分片过滤、Dify 管理命名、源文件级补偿回滚和课程/路线目录事务均已验证。

验收后的线上数据状态：

- 正式课程 `9`，正式路线 `2`，无额外课程或路线。
- Dify 课程知识库 `11` 份正式文档，通用知识库 `0` 份文档。
- 聊天、画像、进度、点击、清洗批次、清洗文档、外部删除任务和事件日志均为 `0`。
- 上传卷为空。
- Redis 有 `6` 个需保留的业务配置键；服务启动后另有 `5` 个 Celery 绑定/健康缓存运行时键，不属于测试数据。

证据与排坑记录：

- [接口契约与清洗全链路验收记录-2026-07-30.md](docs/接口契约与清洗全链路验收记录-2026-07-30.md)
- [接口清洗发布排坑记录-2026-07-30.md](docs/接口清洗发布排坑记录-2026-07-30.md)

仍未纳入本轮完成口径：20 并发 10 分钟压力测试、生产 Dataset 破坏性故障注入、小程序真实客户端端到端验收、40 条回答的人工语义门禁及灰度上线。后续执行时不得把本轮检索通过等同于这些项目已完成。

## 1. 历史结论（2026-07-23 至 2026-07-29）

> 本节至第 8 节保留早期联调审计链。标题中的“当前”“后续”等词均以记录当时为准；与第 0 节或当前 ADR 冲突时不得用于指导新任务。

AI 管理服务的代码、Dify 应用、测试知识库和服务器基础部署已经基本打通。

商业顾问 Chatflow 已完成双测试知识库绑定并发布：

- “通用知识检索”绑定 `business-general-test`。
- “课程路线检索”绑定 `business-course-test`。
- 发布后的真实 API 调用已确认两个节点分别召回对应知识库。

当前仍不是“全部验收完成”状态。已进入发布后的契约、业务集成、摘要画像、清洗、RAG、并发和故障测试阶段。

### 1.1 2026-07-28 文档清洗链路最新状态

文档清洗策略与入库链路已经完成代码实现、服务器发布和真实链路验收。

当前线上版本：

- 核心镜像：`jx-now-ai-management:0.2.1-cleaning-27a8bc0-r7`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0`
- Alembic 版本：`6a5f9e2b1c44 (head)`
- 离线 wheel：`jx_now_ai_management-0.2.1-py3-none-any.whl`
- 离线 wheel SHA256：`1f26e3920051afd046840b8716691aed17497b08095c900b8d4bd2edd116f3d1`

本轮新增并已验证：

- 六类正式清洗策略可从 AI 管理页读取。
- 上传批次支持文件级状态、条目级状态、单文件重试、单条目重试和人工复核。
- 成功条目会验证 Dify 索引状态、父子块数量和 Metadata 写入结果。
- Redis 可查询批次状态与文件清单。
- 成功源文件会删除；失败文件按保留期暂存。

2026-07-28 线上真实验收结果：

- `/health/live`、`/health/ready` 均通过。
- AI 管理页测试路径 `/ai-management-test` 可访问，`/ai-management-test/_stcore/health` 返回 `ok`。
- `scripts/dify_contract_check.py`、`scripts/live_business_lifecycle.py`、`scripts/live_stop_check.py` 均通过。
- 真实上传“1 个有效 Markdown + 1 个非法 EXE”批次后：
  - 批次状态 `completed`
  - 成功文件 `succeeded`
  - 非法文件 `failed`
  - `all_succeeded=false`
  - `all_processed_without_error=false`
  - 成功条目远端父块数 `1`、子块数 `1`
  - 成功源文件已删除
  - 验收生成的测试知识库文档已删除
- 真实待复核链路补测后：
  - 正式策略列表返回 6 个策略
  - 纯待复核文件终态稳定为 `review_required`
  - 待复核条目直接重试返回 `409`
  - 纯待复核文件直接重试返回 `409`
  - `replace_and_approve` 可成功入库并回读远端父子块计数 `1/1`
  - `exclude` 后文件终态为 `excluded`，批次聚合状态同步刷新

本轮发布中修复过 4 个真实缺陷：

1. 待复核条目可被直接重试并发布，绕过人工复核。
2. 纯待复核文件在条目生成完成后状态未落到终态，仍停在 `processing` / `cleaning`。
3. `exclude` 复核后文件和批次聚合状态未即时刷新。
4. 服务器 `Dockerfile.offline` 实际运行代码来自 `wheelhouse`；只同步源码再重建镜像不会带上本地修复，必须先替换 wheel 再重建。

对应本地回归结果：

- `python -m pytest tests -q`：`55 passed`

### 1.2 2026-07-29 接口、测试页与真实清洗链路复验（r8）

本轮验收范围已经完成，不应再将接口交接文档视为“仅按代码推断”：

- 核心镜像：`jx-now-ai-management:0.2.1-cleaning-27a8bc0-r8`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0-r8`
- `BUILD_VERSION`：`0.2.1-cleaning-27a8bc0-r8`
- wheel SHA256：`d3ffbbccf2dd633fc9d626fb574baf7586281809a9c6892467afd7ca1602649a`
- Alembic：`6a5f9e2b1c44 (head)`

- 本地全量回归：`58 passed`
- 按 [小程序后端与AI管理服务接口对接标准.md](docs/小程序后端与AI管理服务接口对接标准.md) 的路径、参数和返回结构实测：`49/49` 通过。
- 浏览器测试页真实用户点击：`15/15` 通过；商业顾问问题“我想系统学习 AI 工具，应该从哪里开始？”稳定返回路线与课程卡片。
- 一条真实知识星球 Markdown 样本完成上传→`zsxq_post` 清洗→Dify 入库→父子块/Metadata 回读→禁用→启用→删除；父/子块数为 `3/11`，Metadata 中的版本值为 r8。
- 专用 stop 校验：HTTP `200`，终态 `message_end.status=stopped`。

验收报告与截图位置：

- [测试页与接口全量实测记录-2026-07-29.md](docs/测试页与接口全量实测记录-2026-07-29.md)
- 服务器：`/opt/ai-management/var/reports/v2/20260729-interface-contract-r8-retry1/`
- 浏览器截图：本地 `output/playwright/`

发布过程出现过宿主机无 `pg_restore`、Windows CRLF 传入 Bash、旧导出镜像变量覆盖 `.env` 三个运维问题，均已在部署手册中修正；最终 API、Worker、Scheduler、测试页均运行 r8。

### 1.3 2026-07-29 测试页全接口手工验收入口（r9）

- 本次仅升级测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0-r9`；核心 API、Worker、Scheduler 保持 r8，未迁移或修改数据。
- “AI 管理只读”已替换为“AI 管理接口”：可手工验收管理会话详情、保留策略与输出风格读写、知识库文档列表 / 启停 / 删除、删除指定用户 AI 数据。
- 页面现有的聊天、工具、课程、清洗、日志、诊断入口仍保留，因此交接标准里的小程序后端和 AI 管理接口均有对应的手工验收入口。
- 写操作要求勾选确认；删除文档必须输入完整名称，删除用户数据必须输入 `DELETE <用户 ID>`。r9 浏览器已实测读取配置与知识库文档成功，删除按钮在确认前保持禁用。
- wheel SHA256：`4c3fdb635c055f53206adfa1887806d8836f3608ab60a41569679feae9144c37`；回退材料：`/opt/ai-management/backups/20260729-ui-contract-full-r9/`。
- 本地全量 pytest 本轮出现超时及 Windows 测试数据库文件锁，已记录在 `docs/测试页与接口全量实测记录-2026-07-29.md`；新增页面专项校验、语法和测试页 ruff 检查均通过。后续若改动 API 或清洗逻辑，必须先定位该慢用例并重新完成全量测试。

### 1.4 当前交接状态（2026-07-29，接口专项核验）

- jx 当前已部署版本：`jx-now-ai-management:0.2.1-course-route-fc36f00-r4`；API、Worker、Scheduler、测试页均为同一 r4 发布版本。
- 对外接口标准以 `docs/小程序后端与AI管理服务接口对接标准.md` 为准。自 `10cd1ea` 以来，公开路由、请求头、请求体、SSE 事件名和返回字段未改变。
- 本地 `ddfbb67` 尚未发布，只增加路线确认语义的服务端校验，不要求小程序后端修改接口调用。
- 本次只进行接口契约专项测试，不重复页面上传、jx 真实链路或全量回归；专项测试 `17/17` 通过，结果已同步到本文和接口文档。
- 已知未关闭问题：全量回归中“第一阶段已经完成”未被当前阶段完成词表识别；该问题影响路线进度语义，不改变公开接口结构。

## 2. 已完成并验证的工作

### 2.1 本地代码

已经实现：

- FastAPI AI 管理服务。
- 商业顾问本地逻辑会话和 SSE。
- 朋友圈顾问、日常写作 Workflow 代理。
- Dify 应用客户端和知识库客户端。
- PostgreSQL 数据模型和 Alembic 迁移。
- Redis 缓存、锁、课程快照和点击统计。
- Celery Worker 和凌晨定时任务。
- 对话摘要、画像候选和摘要游标逻辑。
- 课程与路线 Markdown 导入 CLI。
- 课程 ID、路线 ID、启用和审核状态校验。
- 课程跳转签名链接。
- 代码内置清洗策略、批量上传、状态查询和重试。
- Dify 索引、Metadata、零分块检查和失败补偿。
- Streamlit 极简测试页代码。
- 四份 Dify DSL 和四份 Prompt 文档。
- 测试课程、测试路线、通用资料和 40 条 RAG 问题。

本地验证结果：

- `python -m pytest -q tests`：27 项通过。
- Docker Compose 配置解析通过。
- Alembic 初始迁移可执行。
- 课程测试资料 dry-run：9 门课程、8 门启用课程、2 条路线。

### 2.2 Dify 应用

| 应用 | 类型 | 当前实测 |
|---|---|---|
| 商业顾问 | Chatflow | 已发布版本可调用；两轮续聊成功 |
| 摘要画像 | Workflow | 调用成功并返回结构化结果 |
| 日常写作 | Workflow | 调用成功 |
| 朋友圈顾问 | Workflow | 调用成功 |

已经验证：

- 商业顾问第一轮返回 HTTP 200。
- 第二轮复用同一个 Dify `conversation_id` 并返回 HTTP 200。
- 按正确 JSON body 删除临时 Dify 会话返回 HTTP 204。
- 摘要画像、日常写作、朋友圈顾问 Workflow 均返回 `succeeded`。

注意：早先出现过一次会话删除 415，是临时探针错误地把 `user` 放在查询参数中导致。正式客户端使用 JSON body，已实测删除返回 204，不是产品缺陷。

### 2.3 Dify 测试知识库

知识库 API 地址：

```text
http://112.74.37.111/v1
```

API Key、App Key 和内部 Token 不写入本文，均已保存在本地和服务器的 `.env`。

| 知识库 | Dataset ID | 当前数据 |
|---|---|---|
| `business-general-test` | `19206ce0-80a4-4a93-aa86-63e3385d2b2a` | 1 份测试资料，22 个分块 |
| `business-course-test` | `447241bf-846a-428e-90c0-0e1fe8d7b331` | 9 门课程＋2 条路线，共 11 份文档 |

服务器上另有旧知识库：

```text
名称：商业顾问
Dataset ID：a05a3c77-eeb7-40c7-a8b2-260c5f4e0da2
```

不要删除或修改旧知识库，除非用户之后明确授权。

当前 Chatflow 发布状态：

- “通用知识检索”节点显示并实际召回 `business-general-test`。
- “课程路线检索”节点显示并实际召回 `business-course-test`。
- 旧知识库“商业顾问”未修改、未删除。

### 2.5 发布后复测与兼容修复（2026-07-23）

发布后真实调用发现并修复了两个 Dify 1.13.3 兼容问题：

1. Chatflow 节点完成事件使用 `node_finished`，服务原先只监听旧名 `workflow_node_finished`。
2. 推荐决策 JSON 位于 LLM 节点的 `outputs.text`；后续普通回答节点曾被误解析为空决策并覆盖正确结果。

修复后结果：

- 本地 `python -m pytest -q tests`：29 项通过。
- 服务器使用 `Dockerfile.offline` 和现有 wheelhouse 重新构建，API、Worker、Scheduler 已重建并运行。
- `/health/ready` 返回数据库、Redis、Dify 应用和知识库均为 `true`。
- 精确课程“AI工具入门”返回 `course_ai_001` 课程卡片和服务端签名跳转地址。
- AI 学习路线返回 `route_ai_basic`、首阶段 `course_ai_001` 和完整审核阶段。
- 公众号学习路线返回 `route_wechat_operation` 和完整审核阶段。
- 停用课程 `course_ai_legacy` 不生成课程卡片。
- Dify 契约复测：商业顾问两轮成功且复用同一 `conversation_id`；三个 Workflow 均为 `succeeded`；通用/课程知识库分别召回 5/4 条。
- 商业会话生命周期复测：消息列表 2 条；点赞、点踩并附反馈原因均返回 200；重命名成功；删除后列表中不可见。
- 停止生成复测：停止接口返回 200，最终 `message_end.status=stopped`，测试会话已删除。
- 课程跳转复测：签名链接返回 302 并跳转到课程事实表中的正式地址；`course_ai_001` 点击统计可查询。
- 40 条黄金问题已执行检索评测：33 条检索用例，聚合 Recall@5 `97.47%`、Hit@5 `100%`、关键问题 Hit@5 `100%`，检索门禁通过。
- 新增 6 条轻量全链路规则评测：课程、AI 路线、公众号路线、停用课程、未知课程、禁止虚构路线全部通过；首 Token P50/P95 为 `4912/5410 ms`，总耗时 P50/P95 为 `8506.5/10303 ms`。该结果是本轮小样本基线，不替代并发压测。
- RAG 剩余人工门禁尚未完成：引用完整率、无依据回答率、错误课程 ID、错误正式链接、虚构路线。
- 本轮创建的 5 个临时业务测试会话已清理。

### 2.4 测试课程与路线

已经通过正式导入 CLI 写入 Dify、AI 管理 PostgreSQL 和 Redis 课程快照。

```text
课程：9
启用且审核通过：8
路线：2
启用且审核通过：2
```

课程 ID：

```text
course_ai_001
course_ai_002
course_ai_003
course_wechat_001
course_wechat_002
course_wechat_003
course_business_001
course_business_002
course_ai_legacy
```

路线 ID：

```text
route_ai_basic
route_wechat_operation
```

`course_ai_legacy` 是停用课程，后续测试必须确认它不会生成课程卡片。

测试数据源：

```text
test_data/courses/测试课程与路线.md
```

## 3. 服务器部署状态

可重复的本地构建、上传、配置、离线重建、测试和回滚流程已整理到
[`docs/本地到线上部署联调实战手册.md`](docs/本地到线上部署联调实战手册.md)。
后续每次发布应同步更新该手册的排坑经验和本文的当前状态。

```text
公网 IP：112.74.37.111
阿里云私网 IP：172.30.157.197
部署目录：/opt/ai-management
```

运行进程：

```text
ai-management-api-1
ai-management-worker-1
ai-management-scheduler-1
ai-management-postgres-1
ai-management-redis-1
```

部署设计：

- AI 管理使用独立 PostgreSQL、Redis 容器和持久卷。
- 不读写 Dify 自身 PostgreSQL 业务表。
- API、Worker、Scheduler 通过 `docker_default` 网络访问 Dify Nginx。
- API 只绑定阿里云私网 `172.30.157.197:8000`，仍需 `X-Internal-Token`。
- 用户身份由小程序后端通过 `X-User-Id` 传入。

健康检查已通过：

```json
{"status":"ok"}
```

```json
{
  "status": "ready",
  "database": true,
  "redis": true,
  "dify_apps_configured": true,
  "dify_knowledge_configured": true,
  "dify_reachable": true
}
```

已经验证：

- 未提供内部 Token 返回 401。
- `/internal/v1/entry-config` 返回三个入口。
- 日常写作经 AI 管理服务 SSE 返回 `status`、`answer_delta`、`message_end`。
- 一次冒烟首 Token 约 913 ms，总耗时约 2136 ms；这不是性能结论。

### 3.1 构建说明

服务器访问 PyPI 很慢，因此增加 `Dockerfile.offline`。生产镜像为：

```text
jx-now-ai-management:0.1.0
```

Streamlit 已从生产核心依赖移到可选 `ui` 依赖，避免生产镜像安装无关大包。

服务器当前保留离线 wheel：

```text
/opt/ai-management/wheelhouse
```

确认不需要再次离线构建后可以清理，但不是当前紧急任务。

### 3.2 极简测试页

Streamlit 极简测试页已构建为独立镜像：

```text
jx-now-ai-management-ui:0.1.0
```

运行容器：

```text
ai-management-test-ui
```

安全边界：

- 服务器仅绑定 `127.0.0.1:8501`，不直接暴露公网。
- 本机通过 SSH 隧道访问 `http://127.0.0.1:8501`。
- 内部 Token 仍由服务器 `.env` 注入测试页容器，不复制到本机文件或浏览器。
- 已修复历史会话下拉框兼容 `conversation_id` 字段。

## 4. 新对话开始后的工作顺序

### 第一步：完成商业顾问绑定并发布（已完成）

进入：

```text
商业顾问 → 编排
```

确认：

```text
通用知识检索 → business-general-test
课程路线检索 → business-course-test
```

完成后发布。不要绑定旧知识库“商业顾问”作为新版测试节点。发布后必须真实调用 API，不能只看控制台状态。

### 第二步：重新执行 Dify 契约测试（主要项已完成）

至少验证：

1. 商业顾问两轮对话和同一个 `conversation_id`。
2. 第二轮普通动态 inputs 实际更新。
3. 停止生成。
4. 点赞、点踩和反馈原因。
5. 删除会话返回 204。
6. 三个 Workflow 返回 `succeeded`。
7. 两个知识库都能独立检索到数据。

可使用：

```text
scripts/dify_contract_check.py
```

脚本带 `--write-knowledge` 时会创建并删除一份临时知识库文档。

### 第三步：课程业务测试（核心场景已完成）

#### 精确课程

```text
请介绍 AI 工具入门课程
```

预期：返回 `course_ai_001`；卡片链接来自课程事实表；正文不自行编造正式链接。

#### AI 路线

```text
我想系统学习 AI 工具，应该从哪里开始？
```

预期：选择 `route_ai_basic`；路线阶段和课程引用来自审核资料。

#### 公众号路线

```text
我想学习公众号运营，有什么学习路线？
```

预期：选择 `route_wechat_operation`。

#### 停用课程

诱导推荐旧版 AI 速成课程。预期：`course_ai_legacy` 不生成课程卡片。

#### 错误候选

诱导模型输出不存在的课程、路线或链接。预期：服务过滤错误 ID，不返回错误卡片或链接。

### 第四步：完整商业会话测试

- 新建、列表、续聊、重命名和删除。
- SSE 断流和停止生成。
- Dify 反馈。
- 同一会话并发返回 409。
- 多用户不串会话、摘要和画像。
- 跨自然日生成新 Dify 会话段。
- 用户画像长期保留。
- 活跃会话删除 7 天前消息但保留摘要。
- 连续 7 天未活动的会话、消息和摘要一起删除。

### 第五步：摘要和画像测试

- 6 个用户轮次触发异步摘要。
- 约 6000 中文字符时按字符阈值触发。
- 主聊天不等待摘要 Workflow。
- 摘要游标之前的原始消息不重复注入。
- 旧摘要任务不能覆盖新摘要。
- 删除历史对话不删除画像。
- 删除完整闲置会话时删除摘要。

### 第六步：RAG 评测

使用：

```text
test_data/rag/golden_questions.jsonl
```

目标：

```text
关键问题 Hit@5 >= 90%
全量 Recall@5 >= 80%
引用完整率 >= 90%
无依据回答率 <= 5%
错误课程 ID = 0
错误正式链接 = 0
虚构路线 = 0
```

资料本身不足时，应调整测试资料或测试集，不得为了过指标让模型编造答案。

### 第七步：批量清洗测试

- 策略列表、`test_markdown` 和现有咨询策略。
- 多文件上传和部分失败。
- 索引完成、分块大于 0、Metadata 成功后才算成功。
- Worker 中断后自动重试一次。
- 失败文件手动重试。
- 所有文件最终进入 `succeeded` 或 `failed`。
- 文件来源可追踪，文件名不直接作为磁盘名或 Redis Key。

正式清洗策略仍需老板提供规则和样本，不能猜测“万能清洗器”。

### 第八步：并发和故障测试

- 20 个并发流式请求，持续 10 分钟。
- Redis、PostgreSQL、Dify 短暂不可用。
- Worker 重启。
- 客户端中途断开。
- 同一会话并发冲突。
- 200 MB 文件流式上传。

不得为了故障测试删除 Dify 正式数据或服务器持久卷。

## 5. 目前不能声称已完成

- 动态 inputs 第二轮更新的运行日志证据。
- 摘要阈值、异步任务和 7 天清理集成测试。
- 批量清洗端到端 Dify 入库测试。
- 40 条 RAG 完整回答人工门禁（检索指标已通过）。
- 20 并发 10 分钟压力测试。
- Redis、PostgreSQL、Dify、Worker 故障演练。
- 小程序后端端到端测试。
- 灰度上线和正式验收报告。

## 6. 防止测试误判

- “已发布”不代表最新草稿已发布，也不代表知识库绑定正确。
- Dify 创建文档成功不等于入库成功；必须索引完成、分块大于 0、Metadata 成功。
- 能回答课程问题不等于推荐正确；还要校验 ID、启用、审核、路线引用和官方链接。
- 点击课程只用于统计，不代表完成学习。
- 一次冒烟耗时不是性能结论。
- Dify 当前仍使用公网 HTTP 地址，正式上线仍应配置域名和 HTTPS。

## 7. 常用位置

```text
本地：D:\work\projectc\jx-now
服务器：/opt/ai-management
```

核心资料：

```text
AI管理服务快速落地与测试计划.md
docs/
dify/dsl/
dify/prompts/
test_data/courses/
test_data/knowledge/
test_data/rag/
test_data/cleaning/
```

## 8. 历史新对话开场指令（已停用）

```text
请先完整阅读：
D:\work\projectc\jx-now\AGENTS.md
D:\work\projectc\jx-now\docs\README.md

根据 docs/README.md 的功能矩阵读取本次任务对应的当前规范、源码和测试。需要线上事实时只读取本交接文档顶部第 0 节；不要把第 1～8 节的历史“下一步”当成当前任务。

先一次性列出会实质影响结果的模糊点和关键假设。每次修改核对并更新受影响文档；未验证事项不得写成已完成。
```

## 9. 文档修改记录

后续可在任务范围内直接修改本文。每次修改完成后，在本节末尾追加记录，至少写明修改原因、修改前和修改后；不得在记录中复制密钥、正文、真实用户 ID 或其他敏感数据。

### 2026-07-30：明确滚动交接文档的直接维护与审计要求

- 修改原因：本文用于向后续协作者交接当前事实，需要允许任务执行者及时更新，同时保留可复核的口径变化。
- 修改前：项目规则允许按运行结果更新当前状态，但没有明确本文可以直接修改，也没有规定在文档结尾记录修改原因和前后差异。
- 修改后：本文可在任务范围内直接修改；每次修改必须在文档结尾追加一条记录，写明修改原因、修改前和修改后。

### 2026-07-30：补充小程序后端私网联调网络配置

- 修改原因：小程序后端地址与 jx 的 Docker 默认网段重叠，AI 服务虽正常但回程路由错误，且 Docker 发布端口缺少精确来源限制。
- 修改前：`172.17.28.83` 的回程路由走 `docker0`；TCP `8000` 没有 `DOCKER-USER` 来源白名单；没有本次网络变更的回滚和业务验证记录。
- 修改后：增加 `/32` 精确私网路由和 TCP `8000` 来源限制，持久化与实际撤销→恢复演练通过；健康、入口、商业 SSE、合成数据清理和无效课程跳转探针通过。小程序后端真实发起测试、云安全组核对和公网 HTTPS 课程跳转仍明确保留为待办。

### 2026-07-31：增加临时公网 HTTP 联调入口

- 修改原因：小程序后端私网暂时不可达，用户确认接受临时 HTTP 公网联调。
- 修改前：AI API 仅通过 `172.30.157.197:8000` 提供私网访问；公网 `80` 仅提供既有 Dify、课程跳转和测试页入口。
- 修改后：新增 Nginx `/ai-service/` 代理到 AI API，使用公网 `80` 端口；公网 `8000` 未开放，私网防火墙规则已恢复；外部健康检查、未授权 `401`、入口配置和商业顾问 SSE 均已验证，合成测试数据已清理。该入口仅为临时 HTTP 方案，正式使用前必须切换 HTTPS 或恢复私网联调。

### 2026-07-31：商业顾问清洗链路 R19b 进度保存（未完成验收）

- 修改原因：用户需要关机，当前“商业顾问清洗链路、课程策略及 jx 全链路验收”停在第二轮真实页面复测与缺陷修复之间；必须保留可继续执行的事实，避免把中间测试误判为通过。
- 修改前：R18 已部署，旧页面测试中的一条百炼 Batch 正在等待；测试清理、第二轮真实页面复测和相关文档同步均未完成。
- 修改后：
  - R19b 已成功部署；健康探针显示数据库、Redis、Dify 配置与 Dify 连通性均正常。首次 R19 发布因发布目录提示词工件名不符合脚本要求而在服务切换前中止，自动回滚后仍为 R18；补齐规范工件名后以同一 wheel 成功部署 R19b。未改 Nginx、未改公网 `/ai-service/` 代理。
  - 发布前备份已完成并验证数据库、Redis、上传卷和安全快照；旧真实页面测试的精确清理清单已验证权限与完整性，已删除 7 个测试批次、7 份 `general` 测试文档及其精确 Redis 键，课程、路线、方向均未删除。正式 `general`、`course` 资料未作为清理目标。
  - 自动化验收：R19 变更前完整隔离回归 121 项通过；R19 后新增的 Dify 索引兼容、页面上传器重置、Batch 进度可观测性、批次死锁修复尚未完成一次合并全量回归，不能把 121 项当作这些最终改动的通过证据。Dify 定向/契约 24 项和课程调用面 5 项通过；页面上传器定向 10 项通过；死锁修复需在恢复后核对最终定向结果并一起全量复跑。
  - 已由真实页面与 jx 日志确认的三个缺陷及本地待发布修复：
    1. 直接发布路径轮询 Dify Batch 索引时，同一合法 batch 首次可查询、随后返回 404，导致整源补偿失败。修复为创建后优先以稳定 document ID 轮询，首次 404 再兼容回退 batch 查询。
    2. 多文件并发完成时，每个 Worker 刷新同批兄弟文件，PostgreSQL 发生行锁死锁。修复为批次级串行汇总，只读取兄弟文件的最终 outcome，不再回写兄弟行。
    3. 测试页成功提交后多文件上传器保留旧选择；切换策略再次提交会累积旧文件。修复为按上传轮次和“知识库+策略”使用动态组件键，提交成功或上下文切换后强制新上传器。
  - 第二轮真实页面复测已开始，但该会话因第 3 项旧缺陷产生文件累积，结果不可作为策略验收：其中 5 个批次已终态、1 个仍处理中，严禁直接改库或宽松删除。修复部署后应先通过正式重试/收敛路径使该会话全部终态，再按精确页面批次清单清理。
  - 下一次继续顺序：先读取本节和项目 `AGENTS.md`；核对三个本地修复与 Batch 死锁测试结果；使用隔离数据库跑完整回归、ruff、编译与索引检查；构建新 wheel、备份、部署；先清理污染页面会话；随后每个策略使用独立新页面会话（先捕获基线）重测，逐项核对清洗结果、Metadata、父子块、Dify 入库和状态；全部通过后精确清理第二轮测试数据，最后才更新 ADR、策略规范、接口索引、计划、排障文档与本状态文档。

### 2026-08-01：R20b 真实页面验收中途保存进度

- 修改原因：用户要求关机前保存当前执行进度；R20b 已部署并完成多套真实页面测试，但公众号 Batch 和课程策略尚未完成，必须保留可继续执行的运行状态和未清理范围。
- 修改前：交接文档最新记录停留在 R19b，未记录 R20b 的模型任务状态修复、发布事实、真实页面测试结果及当前服务端 Batch 状态。
- 修改后：新增第 0.4 节，记录 R20b 健康与迁移结果、本地回归口径、个人咨询/知识星球/资料钩子/书籍/小报童/公众号的真实页面结果；明确公众号百炼 Batch 已创建且仍由 jx 服务端轮询，未取消、未清理；明确课程 `course_route_catalog` 测试、最终精确清理、正式知识库复核和相关文档同步仍为待办，并给出下次继续顺序。

### 2026-08-01：R20c 修复、课程复测与最终精确清理

- 修改原因：完成 R20b 中断后的课程策略、模型契约、全策略真实页面复测、jx 健康复核和测试数据清理，并把中途快照改为可继续使用的当前事实。
- 修改前：模型返回标量字段列表时课程候选进入契约复核；公众号和课程批次、测试用户、任务结果键及少量残余测试文档尚未统一清理；0.4 节仍把公众号 Batch 和课程测试写成进行中。
- 修改后：R20c 增加标量字段列表的严格契约归一化并保留 URL/链接拒绝；课程策略完成审核表导入、5 份文档发布和父子块/Metadata/隐私复核；公众号、咨询、课程及其它本轮测试数据均按精确归属清理；正式课程、路线和正式 Dify 文档保持不变；新增 0.5 节说明剩余生产门禁，不再把分组回归或测试页验收等同于生产规模验收。

### 2026-08-01：20 并发流式压测与 Dify 数据边界确认

- 修改原因：完成计划中的 20 并发、10 分钟在线问答压测，并记录用户要求的长时间运行日志检查和清理边界。
- 修改前：0.5 节仍把 20 并发压测列为未完成；压测用户尚未清理，Dify 会话删除队列边界未记录。
- 修改后：400 次请求全部成功，首事件 P95 约 503ms、总耗时均值约 17.9s；压测用户的本地画像/会话/消息/分段已按官方接口清零；jx 日志和健康检查无服务错误。官方接口留下的本地 `external_deletion_task` 队列未执行，因此没有主动删除 Dify 产生的会话数据；生产故障注入、小程序真实端到端、40 条人工语义门禁、灰度和 HTTPS 仍保持待办。

### 2026-08-01：隔离故障回归与外部门禁核验

- 修改原因：继续执行交接文档中的剩余门禁，先完成不触碰正式 Dify 数据的故障/补偿回归，并核对真实小程序主机、人工评审和 HTTPS 的必要外部条件。
- 修改前：剩余门禁只区分为“未完成”，没有记录隔离回归证据和外部依赖的具体缺口。
- 修改后：本地隔离故障、补偿、精确清理和用户数据边界测试 `20/20` 通过；未执行生产破坏性演练。`jx-backend` SSH 别名无法解析，无法伪造小程序真实端到端；人工语义评审缺少样本/标准/评审人；HTTPS 缺少正式域名、证书和切换窗口。上述项目继续保持未完成。

### 2026-08-01：增加快速代码测试部署当前事实

- 修改原因：用户要求固化“压缩 `ai_management`、上传固定 ZIP、运行脚本、直接测试页面”的流程，并已授权在 jx 使用现有 ZIP 完成部署和测试。
- 修改前：当前最新状态仍为 R20c；没有固定 ZIP 快速部署脚本、可选全测试环境清理、服务器重打 wheel、两版本镜像保留和本次浏览器验证事实。
- 修改后：新增 0.8 节，记录 `quick-20260801-230240-513925f01cf9`、`--keep-data` 数据边界、7 服务/健康/日志/迁移/浏览器结果、备份和报告位置、旧项目镜像精确清理，以及已观察到的 Streamlit 匿名遥测注意事项；0.7 改为历史快照。

### 2026-08-02：用当前源码 ZIP 复验快速部署并简化手册

- 修改原因：用户确认保留单脚本、以服务器分步操作为主、日常默认保留数据，并要求把脚本放到 jx 后真实验证压缩包修改代码和测试页。
- 修改前：0.8 只记录前一份用户 ZIP 的首次快速部署；手册仍把首次脚本安装放在日常上传前，容易误解为需要上传整个 `scripts` 目录；新增 `ai_management.ops` 尚未通过新 ZIP 到达运行镜像的哈希证据验证。
- 修改后：新增 0.9，记录当前源码/ZIP/jx 三方指纹、单脚本安装、`--keep-data` 成功发布、7 服务/数据/迁移/日志/浏览器结果和 `quick_reset.py` 本地/容器哈希一致；手册改为日常服务器步骤优先，明确脚本只需安装一次且不能覆盖整个服务器 `scripts` 目录；0.8 改为历史快照。

### 2026-08-02：实施清洗链路修复并完成完整 wheel 发布

- 修改原因：用户确认实施容量、去重删除、原名发布、PDF/DOCX 转换、课程结构审核和异步收敛计划；需要同步当前线上事实并保留未完成的人审门禁。
- 修改前：0.9 线上仍是快速 ZIP 版本，课程批次因 XLSX 过期 dimension 失败，公众号存在发布分片失败/待补偿，小报童存在正文为空和等待模型状态；没有同名删除墓碑、源文件级删除入口或结构审核阶段。
- 修改后：本地新增容量/磁盘/去重/墓碑/文件命名/转换临时产物/模型看门狗与课程两阶段审核；新增 Alembic `i5f6a7b8c9d0` 并以完整 wheel 发布到 jx。课程重试进入 110 行结构审核表；公众号精确重试后 29 个分片成功；小报童正文为空项停止自动重试，等待模型项已重新规划。同步更新清洗排障与接口索引；正式课程和正式知识库未执行模糊删除。

### 2026-08-02：发布文件级错误收敛修复并完成线上状态复核

- 修改原因：公众号分片全部成功后文件聚合仍残留 `SOURCE_ATOMIC_ROLLBACK`，需要让终态聚合清除陈旧文件级错误，并在 jx 重建所有 Worker。
- 修改前：首版修复已发布，但公众号文件显示 `mixed/completed` 仍带旧错误码；模型任务和课程结构审核状态需要在第二次完整 wheel 发布后复核。
- 修改后：发布 `20260802-cleaning-fix2-d1f064963f58`；ready、迁移头、双包导入和 7 服务一致镜像均通过。公众号文件现为 `mixed/completed` 且文件级错误为空，29 个分片均为 `succeeded`；小报童模型任务完成（17 候选，12 成功、2 排除、3 待复核），正文为空文件保持失败并停止自动重试；课程 110 条目仍全部处于结构审核、模型任务数为 0。

### 2026-08-02：补充并发准入锁后的最终发布

- 修改原因：关闭多个 API 并发创建清洗批次时容量检查的 PostgreSQL 间隙竞争，确保 5 个活动批次上限不会被并发请求突破。
- 修改前：文件级终态错误已收敛，但批次准入只锁活动行，理论上两个并发请求可同时通过同一容量检查。
- 修改后：加入 PostgreSQL `ai-cleaning-admission` 事务 advisory lock，发布 `20260802-cleaning-fix3-9156e562f85c`；ready、Alembic head、双包导入、7 服务镜像和近期严重日志复核通过。现有测试批次未重新触发写入。

### 2026-08-02：同步结构审核页面文案并完成最终 UI 发布

- 修改原因：结构审核与语义审核共用导入按钮时，页面文案会误导管理员以为结构表导入后立即发布。
- 修改前：课程审核页统一显示“导入审核表并发布”。
- 修改后：结构阶段显示“导入结构审核表并进入模型候选”，语义阶段显示“导入语义审核表并发布”；发布 `20260802-cleaning-fix4-b1c85cb503a2`，ready、Alembic head、7 服务和近期日志复核通过。

### 2026-08-02：同步模型分片的本地 Dify 名称投影并完成修复发布

- 修改原因：远端 Dify 分片已经按“原文件名+序号”命名，但历史分片的本地 `CleaningDocument.dify_document_name` 仍保留旧的内部 hash 名；这会在 Dify 响应缺少名称时造成管理端投影不一致。
- 修改前：模型分片发布函数只更新远端 ID，没有把实际创建的 Dify 名称回写到分片内的本地条目。
- 修改后：发布函数缓存并回写实际远端名称；发布 `20260802-cleaning-fix5-e924d99fd6c0`，发布前完成备份，ready、Alembic head、7 服务、双包导入和近期严重日志复核通过；对当前 3 个成功分片的 42 个条目做了精确本地名称回填，未删除任何正式知识库文档。

### 2026-08-02：转换前置 Markdown 改为可配置持久化并发布

- 修改原因：原实现只在 Worker 临时层保存，服务重启即丢失，并把测试保存能力与 `APP_ENV=production` 硬绑定，不能满足“测试环境保存、正式环境关闭并删除”的运维要求。
- 修改前：jx 无可从宿主机稳定查看的共享 Markdown 目录；开启配置可能被 production 校验拒绝；没有保留期、关闭清理、共享挂载或发布脚本目录权限保障。
- 修改后：新增独立开关、专用共享目录、安全命名与原子写入、7 天保留、API 启动/夜间清理和正式发布默认关闭；发布 `20260802-conversion-md-be372c68d875`，jx 当前按测试用途开启。API/Worker 配置、挂载、权限、ready、日志和发布报告均已核验；合成 DOCX 实测生成成功后已精确删除，未写数据库或 Dify。发布中发现并修复 bind mount 属主问题，脚本现从镜像派生运行 UID/GID。

### 2026-08-02：Batch 开关、实时课程回归与源删除聚合修复

- 修改原因：Batch 供应商返回慢影响测试；实时模式需要独立价格快照；真实课程批次还暴露了同源失败后文件聚合停在 processing、源删除后批次聚合未收敛，以及路线模型上下文过少的问题。
- 修改前：新任务按候选规模可能进入 Batch；实时任务沿用 Batch 价格字段；课程同源原子失败只更新文档，文件/批次可能残留处理中；路线模型只看到路线名和空目标。
- 修改后：新增 `BAILIAN_BATCH_ENABLED`（默认 true，jx 测试设 false），实时调用使用独立价格快照并保留现有 RPM/TPM 限制；原子失败后刷新所有文件，精确源删除同步收敛批次；路线请求增加确定性阶段/课程上下文，空语义字段仅允许来源字段兜底，不允许猜测。发布版本为 `20260802-course-catalog-context-fix-db4f37c71fd8`，备份和报告见顶部 0.14。

### 2026-08-02：同步本轮实现的权威文档口径

- 修改原因：代码和 jx 真实回归已完成，但课程规格、管理页手册、RAG 计划和验收指南仍有“默认人工审核”“重复文件可能跳过”“仅 general 知识库”等过期表述，容易导致后续测试沿错误路径执行。
- 修改前：Batch 开关、课程自动门禁、精确源删除、转换 Markdown 服务器诊断产物和实时独立价格快照主要只记录在当前状态与排障文档，产品决策和操作手册未完全对齐。
- 修改后：同步更新 `docs/V2决策记录.md`（ADR-022～025）、课程目录规格、清洗接入说明、AI 管理页手册、RAG 全链路计划、V2 数据设计、测试验收指南、README 与功能索引；明确 `BAILIAN_BATCH_ENABLED` 只影响新任务、`COURSE_CATALOG_AUTO_PUBLISH` 的自动/人工回退边界、同名/同内容必须精确删除后重传，以及 `SAVE_CONVERSION_MARKDOWN` 仅供测试服务器查看。未修改冻结接口文档，未改变运行代码、线上数据或部署版本。

### 2026-08-03：同步商业顾问 Dify 应用访问 Key

- 修改原因：用户要求把 jx 上商业顾问 DSL 的访问 API 切换到新应用访问 Key。
- 修改前：AI 服务使用旧的 `DIFY_BUSINESS_APP_API_KEY`；服务器 `.env` 尚未包含本次指定映射。
- 修改后：服务器 `.env` 已在备份后精确替换该键，并重建 `api`、`worker`、`scheduler`；容器配置哈希、Dify `/parameters`、AI 服务 `ready` 和服务运行状态均通过验证。未记录 Key 明文，未修改 DSL、数据库或知识库。

### 2026-08-03：按最新指令再次同步商业顾问应用访问 Key

- 修改原因：用户先给出另一候选 Key，随后在执行前明确改回上一轮指定值；应以最后一条指令为准。
- 修改前：jx 上的当前配置与最新目标哈希不一致；被撤回的候选 Key 尚未由本轮写入。
- 修改后：备份 `.env` 后仅替换 `DIFY_BUSINESS_APP_API_KEY`，重建 `api`、`worker`、`scheduler`；容器配置哈希、Dify `/parameters`、AI 服务 `ready` 和运行状态验证通过。Key 明文仍未进入仓库或报告。

### 2026-08-03：切换商业顾问 Dify 应用访问 Key

- 修改原因：用户明确要求把 jx 上商业顾问 DSL 的访问 API 切换到另一应用访问 Key。
- 修改前：服务器 `DIFY_BUSINESS_APP_API_KEY` 与本次目标哈希不一致。
- 修改后：备份 `.env` 后精确替换该键并重建 `api`、`worker`、`scheduler`；容器配置哈希、Dify `/parameters`、AI 服务 `ready` 和服务运行状态验证通过，未触发回滚。Key 明文未进入仓库或报告，DSL、数据库和知识库未修改。

### 2026-08-03：再次轮换商业顾问 Dify 应用访问 Key

- 修改原因：用户提供新的商业顾问 Dify 应用访问 Key，要求再次同步 jx 配置。
- 修改前：AI 服务仍使用当天上一轮已验证的商业顾问应用访问 Key。
- 修改后：服务器 `.env` 已备份至 `/opt/ai-management/backups/20260803-022555-business-app-key-sync` 后精确替换，并重建 `api`、`worker`、`scheduler`；容器配置哈希匹配目标，Dify `/parameters` 返回 HTTP `200`，AI 服务 `ready` 和三个服务运行状态通过。未记录 Key 明文，未修改 DSL、数据库、Redis 或知识库。

### 2026-08-03：统一改造 fix8 发布、六策略页面回归与接口覆盖

- 修改原因：按统一执行计划完成 fix8 红线修复、jx 发布、六种当前清洗策略全链路、修改功能页面回归和接口文档覆盖，并把未闭环问题集中记录。
- 修改前：课程/路线推荐在中文相邻位置仍可能展示内部标识；页面和接口回归证据分散，公众号首轮样本编码混用，课程目录和个人咨询状态尚未按最新计划归类；功能索引仍有自动区过期提示。
- 修改后：fix8 已发布并经备份、wheel 校验、迁移 head、live/ready 和服务一致性核验；内部标识正则改为 ASCII 邻接边界并补回归测试；公众号改用完整 GB18030 样本复测通过；六策略、修改功能及管理/日志/诊断页面结果写入脱敏 `var/reports/20260803-unified-fix7-cleaning-ui/issues.jsonl`；功能索引已生成并通过 `--check`。个人咨询模型契约、课程目录业务复核和候选 DSL 发布权限仍保持开放阻塞，未绕过安全门禁。

### 2026-08-03：新增强制清理模式并完成 jx 本地状态归零

- 修改原因：用户确认全部当前清洗记录为测试数据，要求清除仍为 `processing/pending` 的本地批次，同时禁止删除 Dify 知识库内容。
- 修改前：现有 `execute` 仅允许所有本地清洗层进入终态；活动批次会安全拒绝，无法满足测试前强制归零。
- 修改后：现有脚本新增独立 `force` 模式和独立确认词；先停本地写入服务并备份，拒绝活动模型供应商任务，核对前后目标清单一致后删除本地清洗状态、精确 Redis 键和批次 UUID 上传目录；Dify 删除操作固定为 `0`。本地 `18/18` 专项测试和 Bash 语法检查通过，jx 实际强制归零、备份恢复、API ready、测试页直连/Nginx `200` 全部通过。Dify 知识库只读回读为 `general=6`、`course=110`，未执行知识库删除。

### 2026-08-03：准备 OCR 离线运行时并记录远端传输阻塞

- 修改原因：用户确认在 jx 测试服务器启用扫描 PDF OCR，要求不修改业务代码、按需占用 OCR 资源，并把配置和后续操作写入项目文档。
- 修改前：OCR 依赖、模型和专用 Worker 镜像不存在；旧 OCR Worker 由普通核心镜像常驻运行；没有按队列自动启停和 systemd 失败退避。
- 修改后：本地新增离线 OCR 镜像/模型校验、Compose 专用镜像、systemd timer、按需脚本、发布保护和文档/测试映射；旧 jx OCR Worker 在队列为 0 时正常停止。由于大包传输后 jx SSH/HTTP 应用层不返回 banner，远端新镜像、`.env` OCR 开关、systemd 安装和真实验收仍未完成；不得据此宣称部署成功。
- 证据：本地相关测试与索引检查结果、`var/reports/ocr-deployment-20260803/issues.jsonl`；远端继续操作前须先恢复 SSH 并核验 `var/ocr-runtime` 完整性。

### 2026-08-03：确认 OCR 传输引发主机资源阻塞并暂停后续部署

- 修改原因：用户要求先定位根因并配置防护，明确暂停 OCR worker、镜像构建、timer 和大包传输，不能影响 Dify/ai-management 数据与核心服务。
- 修改前：上一启动周期曾出现 MemAvailable 约 285 MiB、8 GiB Swap 全满、97% I/O wait、D 状态进程和 Docker 健康检查超时；传输/构建流程缺少组合资源门禁，Docker 构建上下文还会扫描重复归档和分片。
- 修改后：确认根因机制为内存压力→Swap 换入换出→磁盘 I/O 饥饿→Docker/Dify/ai-management/SSH 超时；OCR 保持停用，远端只清理已确认属于本次失败传输的不完整 `/opt/ai-management/var/ocr-runtime`，安装资源门禁脚本并通过恢复后检查；新增 Dockerfile 专用 ignore、部署互斥锁、资源门禁、低优先级构建和 OCR 默认暂停。未清理 Docker build cache，未重启/重建 Dify 或 ai-management 核心服务，未修改数据库、Redis、上传卷或持久卷。
- 证据：`var/reports/ocr-deployment-20260803/issues.jsonl`；远端恢复后门禁结果为 MemAvailable 约 26.52 GiB、Swap 0 MiB、I/O wait 0%、D 状态进程 0；Dify API healthcheck 的独立超时问题保留为开放项，未在本次任务中处理。

### 2026-08-03：把资源门禁接入当前 jx 发布路径

- 修改原因：已确认资源阻塞根因后，需要防止现有远端快速发布和正式发布脚本绕过资源检查；远端脚本与本地版本存在差异，不能直接整体覆盖。
- 修改前：远端发布流程缺少统一的发布互斥锁和构建前资源门禁，普通发布/回滚服务列表仍包含旧 `ai-ocr-worker`；直接传输或构建可能再次压满主机资源。
- 修改后：先将远端现有运维脚本备份到 `/opt/ai-management/backups/20260803-resource-guard-before`，再以最小补丁接入共用锁、资源门禁和核心/UI 构建前检查，并从普通发布/回滚列表移除旧 OCR worker；远端脚本语法检查通过。OCR 仍暂停，未启动、重建或安装任何 OCR 服务，也未重启 Dify/ai-management 核心服务。
- 证据：远端资源门禁最终检查为 `MemAvailable=26.31 GiB`、`Swap used=0 MiB`、`I/O wait=0%`、`D-state=0`、`status=pass`；故障细节见脱敏报告 `var/reports/ocr-deployment-20260803/issues.jsonl`。

### 2026-08-03：修复 jx 测试环境 Dify 页面请求 pending

- 修改原因：用户反馈在 jx 测试服务器使用 Dify 页面时请求一直挂起；排查需区分页面入口、Dify API、Nginx、容器 DNS 和 jx 测试页链路。
- 修改前：`api` DNS 同时包含 jx `ai-management-api-1` 与 Dify API；Nginx 日志出现请求连接到错误的 `172.18.0.8:5001`，Dify `docker-api-1` `/health` 超过 3 秒无响应且健康检查进程持续堆积；jx `test-ui` 未运行，公网测试页为 502。
- 修改后：远端完成可回滚备份；Dify API 使用 `dify-api` 专用别名，Nginx 与 Dify 内部 URL 切换到该别名，healthcheck 增加客户端超时；重建 Dify API/Worker/插件服务并 reload Nginx；jx `test-ui` 恢复，内部 API 地址切换到 `ai-management-api:8000`。连续 HTTP smoke 已通过；未修改 Dify 数据或执行删除。

### 2026-08-03：补充 Dify 登录态对话与 SSE 回归证据

- 修改原因：基础 HTTP smoke 通过后，需要补齐用户实际使用页面的登录态最小对话和流式响应证据。
- 修改前：交接记录仅有 Dify 控制台接口和 jx 测试页 HTTP 结果，完整浏览器对话/SSE 尚未记录。
- 修改后：在现有登录态 Dify Chatflow 预览页发送合成测试问题；工作流 draft HTTP 200，`advanced-chat/workflows/draft/run` HTTP 200 且 MIME 为 `text/event-stream`，`Network.loadingFinished` 为 41765 bytes，输入框清空、发送按钮恢复、无 Console warning/error；随后远端最近 10 分钟 Nginx 未发现上游连接失败或 502/503/504。未保留对话正文或凭据。

### 2026-08-04：补充六类指定样本本地策略矩阵

- 修改原因：执行计划要求使用用户指定的六类样本完成数据清洗验证，需把最终脱敏统计纳入交接证据。
- 修改前：顶部仅记录个人商业咨询三文件的测试页回归，未记录其余五类样本的本地策略转换结果。
- 修改后：六类共 12 个样本文件均进入预期策略路径，生成 142 个候选文档且无转换异常；仅保留策略状态统计，不写入样本正文、文件名或受保护映射。详细脱敏结果见 `var/reports/20260804-pdf-rapidocr-docker/sample-matrix.json`。

### 2026-08-04：补充本地工作流测试前置边界

- 修改原因：最终验收需区分已通过的定向回归与未能启动的完整模型工作流测试，避免把环境缺失误报为产品通过。
- 修改前：交接只记录样本策略矩阵和远端测试页终态，未在顶部明确完整工作流测试的本地 Redis 前置缺失。
- 修改后：明确记录定向回归通过；完整工作流测试仅因固定测试夹具依赖 `127.0.0.1:6399` 且本机无 Redis 而阻塞，远端 jx Redis/Celery 和模型验收独立通过。问题证据仍在 `var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`，未写入凭据或敏感内容。

### 2026-08-04：补充 Dify Code 节点 PDF→Markdown 自测闭环

- 修改原因：用户要求明确本次 Workflow 验证的目标、已完成项、未完成项和文档影响，并在其自测期间保持 Sandbox 中的 PyMuPDF/RapidOCR。
- 修改前：交接顶部已有远端自测配置和回滚点，但缺少与项目文档、排障记录和测试门禁对应的闭环摘要。
- 修改后：补充独立 DSL、Sandbox 依赖、文本/扫描真实执行证据、当前自测窗口和生产边界；新增 Dify ONNX/seccomp 排障条目及脱敏 issue 记录；确认未修改 `knowledge_cleaner`、jx API/Worker、数据库、Redis、正式 Dify 应用或知识库。当前远端仍按用户要求保持自测配置，结束后必须按备份恢复。

### 2026-08-04：修复 PDF Workflow 页数分支与 RapidOCR stderr 误判

- 修改原因：用户导入测试 DSL 后反馈文本型 PDF 被错误套用 OCR 5 页限制，扫描型 PDF 的 RapidOCR 日志被 Dify Code 节点报为运行异常。
- 修改前：Workflow 先按 `max_pages=5` 检查所有 PDF；RapidOCR/ONNX Runtime 的原生 stderr 未处理，Sandbox 将有效 stdout 结果和日志合并为 RuntimeError。
- 修改后：文本层探测提前到页数限制之前，文本型 PDF 不受 OCR 页数限制；纯图片 PDF 默认 `max_pages=50`（允许 1-200）；OCR 初始化和识别期间 fd 2 临时重定向到 `os.devnull`，恢复后正常输出结构化 Markdown 结果。未改生产清洗代码、Dify 业务数据库或正式应用。
- 验证：远端 Worker 日志已确认原请求 stdout 为 `status=succeeded/backend=rapidocr`、error 仅为 RapidOCR/ONNX Runtime 日志；修复后 DSL 代码编译、`git diff --check`、功能索引检查和 `test_ai_management.py -k dsl_and_gold_data_are_complete` 通过。用户需重新导入/发布该 DSL 后再用两个样本手测。

### 2026-08-04：修复受限 Sandbox 日志 sink 并完成指定扫描样本验证

- 修改原因：用户反馈 `/dev/null` 不存在；改用临时文件后又因 Sandbox 没有可用临时目录失败。
- 修改前：stderr 静默依赖文件系统设备或临时目录，均不适用于当前受限 Code 子进程。
- 修改后：改用 `os.pipe()` 与后台 drain 线程吸收 OCR 原生 stderr，同时关闭 RapidOCR Python logger、提高 ONNX 日志级别；OCR 完成后恢复 fd 2。临时放行的 18080 测试端口、HTTP 服务和样本文件已精确恢复/清理，当前 SSRF 仍只放行 `112.74.37.111:80`。
- 验证：使用用户指定扫描 PDF 完整执行返回 `code=0`、`error` 为空、`status=succeeded`、`backend=rapidocr`、3 页、平均置信度 `0.9969`；Sandbox 镜像和 PyMuPDF/RapidOCR/ONNX Runtime 保持 healthy。用户导入的旧 Workflow 版本仍需重新导入修复后的 YAML 才会生效。

### 2026-08-04：补充 PDF Workflow 排障知识与防踩坑清单

- 修改原因：用户要求解释 DSL 第 227 行的变更，并把本轮页数判断、seccomp、stderr 误判、`/dev/null`/临时目录缺失和 Dify 版本同步问题沉淀为可复用记录。
- 修改前：排障根因已分散在 DIFY-006～DIFY-008，导入操作文档未提供一张完整的 PDF Workflow 踩坑对照表。
- 修改后：在 `docs/troubleshooting/dify.md` 保留稳定根因条目，在 Dify 导入文档新增 3.2 对照表，在 DSL 技巧备忘录补充受限文件系统和重新导入门禁；未修改业务代码、生产清洗链路或 Dify 数据。
- 验证：文档内容与最终 DSL 的 `os.pipe()` 实现、指定扫描样本 `status=succeeded/backend=rapidocr` 证据一致；未写入密钥、样本正文或真实受保护标识。

### 2026-08-04：新增服务器本地 PDF→Markdown 版式微调脚本并完成样本验证

- 修改原因：用户希望在 jx 服务器直接调节 PDF 转 Markdown 的标题层级、段落、缩进、换行和表格样式，且不新增服务、不改生产清洗主链路。
- 修改前：只有生产 `knowledge_cleaner` 转换路径和独立 Dify Workflow 验证器，没有一个可在服务器现有 OCR 镜像中反复调参的独立脚本。
- 修改后：新增 `scripts/pdf_markdown_refine.py`；前两页文字探测达到 20 个非空白字符时走 `pdfplumber`，否则走现有离线 OCR 镜像中的 `RapidOCR + pypdfium2`。默认 OCR DPI 为 200，支持单文件/目录批量、独立输出目录、`--force` 覆盖和 JSON 诊断；输出文件权限为 `600`。图片表格只保证有效文字和阅读顺序，复杂列结构会线性化并告警，不宣称像素级还原。
- 服务器测试：用户样本位于 `/opt/ai-management/tmp/pdf/`；脚本放在 `/opt/ai-management/tmp/pdf-markdown-refine/`，通过一次性 `jx-now-ai-management-ocr:20260804-rapidocr-semantic-78f9a1` 容器运行。3 份样本全部成功：文字型 1 份走 `pdfplumber`（8 页、2 个表格），图片型 2 份走 `RapidOCR`（3 页和 4 页，平均置信度约 0.996～0.998）。输出在 `/opt/ai-management/tmp/pdf-markdown-refine/output/`。
- 期间修复：首次远端运行发现 RapidOCR NumPy 数组不能使用 `or []`，以及 Python `statistics.median()` 不支持 `default` 参数；同时将列表缩进改为按坐标计算，并避免普通数字列表被误判为标题。已修复并从头重跑，最终三份均为 `succeeded`。未修改 OCR Worker、Compose、Dify、数据库、Redis 或生产 PDF 转换代码。
- 文档同步：版式微调脚本的用途、命令、参数、结果和图片表格边界已写入《文字型 PDF 转 Markdown 评审与落地建议》，并登记到 `docs/功能与接口索引.md`；本记录不保存样本正文、密钥或受保护映射。

### 2026-08-04：覆盖重跑服务器 PDF→Markdown 样本并收集空文件错误

- 修改原因：用户要求使用已放入 `/opt/ai-management/tmp/pdf/` 的全部样本覆盖原 Markdown 输出，并先收集错误，暂不统一处理。
- 修改后：使用现有 OCR 镜像和 `scripts/pdf_markdown_refine.py --force` 批量重跑；5 个输入中 4 个成功、1 个失败，成功结果已覆盖写入 `/opt/ai-management/tmp/pdf-markdown-refine/output/`。本次没有删除或替换任何输入文件，也没有修改服务配置。
- 已验证错误：失败项对应输入文件大小为 0 字节、类型为 `empty`，因此 `pdfplumber` 报 `No /Root object! - Is this really a PDF?`；根因是空输入文件，不是 OCR 或转换脚本异常。待用户确认重新上传后再处理。
- 证据：远端日志目录 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260804-112801/`；本地脱敏记录 `var/reports/20260804-pdf-markdown-refine/issues.jsonl`。日志保留 stdout、stderr 和退出码，未保存正文到仓库。

### 2026-08-04：重新覆盖重跑服务器 PDF→Markdown 样本并闭环空文件问题

- 修改原因：用户重新上传/替换空输入后要求再次测试。
- 修改后：复核服务器 PDF 目录共 5 个文件、空文件数为 0；再次使用现有 OCR 镜像和 `--force` 全量重跑，退出码为 `0`，5/5 成功、0 失败，覆盖输出 5 个 Markdown。3 个文件走 `pdfplumber`，2 个文件走 `RapidOCR`，合计 263 页、文字型表格 60 个；图片型表格仍按既定规则线性化并告警。
- 错误闭环：上一次的 0 字节输入已由用户替换，原 `PDF-MD-001` 标记为 `resolved`；本次没有修改脚本、服务配置或删除输入文件。
- 证据：本次日志目录 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260804-113347/` 和复测目录 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260804-113713/`；脱敏问题记录 `var/reports/20260804-pdf-markdown-refine/issues.jsonl` 已更新。

### 2026-08-04：修复 PDF→Markdown 版式问题并在 jx 完成最终重跑

- 修改原因：用户反馈服务器脚本存在跨页表格未合并、长文标题层级/目录失真、RapidOCR 页脚残留和未按原段落换行等问题，要求参考官方/GitHub 方案后修复并重新生成 Markdown。
- 修改前：`pdfplumber` 表格只按页输出；目录点线内容可能被合成一段；标题仅按固定阈值判断；OCR 只按单页输出，未做底部页脚归一化和未结束句续行。临时启用 `text/text` 表格策略还会把普通双栏正文误识别成表格。
- 修改后：`scripts/pdf_markdown_refine.py` 增加相邻页表格状态机（首列空续接、首列有值新行、重复表头去重）、文档相对字号/粗体标题映射、点线目录拆分、底部页脚过滤和 OCR 段落续行；默认仅使用线/矩形几何表格策略，保留无边框复杂表格为已知限制。JX 旧脚本先备份到 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260804-pre-layout-fix/` 和 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260804-pre-ocr-paragraph-fix/`，再同步新脚本。
- 最终验证：使用现有 `jx-now-ai-management-ocr:20260804-rapidocr-semantic-78f9a1` 一次性容器、`--ocr-dpi 200 --force` 覆盖输出；输入 5 个且空文件数为 0，退出码 `0`，5/5 成功（3 个 `pdfplumber`、2 个 `RapidOCR`），合计 263 页。输出 5 个均非空、权限 `600`；目录样本 25 个普通目录项，跨页几何表格 1 个连续表格块；代码围栏、`None`、OCR 页脚模式均为 0。复测确认此前句内断行已合并。
- 官方依据与限制：`pdfplumber` 官方按页返回表格且不提供跨页/正文阅读顺序自动合并；RapidOCR 官方只提供行框、文本和置信度，段落/标题/页脚均由本脚本启发式恢复。无边框复杂表格、多栏版式和 OCR 表格结构仍需人工复核。
- 证据：最终日志 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260804-234210/`；脱敏问题记录 `var/reports/20260804-pdf-markdown-refine/issues.jsonl` 的 `PDF-MD-002`、`PDF-MD-003`。本次未修改输入 PDF、OCR Worker、Compose、Dify、数据库、Redis 或生产清洗链路。

### 2026-08-05：按通用结构规则平铺目录并覆盖重跑

- 修改原因：用户确认目录只用于匹配正文标题，要求保留原始目录标题、统一一层、删除子目录和页码；并强调样本只是示例，不能固定写死条目。
- 修改前：目录输出仍可能保留页码，编号标点被统一改写；当 PDF 把多行目录拆分或拼接时，子目录可能混入一级目录，长目录候选数还可能少一项。
- 修改后：`scripts/pdf_markdown_refine.py` 保留原始编号/标点/标题，使用编号层级、子项标记、目录行 `x0`/缩进和同级序列筛选顶层，移除点线与页码，统一输出无嵌套 `- 原始编号 标题`；同时保留 RapidOCR 页脚、段落和字段断行规则。该逻辑不依赖样本文件名或固定标题。
- 服务器动作：旧脚本先备份到 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260805-pre-flat-toc/`、`/opt/ai-management/tmp/pdf-markdown-refine/backups/20260805-pre-toc-count-fix/` 和 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260805-pre-generic-fixture-comments/`，再同步脚本；使用现有 `jx-now-ai-management-ocr:20260804-rapidocr-semantic-78f9a1`、`--ocr-dpi 200 --force` 覆盖输出。未修改输入 PDF、OCR Worker、Compose、Dify、数据库、Redis 或生产清洗链路。
- 验证：日志 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260805-010112/`，退出码 `0`，5/5 成功（3 个 `pdfplumber`、2 个 `RapidOCR`），共 263 页，输出 5 个非空 Markdown 且权限均为 `600`；长目录 25 个一级条目、0 个嵌套条目、0 个页码；代码围栏、`None`、OCR 页脚和错误标记均为 0。本地直接调用 6 个回归函数全部通过，回归夹具已改为泛化占位内容；本机未安装 `pytest`，未将其缺失误报为失败。当前服务器脚本 SHA-256：`974892b84eb65b928fa5ab1256aca301e84fe4e8e87f254f3818099af67fcc05`。
- 已知边界：同编号、同标点且无缩进的父子目录在 PDF 信息中不可唯一判定；无边框复杂表格、多栏阅读顺序和 OCR 表格列结构仍需人工复核。

### 2026-08-05：商业顾问分段流式发布、问题修复与文档同步

- 修改原因：用户要求把 Chatflow 中先返回的即时提示与最终答案分开展示，并部署到 jx 测试页完成真实验证。
- 修改前：本地适配层只按 `message.answer` 追加 delta；Dify 1.13.3 的 `progress_answer` 固定 Answer 节点实际通过 `node_finished.data.outputs.answer` 返回，因此线上只能发阶段边界，不能发即时提示正文。快速部署脚本还会把 UI 冷启动 502 判为失败，并在无 systemd 时漏掉 OCR Worker 恢复。
- 修改后：`ai_management/conversations.py` 将 `progress_answer` 输出转为现有 `answer_delta`，以 `progress_answer_started`/`final_answer_started` 切换阶段；测试页使用“即时提示/最终回答”两个占位区。快速部署脚本增加 60 秒测试页重试，并使用完整 Compose、独立于 systemd 的 OCR Worker 状态恢复。同步更新 Dify、SSE、部署排障和接口说明。
- 验证：本地定向测试 11 项通过；jx 最终发布 `quick-20260805-020249-034af4811733`，API ready、测试页 HTTP 200、Core/UI/OCR 同源且 OCR Worker running；真实合成 SSE 为 1 个即时提示 delta + 2 个最终答案 delta，无错误；公网浏览器测试页出现两个独立展示区并完成请求。
- 清理：3 个合成用户会话及关联 Dify 会话按精确 ID删除，未清理正式数据；失败发布报告和最终报告保留在服务器固定报告目录。未提交或推送 Git。

### 2026-08-05：修复 PDF 标题误判、目录层级与段落换行

- 修改原因：用户反馈加粗正文被错误输出为标题、长文跨行标题被拆成两个层级，以及连接内容被错误拆段；要求继续使用通用规律，不按样本文档写死。
- 修改前：粗体本身可能触发 H2/H3；有目录的正文可能出现多个 H1；标题换行在分类前被当作独立行；OCR/文字 PDF 的段落阈值在不同版式下可能把独立段落粘连或把未结束句拆开。
- 修改后：`scripts/pdf_markdown_refine.py` 以目录匹配、相对字号、编号、独立行和上下间距联合判定标题；粗体且正文同字号不升级；有目录时 `# 目录` 为唯一 H1、正文从 H2 开始；标题先合并再分类；正文仅在几何大间距、句末标点、字段标签或缩进变化时断段，未结束句续接；清理 PDF 中源文本行首 `# `，避免伪 Markdown 标题。OCR 改为使用全文件字号基线。
- 服务器动作：旧脚本备份到 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260805-pre-heading-paragraph-final/` 和 `/opt/ai-management/tmp/pdf-markdown-refine/backups/20260805-pre-heading-global-ocr/`；旧 OCR 镜像标签已被清理，使用现有等价离线镜像 `jx-now-ai-management-ocr:quick-20260805-020249-034af4811733`，未联网拉取、未新增服务。
- 验证：日志 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260805-024839/`，退出码 `0`，5/5 成功（3 个 `pdfplumber`、2 个 `RapidOCR`），263 页；5 个输出均非空且权限 `600`。有目录的 5 页/239 页文字样本分别生成 5/25 个目录项，均仅保留一个 H1（目录）、0 个嵌套/页码；长标题跨行合并 1 次；粗体正文误判 0；OCR 页脚、代码围栏、`None` 和错误标记均为 0。本地 14 项回归函数和编译检查通过。当前服务器脚本 SHA-256：`4a320e2d96df75aa8493eadd5731cbc923358efdc26fa3faf4033cad76d98b91`。
- 已知边界：没有目录匹配、字号与正文相同且间距不明显的编号行会保守按正文/列表处理；无边框复杂表格、多栏阅读顺序和 OCR 表格列结构仍需人工复核。

### 2026-08-05：合并 PDF 清洗与商业顾问去重执行计划

- 修改原因：按用户确认合并两份执行计划，增加机器可配置验收费用上限，并按“测试阶段集中记录、测试完成后统一修复”执行。
- 修改前：PDF 版式核心仍由独立脚本维护，上传链路没有统一 PDF 问题中心；商业顾问最终答案可能重复即时提示；验收费用没有机器侧累计门禁；Dify PDF Workflow 临时资产仍被当前索引引用。
- 修改后：共享 PDF 核心接入 `knowledge_cleaner`，PDF 告警/阻断复用 `SystemEventLog` 问题中心；商业顾问按精确前缀跨 chunk 去重；新增 `JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN`（默认 5.00 元）和 fail-closed 预检；Dify PDF Workflow/探针退役并恢复 jx 原始 Sandbox/SSRF 基线。当前只记录脱敏测试证据，未提交或推送 Git。

### 2026-08-05：合并计划统一修复、远端复测与精确清理

- 修改原因：远端代表性 PDF 验收发现问题中心事件按 `cleaning_file` 资源记录，而精确清理脚本只按批次 ID 删除，导致已清理测试批次仍残留 1 条 open 告警。
- 修改前：`scripts/jx_release_data.py` 删除批次事件时只匹配 `CleaningBatch.id`；首轮发布 `20260805-merge-6c843e` 的 PDF 测试批次与 Dify 文档已删除，但 file-scoped 告警仍可查询。
- 修改后：清理前读取目标批次的 `CleaningFile.id`，按“批次 ID + 文件 ID”精确删除对应 `SystemEventLog`；新增清理契约测试。第二次完整发布 `20260805-merge-fix` 后，代表性 PDF 第二轮成功并完成批次/Dify/Redis/事件精确清理，问题中心 open 总数为 0；首轮遗留事件按精确 ID resolve。
- 证据：发布报告 `/opt/ai-management/var/reports/v2/20260805-merge-fix-deploy`；本地脱敏报告 `var/reports/20260805-plan-merge/`；未提交或推送 Git。

### 2026-08-05：补充完成度与未完成测试阻塞说明

- 修改原因：用户要求明确区分已完成、未完成和测试问题处理状态，并按项目级 Agents 补齐当前状态文档。
- 修改前：顶部状态已记录主要功能和远端结果，但未单独列出全量测试超时、跨域批量超时及受保护样本缺失的未完成项。
- 修改后：明确产品实现、定向回归、远端验收和问题修复均已完成；环境/输入阻塞仍未完成且保留在脱敏 `issues.jsonl`，不生成替代样本、不伪报通过。未修改 `AGENTS.md`，未提交或推送 Git。

### 2026-08-05：补齐排障文档模板并复核测试状态

- 修改原因：文档契约测试发现历史排障条目字段不完整；用户要求按项目级 Agents 更新相关文档并明确测试问题是否全部处理。
- 修改前：DEPLOY-007/009/010/011/012、部分 DATA/DIFY 条目缺少模板字段；状态区未区分已修复的产品问题与仍开放的测试环境阻塞。
- 修改后：补齐部署、数据清理、Dify 排障条目的完整字段，文档契约测试 `11/11` 通过；状态区明确全量 pytest/跨域批量超时和受保护样本缺失仍为 `open` 环境/输入阻塞，未生成替代样本或伪报通过。证据和状态见 `var/reports/20260805-plan-merge/issues.jsonl`。

### 2026-08-05：补充商业顾问首屏问题、内部标识过滤及剩余事项

- 修改原因：用户要求只调整商业顾问首次进入页面的固定问题，并在 `ai-management` 用户可见文本出口过滤路线、阶段、课程内部标识，同时明确已处理问题、测试结果和仍需处理的问题。
- 修改前：当前 DSL 的首屏固定问题数量和文案未满足本轮确认口径；服务端公开文本过滤边界未在正式接口标准中明确覆盖阶段标识、英文/短 ID、括号形式和跨 SSE 分片；状态交接未集中记录本轮线上发布限制与路由契约测试阻塞。
- 修改后：首屏固定为 3 条现有 `AI高手提效课` 相关问题；服务端统一过滤用户可见回答、历史、推荐、专家建议和引用资源中的课程/路线/阶段标识及旧占位词，同时保留 Chatflow 决策 ID 和课程卡/路线进度机器字段；补充 API/SSE、Dify 配置指南、正式接口标准和本状态交接。
- 验证结果：相关定向回归、Ruff、Python 编译、统一 DSL 构建检查、功能索引检查和排障文档字段检查均通过；仍保留 Dify 未发布、两个 `_IncludedRouter` 路由契约测试失败、全量长测/受保护夹具环境阻塞，未将其误报为完成。
- 变更边界：未修改 `AGENTS.md`，未执行 Dify 发布、远端写入、Git commit 或 push；完成线上配置仍需按本状态顶部说明由有权限人员发布并在缓存失效后核对。

### 2026-08-05：恢复课程/路线正式名称并统一后端公开文本出口

- 修改原因：用户实测发现过滤器把课程名称列表变成“内部标识已隐藏”，并继续泄露阶段 ID；要求从课程推荐、路线确认、阶段进度和历史链路整体修复。
- 修改前：`stream_filter.py` 只删除 ID/占位词，无法把 ID 还原为目录名称；路线快照按原始 `order` 返回，`AI高手提效课` 可能先显示“高手进阶”；路线知识库材料主要提供 ID，模型容易臆造课程名。
- 修改后：新增已审核目录 ID→名称映射、占位列表恢复、错误名称纠正、未知 ID 不可用提示和跨 SSE 分片处理；`courses.py` 固定阶段顺序并升级快照版本；`course_import.py` 生成路线资料时补充课程名称；结构化机器字段保持兼容。
- 本地证据：`tests/test_stream_filter.py`、`tests/test_ai_management.py`、`tests/test_course_route_catalog.py` 相关用例通过；目标代码 Ruff、Python 编译通过。组合长测曾被遗留 pytest 进程占用 SQLite 文件，已拆分运行并保留该环境现象，不把它写成代码失败。
- 后续：发布、真实 SSE 和精确测试会话清理已完成；小程序纯前端渲染问题继续保留为开放事项。

### 2026-08-05：线上发布、路线确认修复与最终真实 SSE 验收

- 修改原因：真实回归发现小程序实际发送的“我确认学习路线：AI高手提效课”未命中后端路线确认判定；同时需确认名称恢复、课程卡片、阶段顺序和三条首屏问题在最终 release 上整体生效。
- 修改前：后端只接受“确认选择”等少数路线确认表达，截图文案会持续停在待确认；快速部署脚本成功路径关闭退出 trap 前没有执行 OCR 状态恢复；首次成功部署后 OCR worker 保持 exited。
- 修改后：`courses.py` 接受明确的“确认学习路线/选择学习路线”表达并保留问题句拒绝规则；补充路线确认回归测试。`scripts/jx_quick_deploy.sh` 在成功关闭 trap 前显式恢复发布前 running 的 OCR worker；远端原脚本已按精确目录备份后更新。
- 验证：最终 release `quick-20260805-142049-ddc348e62453` 保留数据发布成功；API ready、Dify reachable、test UI 200、OCR running。三条固定问题和路线连续链路真实 SSE 均通过，路线确认后进入阶段确认，阶段确认后为 active 并返回 5 张卡片；可见文本无内部标识/旧占位词，课程链接有效。入口配置返回 3 条问题且与目标文案完全一致。
- 清理边界：仅删除本轮 4 个合成用户的会话，删除接口均返回 200；未删除正式课程、路线、Dify 数据集、Redis 整库或持久卷。未修改固定接口文档，未执行 Git commit/push。
- 遗留验证：`tests/test_project_documentation_contract.py` 仅剩 `_IncludedRouter.path` 测试配置兼容失败；`tests/test_app_routing.py` 本轮超出 120 秒等待窗口。两项均按脱敏证据保留，未扩展本次业务改动范围。

### 文档修改记录（2026-08-05 统一清洗链路任务）

- 修改原因：记录本轮统一转换、表格清洗、策略/模型/复核、父子分隔符、Metadata、Dify 发布和 jx 新数据验收的最终事实，并覆盖部署中发现的 OCR 早期失败恢复问题。
- 修改前：顶部最新状态仍以旧 PDF 表格告警行为、旧 release 和旧测试快照为准，未记录统一 Markdown 最终门禁、Dify `.md` 命名及本轮标准 PDF/DOCX/Markdown 验收。
- 修改后：新增 0.31 当前状态，记录根因、统一链路、release/备份/报告、3 个标准文档验收和精确清理结果；历史章节保留不改写。

### 文档修改记录（2026-08-05 raw 验收补充）

- 修改原因：补充用户确认的 raw 上传模式验证，避免只验证 `cleaned` 而遗漏 raw 的转换和表格清洗分支。
- 修改前：0.31 已记录 `standard_document` 的 PDF、DOCX、Markdown 新数据验收，但未记录 raw 三种文件类型的远端结果。
- 修改后：补充 raw PDF、DOCX、Markdown 均成功、Dify 名称/Metadata 通过及精确清理结果；专用输入目录已删除，历史输入和正式数据未清理。

### 文档修改记录（2026-08-06 清洗失败隔离策略与 jx 验收）

- 修改原因：用户确认不再让几十万个文档因模型契约或单文档异常整体进入人工审批；要求模型无返回值最多重试一次，仍失败则记录文件名、策略和失败原因并跳过，失败记录保留 7 天，同时完成 jx 测试页到知识库入口的真实验收。
- 修改前：内容模型策略的契约/模型异常可能被聚合为 `review_required`，缺少按文档失败隔离和统一失败记录查询；快速发布无法覆盖本轮新增的共享 `knowledge_cleaner` 模块。
- 修改后：四类内容模型策略按文档隔离 `failed`、`review_required` 和成功结果；仅无响应重试一次，非空契约失败不重试；新增失败条目分页/CSV 查询接口和 7 天保留；通过完整 wheel 发布解决共享模块导入门禁。
- 验证：release `20260806-065957-full-bd1c4762d48d` 的 ready、Dify、测试页和同源服务检查通过；测试页批次 `019fd42f-7599-7cc0-a4ce-6eacc1063dc1` 使用 `zsxq_post` 成功完成实时模型调用、Dify 发布和条目详情回读，Metadata 可读；本地 `pytest tests -q` 全部通过。
- 问题与证据：首次快速发布的 OCR 授权门禁和共享模块导入失败，以及测试页隐藏上传控件问题，均记录在 `var/reports/20260806-cleaning-policy/issues.jsonl` 并已解决。发布报告/备份路径见顶部 0.32；未修改小程序后端和既有 AI 管理接口，未提交或推送 Git。

### 文档修改记录（2026-08-06 review_required 开关修复与最终验收）

- 修改原因：用户确认需要通过环境配置关闭过严的 `review_required` 阻断，同时要求空文档仍复核、非空文档继续模型/知识库链路，并在 jx 测试页验证到条目入口。
- 修改前：jx 测试环境没有明确的开关语义；空正文策略异常未被 Worker 的空内容识别规则匹配，空文档重试后变成 `CLEANING_FAILED`；上一轮状态只记录了旧 release 和旧测试批次。
- 修改后：补充 0.33 状态，记录默认开启/jx 测试关闭、批次快照、非空复核旁路与安全硬门禁；Worker 修复“没有可清洗的正文”分类；同步记录新 release、备份、问题报告、空文档复核和非空文档入库证据。
- 验证：本地全量 pytest、索引和编译检查通过；jx release `20260806-111200-full-8f59f14169c9` ready；空文档批次 `019fd511-11ea-718b-bfed-0ba266f08d4d` 进入 `review_required`，非空批次 `019fd515-596b-78b2-8733-05cb5ab460c4` 成功发布并可读取条目详情。未修改 `AGENTS.md`、小程序后端和既有 AI 管理接口，未提交或推送 Git。

### 文档修改记录（2026-08-06 新批次降级与完整发布）

- 修改原因：用户要求模型失败时自动使用表格清洗后的源 Markdown 入库，避免几十万个文档卡在 `review_required/failed`，同时要求完善错误定位日志、Dify 多文件上限处理和看门狗，并完成 jx 部署。
- 修改前：模型复核/技术失败可能成为人工阻塞；普通发布任务未统一进入单并发 Dify 队列；Dify 分片异常路径可能不刷新文件/批次聚合；错误事件只有异常类型，难以定位文件和阶段。
- 修改后：新增新批次源 Markdown 降级和历史批次 fail-closed；空源明确失败；所有 Dify 写入走 `ai_publication` 单并发；Dify 文章分片遵守配置上限；新增 5 分钟清洗管线看门狗；错误事件保存脱敏上下文和异常摘要；同步 ADR、清洗/Dify 排障文档和功能索引。
- 验证：本地全量 pytest、编译、文档索引和契约通过；快速发布因远端缺少共享模块自动回滚；完整 wheel release `20260806-144000-full-9afc8b78bc35` 发布成功；真实合成 Markdown 上传到 Dify 后成功并按精确 file ID 清理。未修改 `AGENTS.md`、对外接口或 Git 历史。

### 文档修改记录（2026-08-06 根因修复、日志增强与完整发布）

- 修改原因：用户提供 `lead_magnet` pending、`book` failed 和 `zsxq_post` 清洗结果疑似未提取的问题，要求从上传、策略、模型、分隔符/Metadata、Dify 发布到日志完整定位，并部署修复。
- 修改前：资料钩子在本地预写 Dify 分隔符后又被边界校验拒绝；书籍 PDF 转换硬门禁原因未在任务详情中充分显式；帖子模型只保存保留/排除后的源块，未要求结构化知识单元；失败日志缺少可定位异常上下文。
- 修改后：资料钩子保留源 Markdown，由发布 Worker 统一交给 Dify 分块；帖子模型增加证据绑定知识单元和独立文档编排；错误事件补充脱敏异常消息与 traceback；保留历史批次状态，不自动回写旧数据。
- 验证：本地目标回归、Ruff、编译、索引和 diff 检查通过；jx release `20260806-165915-full-23162b2` ready、迁移 head、同源镜像、OCR timer 和容器内烟测通过。未修改 `AGENTS.md`、对外接口或 Git 历史。

### 文档修改记录（2026-08-06 帖子知识单元契约强化与最终发布）

- 修改原因：复核 `zsxq_post` 历史模型结果后确认旧 schema 只有 `keep/drop`，不能证明完成了认知提取；用户要求认知分享真正提取可迁移商业知识，同时仍保证失败不阻塞入库。
- 修改前：模型返回非空 `keep/drop` 即可进入成功路径，帖子正文可能与源 Markdown 基本一致。
- 修改后：保留内容必须有问题、结论、推理、适用条件、行动、风险和来源块组成的知识单元；缺失知识单元按契约失败处理，新批次自动回退源 Markdown。
- 验证：本地相关套件通过；jx 最终 release `20260806-172003-full-23162b2`、wheel SHA、备份、报告、readiness、迁移、同源镜像、OCR timer 和容器契约烟测均通过。未修改 `AGENTS.md`、对外接口或 Git 历史。

### 文档修改记录（2026-08-06 学习路线阶段课程集名称与完整发布）

- 修改原因：用户确认学习路线目前缺少课程集名称，要求按阶段内原始文档顺序展示课程集名称，不增加课程名称、课程 ID 或复杂存储结构，并要求完成本地测试和 jx 部署。
- 修改前：`learning_route.stages[]` 只有阶段/课程相关字段，客户端无法直接展示阶段对应的课程集名称；部署验证记录未包含本轮字段契约和真实 SSE 结果。
- 修改后：新增 `stages[].course_collection_names` 数组，按阶段 `course_ids` 顺序从既有课程快照运行时派生并首次出现去重；同步公开 SSE 契约文档、部署验证记录和顶部最新线上状态。第一次快速发布因旧运行时缺共享模块自动回滚，随后用完整 wheel 成功发布。
- 验证：本地全量 `339 passed`，Ruff/编译/索引/diff 检查通过；jx release `20260806-195815-course-collections` ready，真实 SSE 返回 2 个阶段且所有阶段字段均为数组；合成会话已精确删除，正式数据未清理。
- 变更边界：未新增 Redis Key、数据库字段或迁移，未修改小程序后端和 Dify App，未执行 Git commit/push。

### 文档修改记录（2026-08-06 商业顾问“下一步建议”服务端发布）

- 修改原因：用户确认将本地商业顾问“推荐理由”调整的服务端实现部署到 jx，并要求解决部署中发现的问题、沉淀本地排障资料。
- 修改前：快速发布只上传 `ai_management`，服务器宿主机源码缺少 `knowledge_cleaner.markdown_table_cleaner`，导入门禁在服务切换前中止；完整发布脚本的 post-switch 回滚会引用 `env.backup`，但没有自行创建该文件。
- 修改后：构建含 `ai_management` 和 `knowledge_cleaner` 的完整 wheel，完成 release `20260806-190738-business-next-step`；API、Worker、Scheduler、三个专用 Worker 与测试页已切换为同源镜像。`scripts/jx_deploy_release.sh` 在任何副作用前备份 `.env`，并已校验后同步至 jx；新增对应发布契约测试和 DEPLOY-017 排障条目。
- 验证：wheel SHA-256 与服务器一致；API ready、数据库/Redis/Dify 配置与可达性为 true，容器内可导入 `ai_management.conversations` 和 `knowledge_cleaner.markdown_table_cleaner`，Alembic 为 `j6a7b8c9d0e1 (head)`，测试页 HTTP 200，OCR autoscaler timer active。发布过程未清理数据、未发布 Dify App、未改对外接口或 SSE 事件。
- 边界：当前已部署服务端门禁与 SSE 语义代码；因用户已确认不发布 Dify，线上商业顾问 Chatflow/DSL 仍未导入或发布，新的 `expert_course_ids` 输出契约尚未在 Dify 端生效。未提交或 push Git。

### 文档修改记录（2026-08-07 回到最新版供问题验证）

- 修改原因：用户在回退到 `20260806-172003-full-23162b2` 后，要求切换回 jx 当前最新版以复现和验证问题。
- 修改前：jx 运行 `20260806-172003-full-23162b2`；服务器最新完整三镜像为 `20260806-195815-course-collections`。
- 修改后：先完成 `forward-20260807-013426-to-20260806-195815-course-collections` 的数据库、Redis、上传卷和配置快照，再恢复 `195815-course-collections` 的核心/UI/OCR 镜像与 wheel；未修改数据库、Redis、上传卷或 Dify 应用。
- 验证：API、Worker、Scheduler、测试页均为目标镜像且 running；ready、数据库/Redis/Dify 连通、Alembic `j6a7b8c9d0e1 (head)`、容器内共享模块导入、测试页 HTTP 200、OCR autoscaler timer active 均通过。用户可直接开始问题验证。

### 文档修改记录（2026-08-07 课程目录恢复与路线活动分支修复）

- 修改原因：真实页面复核发现课程目录只有部分数据，且活动路线的短阶段追问在 Dify 返回 `general_qa` 时出现原始课程 ID、课程卡缺失；用户同时要求确认 PDF/目录修复不影响无关功能并完成 jx 页面验收。
- 修改前：jx 使用回退 release；目录为不完整快照，路线阶段 ID 与旧进度指针不一致；服务端仅对 `route_recommendation` 做路线展示字段补充；发布报告可能复用旧 release ID 的失败标记。
- 修改后：完成完整目录批次 `019fda86-9df8-71fa-99c1-c7f0b620205c` 的 110/110 成功验证和显式恢复，目录计数恢复为 105/5/33/103；服务端统一装饰已校验路线并按阶段名/顺序稳定阶段 ID；发布脚本清理旧失败标记；发布 `20260807-1345-route-activefix` 并完成隔离用户页面路线、阶段、课程卡和综合点评验收。
- 验证：本地课程路线定向测试 `21 passed`；远端 ready/live、同源镜像、迁移和导入烟测通过；页面确认路线后进入阶段确认，阶段确认后课程卡后只有一条无前缀综合建议。首次发布因资源看门狗瞬时 I/O 阻断自动保持旧服务，重试发布成功。
- 边界：未发布 Dify DSL、未新增 HTTP/SSE 事件、未改变 `course_ids` 机器字段或真实小程序、未迁移历史 PDF/批次、未执行 Git commit/push。

### 文档修改记录（2026-08-07 全量测试与线上最终核验补充）

- 修改原因：目录恢复、路线活动分支修复和部署完成后，补录最终全量测试与线上运行态证据。
- 修改前：执行计划只记录“文档/索引更新后重新执行全量测试”，未写入最终测试数量；顶部状态缺少本轮文档契约与全量测试数字。
- 修改后：补录文档契约 `11 passed`、全量 `pytest tests -q` `379` 项通过、现有弃用警告，以及 `health/live`、`health/ready`、同源容器和目录计数复核结果。
- 验证：文档契约、索引检查和 `git diff --check` 通过；jx 当前 release `20260807-1345-route-activefix` 无回滚标记，页面验收页保持交接状态。
- 边界：仅更新验收记录，不修改代码、数据库、Dify、对外接口或历史业务数据。

### 文档修改记录（2026-08-09 jx 服务不可访问排查）

- 修改原因：用户反馈前两天仍可访问、今天 `ai-management` 无法访问，要求核对 Git 分支、部署版本、服务器服务状态并记录排障经验。
- 修改前：顶部最新状态仍停留在 2026-08-07 发布完成/页面可复核，未反映当前 AI Compose 已停止、Nginx 502 和本地 HEAD 与线上 release 的差异。
- 修改后：新增 0.45 当前检查结果，记录本地 `work` 分支与 HEAD、jx release `20260807-1455-route-collections`、8 个 AI 容器 `Exited (0)`、API 正常 shutdown、Nginx 502、清理流程停服线索、未确认的具体触发命令和待授权恢复边界；新增脱敏开放问题报告，不把服务宣称为已恢复。

### 文档修改记录（2026-08-09 jx 停服根因定位）

- 修改原因：继续追踪 18:28 停服事件，确认用户反馈“前两天还能访问、今天不可访问”的完整链路和可复用防复发措施。
- 修改前：0.45 只确认容器被正常停止，尚未确认具体脚本及为何数据清理报告成功后服务仍未恢复。
- 修改后：新增 0.46，确认实际执行的是服务器专用 `jx_reset_course_document_data_test.sh`；该脚本成功路径明确保持服务停止，postcheck 使用一次性 `docker compose run` 只复核数据归零，不复核命名服务；本次没有后续 `start` 或命名容器启动证据，导致 Nginx 502。新增脱敏根因与分阶段整改报告；未修改 jx、未宣称服务已恢复。
- 验证：服务器脚本内容、脚本更新时间、备份/执行/postcheck 脱敏摘要、Docker journal 和当前容器状态相互吻合；本地文档契约测试与 `git diff --check` 另行执行。

### 文档修改记录（2026-08-09 Markdown 表格清洗服务器隔离验证）

- 修改原因：用户要求确认之前的 Markdown 表格清洗脚本/共享实现能否在 jx 服务器上测试，并核对服务器 CLI 与本地版本是否一致。
- 修改前：顶部最新状态仍记录 jx 停服，未记录服务已经恢复运行，也未记录服务器实际 PDF 脚本没有 `--clean-tables` 以及共享 Markdown 表格清洗验证结果。
- 修改后：新增 0.47，记录只读健康证据、服务器入口差异、合成样本转换/降级/代码围栏验证、5 个真实 Markdown 的 8 张表全部按 `empty_header` 安全降级，以及本地定向回归结果；明确未执行服务启动、发布、清理或 Dify 写入。

### 文档修改记录（2026-08-09 Markdown 表格清洗隔离输出实测）

- 修改原因：按用户要求在 jx 服务器对指定 Markdown 目录执行已有表格清洗实现，并保留可复核结果。
- 修改前：顶部 0.47 记录的是前一次隔离验证，写明当次临时输出已删除，未记录本次新建的保留式隔离输出和幂等校验。
- 修改后：新增 0.48，记录本次共享清洗函数、实际 OCR 镜像、5 个输入、8 张表的 `empty_header` 安全降级、结果目录与报告位置；明确原目录未覆盖、输出权限为 `0600`、结果二次运行幂等。
- 验证：服务器输出 5/5 非空，2 个文件变化，`tables_converted=0`、`tables_demoted=8`、`rows_skipped=0`；未修改服务、配置、数据库、Redis、Dify 或原始 Markdown。

### 文档修改记录（2026-08-09 最新代码重建发布）

- 修改原因：用户要求使用最新代码重新在 jx 部署一套、完成测试，并把部署问题与解决方案记录到本地。
- 修改前：顶部最新状态为 Markdown 表格隔离验证，未记录本次完整 wheel 重建、jx release、发布失败重试、完整清洗验收结果和最新服务状态。
- 修改后：新增 0.49，记录 release `20260809-125124-full-e06470c`、备份/报告路径、同源镜像、ready/UI/OCR/迁移/资源门禁、wheel 模块闭包问题、Windows CRLF 问题、远端验收统计、当前部分课程目录状态及补充回归边界；新增本地脱敏问题报告链接，并在 `docs/troubleshooting/deployment.md` 增加 DEPLOY-019/020。
- 验证：API ready、Dify 无写入契约、实时业务 SSE、stop 接口、测试页和 OCR timer 均完成核验；完整清洗验收安全报告、退出码和精确清理 manifest 已保留。未执行 Git commit/push，未发布 Dify DSL/App，未做非清单范围清理。

### 文档修改记录（2026-08-09 完成验收测试文档精确清理）

- 修改原因：用户确认删除本次部署完整清洗验收产生的测试文档。
- 修改前：0.50 记录 general manifest 的 71 个目标仍存在，尚未执行删除。
- 修改后：按 manifest-only 流程完成删除 Dify general 文档 71 篇、清洗批次 19 个、测试会话 1 个及对应精确 Redis/上传状态；course manifest 目标为 0，未删除 course 文档。
- 验证：清理报告 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-cleanup` 显示目标文档重叠数为 0；API ready、测试页 HTTP 200、OCR timer active，未扩大触碰正式目录。

### 文档修改记录（2026-08-09 完整部署手册补充）

- 修改原因：用户要求把整个代码重建部署流程、踩坑和注意事项整理成下次可直接复用的本地手册。
- 修改前：0.49 已记录本次 release、验收结果和问题链接，但没有从源码检查、wheel 闭包、LF 传输、备份、发布、回滚到测试判定的完整操作顺序。
- 修改后：新增 `docs/jx完整代码重建部署与避坑手册.md`，并在 `docs/README.md` 建立入口；手册包含命令模板、停止条件、数据边界、失败分类、回滚要求和本次实战路径。
- 验证：文档契约、项目索引检查和 `git diff --check` 通过；未修改 jx、未执行 Git commit/push。

### 文档修改记录（2026-08-09 完整清洗验收测试文档清理边界）

- 修改原因：用户发现本次部署后知识库出现测试文档，要求确认来源、是否来自备份、重启/重构是否会重复出现以及删除边界。
- 修改前：0.49 只记录完整清洗验收生成了 cleanup manifest，未记录该 manifest 尚未执行清理，也未核对目标 Dify 文档是否仍存在。
- 修改后：新增 0.50，确认本次 general manifest 的 71 个精确 Dify 文档目标仍全部存在；明确它们来自验收上传而不是备份恢复，普通重启/重构不会重新生成；新增 DATA-033 和本地脱敏问题记录，保留删除需用户确认的边界。
- 验证：只读对账完成，未删除任何 Dify 文档、数据库记录、Redis key 或课程数据；正式文档未按名称或模糊条件触碰。

### 文档修改记录（2026-08-09 完成用户确认的精确清理）

- 修改原因：用户确认删除本次部署完整清洗验收产生的测试文档。
- 修改前：general manifest 的 71 个目标仍存在；顶部记录尚未执行删除。
- 修改后：按 manifest-only 流程完成删除 Dify general 文档 71 篇、清洗批次 19 个、测试会话 1 个及对应精确 Redis/上传状态；course manifest 目标为 0，未删除 course 文档。
- 验证：清理报告 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-cleanup` 显示目标文档重叠数为 0；API ready、测试页 HTTP 200、OCR timer active，未扩大触碰正式目录。

### 文档修改记录（2026-08-09 用户反馈后完成全范围测试数据清理）

- 修改原因：用户反馈知识库仍有测试文档，并明确要求同步清除文档对应的数据库和缓存。
- 修改前：0.50 只记录旧 general manifest 的 71 篇清理结果；未记录独立 `business-course-test` Dataset、当前 release 后续 general 测试批次和课程快照缓存的对账结果。
- 修改后：新增 0.51，记录独立测试 Dataset 的 110 篇文档清理、course 文档域 22 篇/3 批次/21 门课程/1 条路线清理、当前 release 6 个 general 批次及其 Dify/数据库/Redis 清理、空课程快照 key 的最终精确删除，以及最终 general 保留范围和服务状态；新增 DATA-034 与本地脱敏问题记录。
- 验证：`business-course-test=0`、配置 `course=0`、当前 release 批次 `0`、课程快照 key `0`；general 保留 `264` 篇非目标文档，API ready、测试页 HTTP `200`；备份和清理报告路径已记录，未按名称或模糊条件删除剩余 general 数据。

### 文档修改记录（2026-08-09 定位并清理 general 残留）

- 修改原因：用户反馈知识库仍未清空，要求定位核心问题并完成文档、数据库和缓存清理。
- 修改前：0.51 的阶段性结论仍把 general 的 263 篇文档视为未被本次精确范围证明的历史数据；未识别其中 237 篇实际没有本地清洗关联，前述清理结论不完整。
- 修改后：记录根因——Dify 直接 API 上传绕过本地 `CleaningBatch` provenance；冻结写入后将完整 general Dataset 文档 ID 集合纳入 v3 精确 manifest，完成 Dify、清洗数据库、Redis 和上传目录清理，同时保留配置、会话和无关日志。
- 验证：Dify `general/course/business-course-test/business-general-test` 均为 `0`；本地清洗批次/文件/文档/模型任务/发布分片为 `0/0/0/0/0`，清洗 Redis 键和课程快照 key 为 `0`，上传目录为空；API ready、测试页 HTTP `200`、OCR timer `enabled/active`、Celery active 为空；最终报告为 `/opt/ai-management/var/reports/v2/20260809-143906-general-document-cleanup`。

### 文档修改记录（2026-08-09 明确写入型验收的清理规则）

- 修改原因：用户询问本次实际操作及知识库文档是否需要在每次部署后清除。
- 修改前：0.51 已记录本次清理结果和根因，但没有把“无写入验证”与“写入型验收”的常规收尾规则明确区分。
- 修改后：明确默认部署验证不清理知识库；仅完整清洗验收、测试上传、课程/路线测试导入等写入型测试需要按精确 manifest 和 Dataset inventory 清理；正式和已授权保留数据默认保留。
- 验证：规则与本次 Dify inventory、v3 精确清理报告及 `docs/jx完整代码重建部署与避坑手册.md`、`docs/troubleshooting/deployment.md` 的更新一致；未执行新的远端删除、发布或 Git 操作。

### 文档修改记录（2026-08-09 清理 ai-management 应用日志）

- 修改原因：用户要求清理 jx 服务器上的 ai-management 日志。
- 修改前：顶部 0.51 只记录文档和课程测试数据清理，且写明无关审计日志未纳入范围；未记录本次应用日志清理结果。
- 修改后：新增 0.52，明确“日志”仅指 `SystemEventLog`，记录 `130 → 0`、备份目录、服务恢复、ready/容器复核及 Docker/systemd/Dify 日志保留边界。
- 验证：清理后 `SystemEventLog=0`，API `/health/ready` 为 `ready`，8 个 ai-management 容器运行；未删除文档、课程、Dify、Redis 业务数据或持久卷。

### 文档修改记录（2026-08-09 意外知识库写入根因与修复）

- 修改原因：用户在未手动上传后再次发现知识库数据，要求定位根因并处理，且要求沉淀完整避坑信息。
- 修改前：0.52 仍把此前文档清理视为当前终态，未记录临时公网桥接实际暴露内部上传接口、周期请求重建数据及对应处置。
- 修改后：新增 0.53，记录桥接暴露根因、调用方可观察边界、精确 `403` 防护、备份、v3 事故 manifest 精确清理、Dify/数据库/Redis/上传卷归零和服务复核；同步新增 DEPLOY-022、DATA-036、完整部署手册及脱敏报告。
- 验证：07:30 的 3 次实际周期上传请求均返回 `403`；清理后 Dify `general/course=0`、本地清洗与课程/路线为 `0`、清洗 Redis/课程快照/上传卷入口为 `0`，API ready、测试页 HTTP `200`。未记录密钥、正文、真实用户 ID、原始文件名或精确远端文档 ID。

### 文档修改记录（2026-08-10 咨询会议 RAG 清洗、jx 验收与发布）

- 修改原因：用户要求将咨询会议总结的模型清洗接入 `personal_business_consultation`，在 jx 样本上验证并沉淀失败边界、Metadata 和部署踩坑。
- 修改前：交接文档没有记录 `consultation-rag-v1` 合同、模型失败的整份兜底规则、最终 jx 样本批次、发布 wheel 及二进制 stage 事故。
- 修改后：新增 0.54，记录实现与 Metadata 收窄原则、模型安全归一化与严格失败边界、本地/相关测试结果、最终 jx release/备份/报告、4 源文件→7 文档的样本验收、Dify 本地 DSL 分支状态和待登录发布事项；同步新增 DATA-045、DEPLOY-023。
- 验证：最终 jx release ready、关键包导入、OCR timer 和样本批次回读均通过；Dify 正式导入/发布仍待管理会话登录后完成，未猜测或写入凭据。

### 文档修改记录（2026-08-10 Chrome Dify 导入权限核验）

- 修改原因：用户确认 Chrome 可直接访问 Dify `/apps`，要求继续完成正式导入/发布。
- 修改前：0.54 将 Dify 未完成发布归因于浏览器未登录。
- 修改后：确认 Chrome 工作区已登录；实际阻塞收窄为 ChatGPT Chrome 扩展未启用本地文件 URL 访问，文件上传事件无法触发，因此尚未上传 DSL，也未伪造或读取凭据。
- 验证：当时 Chrome `/apps` 页面可见 `jx's Workspace` 和现有应用；Dify 导入弹窗可打开，但隐藏文件控件未触发高层 chooser。该历史阻塞已在随后记录中通过页面级 CDP 临时显示控件解决，不需要用户开启扩展权限或手工上传。

### 文档修改记录（2026-08-10 Chrome 隐藏文件控件自动导入与 Dify 发布）

- 修改原因：用户明确要求不要人工上传，且指出 Chrome 已登录可正常访问；需要纠正先前把文件控件问题误判为登录/扩展权限问题，并沉淀可复用的自动化解决方式。
- 修改前：上一条记录把 Dify 未导入归因于本地文件 URL 权限，未记录隐藏 `input[type=file]` 的页面结构，也未完成正式发布。
- 修改后：确认登录态正常；使用标签页级 CDP 临时显示 Dify 隐藏文件控件，再通过标准 chooser 自动上传 DSL；隔离应用 `eaa840e6-ec85-47e4-89a5-5cf0678f7ac3` 已显示“最新发布/已发布”，并补充 `docs/troubleshooting/dify.md` 的 DIFY-013。未读取或写入 Chrome 扩展权限，未输出密钥。
- 验证：Dify `/parameters` 返回 `200`；真实 Chat API 返回 `200` 且带 `retriever_resources` 元数据（本次测试 Dataset 为空，召回数为 `0`）；案例检索节点的 `record_type=consulting_case` 绑定已在编辑器复核。

### 文档修改记录（2026-08-10 jx 切换新 Dify 应用并完成真实 SSE）

- 修改原因：正式导入会创建新隔离应用，必须让 jx 测试环境实际使用新发布版本并验证服务端调用，而不是只验证 Dify 编辑器状态。
- 修改前：jx `.env` 仍指向旧商业顾问应用，只有本地静态检查和 Dify 页面发布证据；无法证明 jx 已使用新分支。
- 修改后：备份 `.env` 到 `/opt/ai-management/backups/20260810-055542-consultation-dify-key-predeploy/env.backup`，只替换 `DIFY_BUSINESS_APP_API_KEY`，按 DEPLOY-013 强制重建 `api/worker/scheduler`；旧应用保留回滚，未删除或修改其它环境。
- 验证：jx `health/ready=ready` 且 `dify_reachable=true`；真实“相似案例/类似经验”请求返回 HTTP `200`，SSE 事件含 `message_end` 且 `status=completed`，测试会话删除返回 `200`。密钥只在进程内使用，文档仅保留备份路径和脱敏指纹。

### 文档修改记录（2026-08-10 原计划剩余门禁续测）

- 修改原因：继续此前已完成的咨询会议清洗、知识星球样本、Dify 发布和 jx SSE 验收，执行原计划剩余的本地回归与可安全执行门禁。
- 修改前：交接文档只记录前序 jx/Dify 完成结果和更早的剩余生产门禁，未记录本轮本地 397 项收集结果、测试 harness 修复及当前环境无法建立 jx 通道的事实。
- 修改后：新增 0.55，记录 `396 passed + 1` 个已有冻结摘要失败、直接相关 `67 passed`、静态门禁和黄金集本地解析；补充 jx RAG `general 10/10、course 0/23`、Dify 无写入合约、商业 E2E `2/6` 及课程目录基线不匹配结论，明确未执行破坏性恢复、灰度或正式数据替换。新增 `TEST-012`、`TEST-013`、`DATA-046`、`DEPLOY-024` 与脱敏续测报告。
- 验证：`.venv` compileall、功能索引 `--check`、目标 Ruff、Git Bash `bash -n`、全量 pytest、jx `/health/ready`、关键包导入、Dify/SSE、业务 E2E 和精确临时文件清理均已运行；未修改小程序接口文档、正式 Dataset、课程目录或 jx 持久化数据。

### 文档修改记录（2026-08-10 jx RAG 与商业 E2E 续测完成）

- 修改原因：远端通道恢复，继续原计划中可安全执行的 jx RAG、Dify 合约和商业顾问轻量 E2E，并记录课程黄金集与当前目录基线差异。
- 修改前：0.55 仅记录 jx 通道不可用，未有本轮远端检索命中、业务 E2E、合约状态和最终服务复核证据。
- 修改后：补充 jx RAG 失败但可解释的分域指标、Dify 两轮 SSE/三个 Workflow 合约结果、E2E `2/6` 及正式课程数据保留边界；脱敏报告和问题记录同步更新。
- 验证：jx 最终 release 保持 ready，临时 fixture 和测试会话均按精确范围收尾；未清空、替换或删除 general/course 正式数据。

### 文档修改记录（2026-08-10 知识库上传内网来源放行）

- 修改原因：内网调用方 `172.17.28.83` 访问知识库上传接口被 Nginx 返回 `403`，用户要求定位并修复。
- 修改前：临时 `/ai-service/` 桥接对 course/general 的 `upload-batches` 及其子路径使用硬编码 `return 403`；请求未进入 ai-management，虽已存在该来源到 TCP `8000` 的主机防火墙允许规则。
- 修改后：恢复并安装受版本控制的 `scripts/configure_dify_nginx_ai_service_guard.py`；上传路径改为仅允许 `172.17.28.83/32`，随后代理至 ai-management。非该来源仍为 `403`，且转发后仍要求 `X-Internal-Token` 和 `X-Admin-Token`。
- 验证：Nginx `nginx -t` 与热加载通过；非白名单本机探针为 HTTP `403`；主机精确来源规则仍存在；API `/health/ready` 为 `status=ready` 且数据库、Redis、Dify 可达，8 个 ai-management 服务均运行。配置与旧脚本备份位于 `/opt/ai-management/backups/20260810-1705-ai-service-upload-source-allowlist`。

### 文档修改记录（2026-08-10 更正知识库上传 403 根因）

- 修改原因：首次来源白名单修复后，4.10.2～4.10.8 仍返回 `403`；用户明确指出经内网端口 `80` 调用、而其他 ai-management 接口正常，要求重新定位真实根因。
- 修改前：将调用方声明的 `172.17.28.83/32` 直接作为 Nginx 唯一允许来源，并把问题归因于此前硬编码 `return 403`；未验证 Nginx 在 Docker NAT 后实际记录的客户端地址，也未区分裸 `/internal/...` 与实际桥接路径。
- 修改后：Nginx access log 证明实际成功/失败请求均为 `/ai-service/internal/v1/...`；裸 `/internal/...` 在端口 `80` 返回 Dify Web 的 `404`。4.10 上传组被专用 location 的 `deny all` 拦截，Nginx 可见来源为 `172.18.0.1/32`。guard 改为必须显式传入多个单主机来源，当前同时允许真实后端 `172.17.28.83/32` 和 NAT 来源 `172.18.0.1/32`；接口文档、部署手册和排障库同步更正。
- 验证：Nginx `nginx -t` 与热加载通过；正确双 Token、无 `files` 的 `POST /ai-service/internal/v1/admin/knowledge-bases/general/upload-batches` 返回 `422`，并在 ai-management API 日志中记录，证明已越过网关且未产生上传写入；API `/health/ready=status=ready`。配置/脚本备份为 `/opt/ai-management/backups/20260810-0945-ai-service-nat-source-fix`。

### 文档修改记录（2026-08-10 知识库管理切换私网 8000 直连）

- 修改原因：用户确认调用方为内网 `172.17.28.83`、jx 为 `172.30.157.197`，要求不再修改 Dify 原始规则，改走 `8000` 并完成验证。
- 修改前：4.10.2～4.10.8 经 Dify `:80/ai-service/` 桥接，受 Nginx 上传阻断和 Docker NAT 来源转换影响；本轮曾临时调整该桥接规则用于定位。
- 修改后：Dify Nginx 上传 location 和 guard 脚本均恢复到本任务开始前的硬 `403` 阻断状态。当前正式调用路径为 `172.17.28.83 → 172.30.157.197:8000/internal/v1/...`；ai-management 仍加入本机 Dify `docker_default` 网络，但其外部调用入口保持宿主机私网 `:8000`，两者不冲突。
- 验证：API 容器确认位于 `docker_default` 且 Dify Nginx 容器可访问其 `/health/live`；API 私网端口绑定为 `172.30.157.197:8000`；`AI-MGMT-8000` 精确允许 `172.17.28.83/32`，回程经 `eth0`；正确双 Token、无文件的直连上传探针返回 `422`，API ready。Dify Nginx `nginx -t`/热加载通过，备份为 `/opt/ai-management/backups/20260810-1000-direct-8000-switch`。

### 文档修改记录（2026-08-10 私网 8000 端到端超时待处理）

- 修改原因：调用方服务器实际执行 `curl http://172.30.157.197:8000/...` 后报 `curl (28)`、`HTTP_STATUS=000`，需要更正此前仅基于 jx 本机探针的结论。
- 修改前：当前状态将私网 `:8000` 描述为已验证通道，未明确区分 jx 本机/API 容器验证与调用方端到端验证。
- 修改后：明确当前为“目标通道”；jx `172.30.157.197:8000` 监听、Docker DNAT、`AI-MGMT-8000` 的 `172.17.28.83/32` 规则、回程路由均存在，但调用方测试期间该规则的 ACCEPT/DROP 计数均为 `0`。jx 可反向 ping `172.17.28.83`，因此服务、Dify、Token、jx 主机防火墙和基础 VPC 路由均不是此次超时点；待核查并放行阿里云 jx 入方向安全组 TCP `8000`（来源精确为 `172.17.28.83/32`），同时确认调用方实际出站源地址。
- 验证：待上述云侧规则生效后，从调用方复测 TCP 连接和带双 Token 的只读 GET；成功标准为 jx `AI-MGMT-8000` ACCEPT 计数增加且接口 HTTP `200`。本次未改动 Dify Nginx、数据库、Redis、Token 或业务数据。

### 文档修改记录（2026-08-10 商业顾问路线门禁发布）

- 修改原因：完成当前 DSL、核心服务和 Dify App 的发布，并记录环境变量切换未即时进入运行容器的问题及真实路线链路验收。
- 修改前：0.55 未记录本次路线门禁 release、隔离 Dify 应用、Key 指纹一致性、三轮 SSE 结果和快速发布共享包缺失问题。
- 修改后：新增 0.56，记录备份/报告路径、同源镜像、ready 与 `/parameters`、路线初问→路线确认→阶段确认的课程卡门禁结果，以及 DEPLOY-025 长期排障条目。密钥明文、真实用户标识和原始回答均未写入。
- 验证：本地相关 81 项测试全部通过；jx 三轮 SSE `passed=true`，首两轮 `course_cards=0`，第三轮 `5` 张卡且三轮 `message_end=completed`；运行容器与 `.env` Dify Key 指纹一致。

### 文档修改记录（2026-08-11 商业顾问精确阶段课程、专家见解与发布）

- 修改原因：用户要求修正已确认路线展示跨阶段/5 门截断、跨会话续问、无意义推荐理由和批次级专家见解，并授权发布 Dify、切换 jx 绑定及完成回归。
- 修改前：激活路线会把当前阶段与下一阶段合并后再截断为 5 张；Dify 无可解析决策时，服务端已保存的路线续问可能不出卡；正文和历史路径仍可能保留推荐理由字段。
- 修改后：新增 ADR-034/035 所定义的服务端权威阶段课程范围和状态兜底，Dify 使用权威上下文与 active Metadata 过滤；卡片后复用既有事件发送一条专家见解，旧推荐理由字段对外统一为空。当前 Dify 应用为 `4c157b1e-aa9d-49c2-99d2-a16ec9706886`，最终 jx release 为 `20260811-0912-route-resume-fix`。
- 验证：发布前资源门禁、可恢复备份和 wheel SHA-256 核验通过；发布后 API ready、Dify 参数 9 项、真实 SSE 精确当前阶段卡片/卡后单条见解/中性正文均通过，合成测试会话和路线进度均为 0。密钥、真实用户标识、原始正文和受保护映射未写入本文。

### 文档修改记录（2026-08-11 商业顾问发布合成测试数据复核与精确清理）

- 修改原因：用户要求确认今早测试是否残留数据，并从全链路删除本次测试数据且不影响已有正常数据。
- 修改前：已清理测试会话和路线进度，但未单独审计创建测试会话时自动落库的用户画像与操作审计事件。
- 修改后：以测试脚本固定前缀 `release-route-check-` 为唯一归属依据，先做只读联合盘点，再删除 3 条测试用户画像和 3 条 `actor_hash` 相同的应用审计事件；未删除课程、路线、知识库文档、Dify 应用、正常会话或全局 Redis 键。
- 验证：清理后本地测试前缀对应的 profiles/conversations/segments/messages/route progress/system events/external deletion tasks/Redis keys/Dify conversations 均为 `0`；API `/health/ready` 仍为 `ready`，database/Redis/Dify 均可达。

### 文档修改记录（2026-08-14 商业顾问移除课程/路线的安全发布准备）

- 修改原因：用户要求移除新商业顾问聊天的课程推荐和学习路线处理，并在 jx/Dify 完成可验证发布；发布中发现服务端 4 个 Start 输入与旧线上 Dify 应用的 9 个输入不兼容。
- 修改前：线上 0.57 服务与 Dify 应用共同保留课程目录、路线状态、课程卡片和专家见解处理。
- 修改后：本地与服务器发布资产已收敛为无课程/路线的新聊天实现；为避免未发布 Dify 时造成线上协议断裂，服务运行态已回到发布前版本，Dify 停在新应用创建前，尚未切换 App Key 或删除数据。
- 验证：备份中的数据库、Redis、上传卷和配置均已校验；新 release 构建/健康检查通过；恢复后 API `/health/ready=ready`、核心服务运行、OCR autoscaler `enabled/active`。待 Dify 发布后追加 `/parameters` 和真实 SSE 结果。

### 文档修改记录（2026-08-14 商业顾问移除课程/路线最终发布）

- 修改原因：完成此前“安全发布准备”中未完成的 Dify 新应用发布、App Key 切换、jx 服务重建和真实 SSE 验收。
- 修改前：0.58 记录的线上运行态仍是旧的课程/路线 Dify 与服务端组合；精简 DSL 尚未发布，不能代表当前线上行为。
- 修改后：新增 0.59，记录新隔离 Chatflow 发布、Dify 聚合变量 `result`→`output` 修复、完整备份 `20260814-business-general-2`、同源 release 切换、核心服务重建和运行时验收。旧 Dify 应用和数据均保留。
- 验证：Dify `/parameters` 仅返回 4 个 Start 输入；API ready，数据库、Redis、Dify 可达；真实 SSE `completed` 且无课程/路线/课程卡事件，测试会话按精确 ID 删除；入口配置 `course_recommendation=false`。密钥、真实用户标识、回答正文和受保护映射均未写入本文。

### 文档修改记录（2026-08-14 发布后 OCR 同源镜像补核验）

- 修改原因：最终镜像盘点发现核心和测试页已是本 release，但按需 OCR Worker 仍是旧的 exited 镜像，不能以 API ready 替代 OCR 同源核验。
- 修改前：0.59 只记录核心服务重建和 API 健康，未明确 OCR Worker 的实际镜像。
- 修改后：资源门禁通过后只重建 `ai-ocr-worker`；其镜像已为 `business-general-20260814-1` 且运行，autoscaler timer 仍为 active。未删除容器、卷、数据或 Redis。
- 验证：OCR、核心、测试页均使用 `business-general-20260814-1` 对应镜像；API ready，数据库、Redis、Dify 均可达。

### 文档修改记录（2026-08-16 Dify 1.13.3 动态 Metadata 条件最小验证）

- 修改原因：核验“Dify 1.13.3 只支持 DSL 中静态 metadata 条件”的说法，并按用户要求使用现有知识库做最小运行验证。
- 修改前：交接文档未记录本次 Dify 1.13.3 动态 Metadata 条件验证结果。
- 修改后：记录隔离的“动态 Metadata 最小验证”绑定现有 `general` 知识库，使用脱敏样本的 `source_title` Metadata。条件配置为 `source_title is Variable → 用户输入/filter_value`；输入已存在值时运行 `SUCCESS` 并返回目标文档内容，输入不存在值时运行 `SUCCESS` 且无文档命中。同步保留最小 DSL 报告 `var/reports/20260816-dify-dynamic-metadata-probe.yml`；受保护的应用、Dataset、文档和输入值不写入本文。
- 验证：Dify Chrome 登录态下完成两次测试运行；未修改正式应用、general 知识库文档或服务运行配置。由于 Chrome 本地文件上传权限被浏览器自动化策略阻止，DSL 文件未通过本地文件导入入口上传；隔离应用按同一 DSL 图配置完成运行验证。

### 文档修改记录（2026-08-16 最近活动清洗、jx 发布与 Dify DSL 发布状态）

- 修改原因：完成“最近活动”实现后的真实清洗、Metadata 回读、jx 发布和 Dify DSL 导入发布，并按项目 `AGENTS.md` 补齐当前交接、排障和权威功能文档。
- 修改前：文档只记录动态 Metadata 最小验证和历史商业顾问发布；没有记录 `recent_activity` 的正常/取消/延期清洗结果、最终 jx release、Dify 新 DSL 发布状态，也没有区分“Dify 已发布”和“jx 已切换 App Key”。
- 修改后：新增 0.60 当前状态；补充清洗说明、测试页操作说明、Dify 绑定指南、商业顾问 RAG 规范、功能索引和排障知识库。明确新版 Dify 已发布但 jx 仍待 App Key 同步，普通活动分析的旧路由负向验收未通过，feature 保持 `in_progress`。
- 验证：最终 jx release 报告、健康检查、清洗批次和数据库 Metadata 回读均有脱敏证据；本地质量门禁通过。未在文档中写入密钥、App ID、批次 ID、源正文、真实用户标识或受保护映射。

### 文档修改记录（2026-08-16 审查后脱敏与活动/案例意图优先级修复）

- 修改原因：独立审查发现历史动态 Metadata 记录仍含受保护应用/数据集/样本标识，并发现“最近会议真实案例”可能被活动分支抢占。
- 修改前：交接历史记录包含不应进入项目文档的资源标识；`quick_intent` 独立计算活动和案例意图，重叠问法的优先级未冻结。
- 修改后：历史记录改为只保留脱敏探针结果和报告路径；活动意图明确排除案例请求，两个重叠问法加入回归测试；统一 DSL 已重新生成。jx App Key 和最终真实 SSE 仍保持开放，不改变当前未完成结论。
- 验证：活动/DSL 聚焦测试 `30 passed`；完整治理质量门禁通过；独立审查 GO、P0/P1/P2 均为 0；治理 `gate-status` 为 `ready=true`。

### 文档修改记录（2026-08-16 App Key 生效与最终 Chatflow 验收）

- 修改原因：用户确认 App Key 已同步到 jx 并生效，继续完成此前开放的服务侧切换、案例重叠路由修复和最终测试。
- 修改前：0.60 只记录 Dify 已发布、jx App Key 未同步，以及旧 App 的普通活动分析误路由；最终 Chatflow 不能写成线上完成。
- 修改后：新增 0.61；记录 jx 容器 Key 指纹一致、`/parameters`/ready、当前 App 的 `quick_intent` 案例优先修复与发布更新、正常/取消/延期/普通业务/案例重叠测试结果，以及 10 个合成会话的精确清理结果。
- 验证：测试页活动四条主路径均显示最终答案，延期覆盖新日期和延期未定；案例重叠问题返回咨询案例；诊断回读确认 `message_end.status=completed`。文档未写入密钥、App ID、会话 ID、源正文、真实用户标识或受保护映射。

### 文档修改记录（2026-08-17 新对话交接）

- 修改原因：用户要求生成当前任务交接文档，并在新的 Codex 对话中继续。
- 修改前：本文最新状态为 0.61，虽然已记录功能验收，但没有单独说明治理完成状态、当前无待办以及新对话的读取入口。
- 修改后：新增 0.62 当前状态，集中记录最近活动实现、jx/Dify 生效状态、真实全链路测试、精确清理、质量门禁和新对话启动时必须读取的文档。
- 验证：未修改服务代码、jx 配置或 Dify 数据；仅更新本滚动交接文档，未写入密钥、App ID、会话 ID、真实用户标识或原始样本。

### 文档修改记录（2026-08-17 最近活动测试数据精确清理）

- 修改原因：用户反馈知识库仍显示前一日最近活动测试数据，要求按完整清洗链路删除，并核对 Redis、数据库和 Dify 状态。
- 修改前：0.62 只记录会话级精确清理，未记录知识库测试文档仍存在；本地清洗批次与 Dify 文档状态未做联合归零复核。
- 修改后：新增 0.63；记录 4 个测试批次、9 条本地逻辑文档和 8 个精确 Dify ID 的盘点与清理结果，区分执行前已不存在的 4 个 ID 与实际删除的 4 个 ID，并记录数据库、Redis、上传目录、健康检查和备份恢复校验结论。
- 验证：jx `health/ready=200`；本轮目标批次、文件、文档、分片、Dify ID 和精确 Redis 键均为 `0`；上传目录已移除；未删除正式 Dataset 文档、课程/路线、应用或持久卷。本文未写入密钥、App ID、会话 ID、真实用户标识、原始文件名或文档 ID。

### 文档修改记录（2026-08-17 朋友圈与日常写作案例目录实现）

- 修改原因：用户要求为朋友圈顾问和日常写作增加可点击案例、默认案例管理和接口文档，并在完成测试后部署 jx。
- 修改前：工具测试页只有自由输入；没有案例目录 API、管理接口或可维护的默认案例配置。
- 修改后：新增 kind 隔离的 `AdminSetting` 案例目录、用户读取与管理员 CRUD/恢复接口、Streamlit 案例查看/一键生成/维护 UI；同步接口交互版本、功能索引、Dify 运行时说明和 Feature 契约。DSL 未变更，当前未写入 jx 发布结果。
- 验证：专项案例/路由/输入契约测试、文档索引检查、format/lint/compile 通过；canonical 全套测试的 6 项既有缺失脚本/样本失败已记录在 `var/reports/20260817-tool-examples/issues.jsonl`，未归因于本 Feature。

### 文档修改记录（2026-08-17 朋友圈与日常写作案例目录 jx 发布）

- 修改原因：用户授权继续处理发布问题，并要求测试完成后部署 jx、记录问题、同步相关文档。
- 修改前：0.64 只记录本地实现和发布前状态；未记录 ZIP 路径兼容问题、服务器 wheelhouse/source 漂移、最终 release、回滚点和远端案例接口回归。
- 修改后：新增 0.65 当前状态，记录两次发布尝试的失败/修复结果、最终 jx release、备份/报告路径、OCR 进程级门禁、服务健康、案例 API/CRUD 回归和 DSL 未变更结论；0.64 标记为历史。
- 验证：最终 release 报告、容器状态、`health/ready=200`、测试页 `HTTP 200`、用户/管理员案例接口和临时 CRUD 烟测均通过；未写入密钥、真实用户标识或受保护映射。

### 文档修改记录（2026-08-18 最近活动最终修正、发布与回归）

- 修改原因：完成独立审查提出的版本摘要白名单和活动状态边界修正，并按用户要求完成本地质量门禁、jx 发布、同名活动更新回归和精确测试数据清理。
- 修改前：版本摘要仍可能受未登记公共 Metadata 影响；交接文档中的质量指纹和 release 仍指向修正前发布；最终远端测试批次尚未记录清理结果。
- 修改后：版本摘要仅使用活动业务 Metadata 白名单；最终质量指纹、jx release、报告和备份已同步；合成 v1/v2 验证新版本先发布、旧版本不可见，清理仅针对本轮两批次及其精确 Redis/上传目录，正式课程和路线保留。
- 验证：目标批次、文件、文档、分片、外部删除任务和 Redis 键均为 0，空上传目录已移除；`health/ready` 的数据库、Redis、Dify 配置与可达性均为 `true`；DSL 未变更，无需同步 Dify。未写入密钥、真实用户标识、原始样本或受保护映射。

### 文档修改记录（2026-08-18 千问 Batch 清洗验收按用户指令取消与精确清理）

- 修改原因：用户要求停止当前异步 Batch 验收，恢复测试前默认配置，并清除本轮清洗生成的知识库、Redis、数据库数据。
- 修改前：jx 为 `quick-20260818-113801-b974c5d8b95d`；测试临时开启 `BAILIAN_BATCH_ENABLED=true` 和阈值 `1`；本轮已创建 9 个 general 清洗批次，4 个供应商 Batch 仍在处理中，远端暂存样本副本存在。
- 修改后：4 个活动供应商 Batch 已通过取消接口停止，9 个本轮 general 清洗批次、4 个 general Dify 文档和精确清洗 Redis 键已按签名 manifest 清除；临时样本副本和临时脚本已移除；`.env` 已从 `/opt/ai-management/backups/qwen-batch-cleaning-20260818-1145/env.backup` 恢复，`BAILIAN_BATCH_ENABLED=false`，临时阈值未保留。
- 验证：清理后 general 清洗批次为 `0`、general Dify 文档为 `0`、`ai:cleaning:batch:*` Redis 键为 `0`；课程 `126`、路线 `5`、方向 `0` 保持不变；API `health/ready` 的数据库、Redis、Dify 配置和可达性均为 `true`。本轮 11 个样本未全部完成终态，不能将其记为完整验收；脱敏问题记录和备份证据保留在本地/服务器报告目录。DSL 未变更，无需同步 Dify。

### 文档修改记录（2026-08-18 千问 Batch 独立复核结论）

- 修改原因：用户取消本轮后，独立只读审查要求确认是否可以把当前实现和远端清理记为完整 GO。
- 修改前：本轮清理和默认配置恢复已完成，但治理状态仍需独立审查；临时取消/manifest 脚本只为本轮操作服务。
- 修改后：审查结论为 `NO-GO`，开放问题包括 `cancelling` 供应商状态终态语义、提交与取消竞态、临时取消脚本/manifest 的范围证明不足，以及部分生成状态仍显示 verified；这些问题已写入 `var/reports/20260818-qwen-batch-cleaning/issues.jsonl`，本次按用户停止指令不再修改代码。
- 验证：本地最终质量门禁通过；远端 general/Redis/数据库清理和默认配置恢复仍已独立核验，但 11 样本完整 Batch 验收未完成，不能宣称本 Feature 完成或所有审查问题已修复。DSL 未变更。

### 文档修改记录（2026-08-19 咨询会议方法论 v3 页面验收补录）

- 修改原因：按项目 `AGENTS.md` 补录本轮 18 份样本的页面上传、清洗、Metadata、Dify 入库、商业顾问测试和部署排障结果。
- 修改前：本文没有咨询会议 v3 的当前状态、候选方法/通用方法论晋升结果，也没有记录 Dify 日志节点级证据边界。
- 修改后：新增 0.70 当前状态，并同步 `docs/清洗策略接入说明.md`、`docs/Dify创建导入绑定与契约测试.md`、`docs/README.md`、`docs/功能与接口索引.md`、排障知识库和脱敏验收报告；只写脱敏计数、状态和报告路径，不写入配置明文、Token、原始正文或原始敏感文件名。
- 验证：18/18 文件成功、31 个 Dify 文档 ID、13 个候选方法、0 个通用方法论；Dify 日志查询均为 `Success`；jx ready、迁移、运行时导入和 Worker 稳定性检查通过；`gov quality` 通过，独立审查仍因外部 Agent 不可用而未取得结论。

### 文档修改记录（2026-08-19 文档清洗策略熔断、OCR 修复与 jx 回归）

- 修改原因：完成课程误入口、咨询批次模型契约失败、咨询 PDF 图片隐私门禁误报的根因修复，并按用户确认完成测试、部署、课程域清理和线上回归。
- 修改前：本文没有记录课程策略创建前熔断、存量 Worker 熔断、咨询 PDF 含图统一 OCR、指定批次重试成功、7 条历史误报事件收敛和课程域旧数据清理结果。
- 修改后：新增 0.71 当前状态；补充 release、备份、报告、精确清理范围、线上探针、批次和 PDF 事件结果，明确 general 数据保留、DSL 未变更和发布过程中的环境问题处理。
- 验证：jx `health/ready`、运行时导入、课程写入 `409` 探针、课程清理 dry-run、咨询批次数据库回读和 PDF 事件精确复核均通过；未写入密钥、Token、源正文、真实用户标识或受保护映射。

### 文档修改记录（2026-08-19 ai-management 系统事件日志精确清理）

- 修改原因：按用户要求确认并执行 ai-management 日志清理，记录既有脚本范围、备份、删除计数、恢复异常及最终线上状态。
- 修改前：本文最新状态为 0.71，未记录本次系统事件日志清理和恢复过程。
- 修改后：新增 0.72；明确仅清理 `system_event_log`，记录 `227 → 0`、备份位置、服务恢复和未触碰的其它日志/业务数据边界；脱敏报告见 `var/reports/20260819-clear-system-event-logs/README.md`。

### 文档修改记录（2026-08-20 活动语义路由与 Qwen 请求一致性修复）

- 修改原因：本轮独立审查发现活动近期查询未命中时间过滤分支、详情问法未进入活动路由、命名详情会同时展示多个候选、列表上限不可配置，以及验收命名规则与生产函数可能漂移；同时需要补齐 Qwen 请求稳定参数。
- 修改前：近期窗口输出 `recent` 但状态路由只匹配 `normal`；活动意图依赖“活动/会议”固定词和有限列表词；上限在 Code 中硬编码 10；验收脚本自行拼接长标题和分片名称；请求体没有固定 seed。
- 修改后：活动实体扩展到沙龙/工作坊/训练营等自然表达，详情/报名/序号/指代优先于“怎么”分析负向；近期默认输出 `normal` 并进入动态时间过滤，已结束独立输出 `completed`；新增 Dify `ACTIVITY_MAX_RESULTS` 环境变量（默认 10、限制 1～20），检索 top_k 提升到 50，命名详情按名称/序号只返回一个候选；验收复用 `dify_activity_document_name` 和生产 `dify_document_name(source_title, ..., split=True)`；Qwen realtime/Batch 共用 `temperature=0.01`、`enable_thinking=false`、`seed=1234` 和 JSON 输出策略。
- 验证：活动/统一 DSL 13 项、清洗发布契约 23 项、Ruff 和 DSL 生成检查通过；大模型清洗 39 项已完成并全绿。新的 jx release 已上传到临时 stage，待治理质量、独立审查和 Dify 登录后的发布验证完成后切换；既有测试样本和 Batch 证据保留在 `var/reports/20260819-qwen-activity/`，未删除。

### 文档修改记录（2026-08-20 活动与清洗分片最终修复、jx 发布及 Batch 验收进行中）

- 修改原因：修复独立复审发现的模型完成后增量文档未规划、重复摘要唯一键冲突、失败分片误去重、重复发布 claim，以及 SQLite watchdog 时区比较异常；按用户确认完成本地质量、独立复审和 jx 部署，并保留测试数据继续做真实 Batch 验收。
- 修改前：reconcile 可能在模型仍生成子文档时提前结束；后生成相同内容可能使整文件规划回滚；失败但未入库的分片可能被错误排除；watchdog 在 SQLite 测试环境可能抛 naive/aware datetime 异常；旧 release 尚未包含最终修复。
- 修改后：按文件行锁增量规划，成功/待发布/索引中内容按最终 Markdown 摘要去重，失败同摘要分片复用并重置为 pending；发布 worker 使用 pending-only claim，替换远端 ID 和补偿删除目标持久化；watchdog 统一 UTC 并在行锁后复检。jx 已切换到 `20260820-qwen-activity-5eb267ef-fix4`，wheel SHA 为 `fae25f9e300fc9f8c837bf8a795b862ec6f2a09e9aaebe082ed8f4f9677fc122`，备份和发布报告保留在 `/opt/ai-management/backups/20260820-qwen-activity-fix4` 与 `/opt/ai-management/var/reports/v2/20260820-qwen-activity-5eb267ef-fix4-deploy`。
- 验证：本地 `gov quality` 通过（指纹 `b141a10a0b65a26fb32551e176d5d043c818a0dfeefcb66a975854d2a9302950`），独立复审对分片/claim 修复通过；jx `health/ready` 的数据库、Redis、Dify 配置和可达性均为 true，实时配置已恢复 `BAILIAN_BATCH_ENABLED=false`、候选阈值 100、token 阈值 200000、上传上限 10。真实 zsxq 增量测试中 198 个文档最终 196 个成功、2 个明确重复排除，196 个分片全部 succeeded，无未分配孤儿；测试数据未删除。四策略真实 Batch 已启动，首个咨询策略为 100 候选、`mode=batch` 且 provider batch id 存在，但供应商仍为 `in_progress`，其余策略及 realtime/Batch 全量最终 KB parity 尚未宣称完成；报告和任务保留待终态复核。最新 Dify DSL 发布仍需浏览器导入权限/发布证据，不能以旧 SSE 报告替代。

### 文档修改记录（2026-08-20 最终质量与外部验证边界补录）

- 修改原因：补录 fix4 发布后最终质量重跑、Batch 供应商延迟和 Dify URL/文件导入的实际排障证据，避免把未完成的外部终态误记为通过。
- 修改前：质量指纹仍为前一轮；Dify URL 导入的 SSRF 超时和 Chrome 文件 chooser 权限阻塞尚未写入；Batch 只记录启动，没有明确当前运行配置已恢复。
- 修改后：最终 `gov quality` 指纹为 `634bdc06d836fedc659de93af53fbd9fb4797d6af795c7c883baed2b1edf7026`；Dify API 日志确认 SSRF proxy 对临时 URL 四次超时，Chrome 文件 chooser 仍要求开启 ChatGPT 扩展的 “Allow access to file URLs”，临时 HTTP 服务已停止且 DSL 临时文件已移除；jx 服务恢复默认实时配置，已提交 Batch 任务继续保留轮询。
- 验证：以上阻塞均有本地/远端可复核日志；不宣称最新 DSL 已发布，也不宣称四策略 realtime/Batch parity 已完成。测试数据、Batch provider 任务、备份和发布报告均保留，未删除业务知识库数据。

### 文档修改记录（2026-08-20 最终质量复核指纹补录）

- 修改原因：独立复审发现状态文档仍引用上一轮质量指纹，需要与当前最终质量证据一致。
- 修改前：最新记录为 `634bdc06d836fedc659de93af53fbd9fb4797d6af795c7c883baed2b1edf7026`。
- 修改后：当前 `gov quality` 状态为 `passed`，指纹更新为 `63974947e43bb6cf735ca101ec03c934c50279145b5adc532f30e82ae55b7a39`；代码复审通过。四策略 Batch 最终终态与最新 Dify DSL 发布仍是外部待验收项，未改变其未完成边界。
- 验证：质量门禁、独立复审和分片定向回归均通过；测试数据、Batch provider 任务、备份与发布报告继续保留。

### 文档修改记录（2026-08-20 四策略 Qwen realtime/Batch 终态与最新 DSL 外部验收）

- 修改原因：继续完成用户批准的四个认知分享策略 realtime/Batch 对照验收、最新活动 DSL 发布验证，并把外部终态证据纳入交接，避免继续沿用“进行中”的旧状态。
- 修改前：咨询策略已有 Batch 进度记录，其余三策略及四策略最终 KB parity 尚未形成终态证据；最新 DSL 已生成但未记录已发布应用；jx 默认配置需恢复为实时模式。
- 修改后：四策略均完成真实 Batch 与 realtime 任务。咨询 Batch/realtime 均为 100/100；微信公众号 Batch/realtime 均为 101/101；小报童 Batch 为 101 候选、202 个本地成功文档，realtime 为 101 候选、186 个成功文档；知识星球 Batch 为 101 候选、202 个本地成功文档，realtime 为 101 候选、192 个成功文档。四策略本地结果均为 `succeeded`、均有 Dify 文档 ID，动态字段剔除后的 metadata key set 一致；各策略 realtime/Batch 均抽样回读 Dify，索引状态为 `completed`。输出文档数量差异来自模型分片，不是失败、未分配或孤儿文档。
- 最新 DSL 已在登录 Chrome 中导入并发布，SHA 为 `de8394a2570c877e64f3c18452f74d632de525438566354a45fb1875077c4e2b`，独立 Chatflow App ID 为 `e178fae2-d6d2-4fe1-8424-c1efd20f73df`；“最新活动有哪些”“最近活动有哪些”均返回紧凑活动列表，指定活动详情返回详情。jx 默认 `DIFY_BUSINESS_APP_API_KEY` 未切换到该新 App，避免未经确认改动现有 API 入口；本项只能表述为“最新 DSL 已发布并验证”，不能表述为“jx 默认商业顾问 API 已切换”。
- jx 默认配置已恢复为 `BAILIAN_BATCH_ENABLED=false`、上传上限 10、Batch 候选阈值 100、Token 阈值 200000；备份、任务、测试数据和知识库结果均保留，未删除历史/测试数据。
- 验证：四策略供应商任务均为终态 `completed` 且成功数与候选数一致；本地 metadata/Dify 抽样回读报告保留在 `var/reports/20260820-qwen-activity/`；浏览器发布与查询验证完成。外部 Dify 新 App 与 jx 默认 API 入口的切换仍是待用户确认的独立发布动作。

### 文档修改记录（2026-08-20 外部验收后治理质量复跑）

- 修改原因：外部验收证据补录后重新执行项目级质量门禁，更新当前质量指纹和治理阶段，避免继续引用旧质量结果。
- 修改前：交接已记录四策略终态和 DSL 发布，但质量指纹仍为前一轮值。
- 修改后：项目级 `gov quality` 通过，当前指纹为 `76b897cd60d972b172354da8f5852dae45c1242a24b5cc1b459f5ce9dba15e3c`；独立复审状态已按当前代码和文档同步，治理门禁为 `READY`。
- 验证：格式、Lint、编译、全量单元/集成测试、Qwen/Dify E2E 检查均通过；未修改 jx 默认 Dify API 入口，未删除测试数据、Batch 任务、知识库结果或备份。

### 文档修改记录（2026-08-20 四策略全量远端 metadata 回读复核）

- 修改原因：用户要求确认模型 Batch 与 realtime 最终入库效果及 metadata 是否一致；此前仅有抽样证据，需要对本轮四策略所有已关联 Dify 文档做只读回读。
- 修改前：交接只记录抽样 Dify `indexing_status=completed` 和 metadata key set 一致，未区分最终远端 metadata 与本地中间文档 metadata。
- 修改后：四策略全部已关联 Dify 文档逐一回读通过，Batch/realtime 每组的远端 metadata key set 完全一致，必需字段齐全，索引状态全部 `completed`。但本地中间态并非完全一致：小报童 realtime 有 16 个、知识星球 realtime 有 10 个文档缺少可选 `topic/unit_title/knowledge_unit_id`；这些字段当前不在发送到 Dify 的 metadata 集合中，因此不影响最终知识库 metadata。小报童和知识星球的 Batch/realtime 文档数量仍因模型拆分差异而不同，不能表述为逐文档内容完全一致。
- 验证：脱敏全量证据见 `var/reports/20260820-qwen-activity/full_remote_metadata_parity.safe.json`；未修改业务数据、测试任务或 Dify 文档。

### 文档修改记录（2026-08-20 模型契约失败审批与跨源去重修复）

- 修改原因：落实用户确认的原始文件名前缀、同契约跨源去重和模型结果未采用人工审批要求。
- 修改前：模型契约失败会自动恢复源 Markdown 并继续入库；模型标题可能覆盖来源名称；重复保护不足以覆盖跨源/跨批次。
- 修改后：条目进入 `review_required` 并生成服务端审核产物，approve 后才发布；Dify 名称固定原始 stem；同知识库/策略/规格按规范化最终正文去重并加锁。未写入密钥、Token、源正文或受保护映射。
- 验证：本地聚焦、全量单元/集成回归及文档索引已通过；jx 清理、部署和真实样本验收将在本地质量门禁完成后执行。

### 文档修改记录（2026-08-20 质量门禁重跑与发布准备）

- 修改原因：接管中断会话后，按当前工作树重新执行质量门禁并准备后续 jx 发布，修正状态文档顶部过期状态和质量指纹。
- 修改前：文档顶部仍将 0.73 标为当前最新，质量记录引用旧指纹，未记录当前索引修复和本地 wheel 构建结果。
- 修改后：0.73 标为历史，0.74 保留为当前开发状态；补录当前 `gov quality` 通过指纹、索引修复和 wheel SHA-256；jx 清理、部署、远端 Batch/样本验收仍明确为未完成。
- 验证：`gov quality` 全部阶段通过；wheel ZIP 完整性与关键模块闭包检查通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 接管后最终质量指纹校正）

- 修改原因：接管中断会话后，发现顶部状态仍引用 Batch 修复前一轮质量指纹，需要与 `.codex/state/quality.json` 的最终结果一致。
- 修改前：顶部和最近 Batch 修复记录引用 `6222e8c21dc8464c014a02c4facc4b25f4d06bd9f608643ec1cada2ed5c17965`。
- 修改后：更新为最终工作树质量指纹 `afa4716952c453af615532c136fc2c9b2845f61fef97aecd5d8087bfb8400479`；代码、wheel、远端未发生变化。
- 验证：仅同步动态状态记录，未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 最终质量指纹更新）

- 修改原因：文档同步后按仓库规定重新执行 Windows 全量 `gov quality`，需要把当前状态更新为最后一次实际结果。
- 修改前：顶部仍记录上一轮 `afa4716952c453af615532c136fc2c9b2845f61fef97aecd5d8087bfb8400479`。
- 修改后：更新为最终质量指纹 `6e8fe21bfb9ba15948d1554c7559e7faaed1a170c314166334e2986883473a56`；测试、代码、wheel 和远端范围均未改变。
- 验证：格式、Lint、编译、全量单元、第二组契约测试、集成和 Qwen E2E 均通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 取消竞态与审核请求指纹补修）

- 修改原因：独立复核确认取消接口缺少任务行锁、轮询修复 Batch 的取消恢复分支未执行清理，且审核 `request_sha256` 未纳入模型/Prompt 上下文。
- 修改前：取消与轮询可能交错写入状态；修复 Batch 异常恢复只按 truthy 结果重新排队；审核指纹仅由标题、Metadata 和源摘要组成。
- 修改后：取消/轮询状态边界使用任务行锁；`recovered == "cancelled"` 统一精确取消供应商 Batch 并删除已知文件；审核请求指纹纳入请求块、模型和 Prompt 版本，并增加恢复清理与指纹回归测试。
- 验证：聚焦清洗测试 5 项通过，Ruff、compileall、`git diff --check` 通过；文档同步后按仓库规定执行最终 `gov quality`，结果以 `.codex/state/quality.json` 为准；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 最终复核边界补修）

- 修改原因：最终独立复核发现恢复远端 Batch 的状态读取仍缺少行锁，以及供应商无响应/终态失败时请求上下文可能未进入审核指纹。
- 修改前：恢复函数可能在取消提交后以旧状态复活任务；失败路径的审核指纹可能退回标题、Metadata 和源摘要。
- 修改后：恢复函数使用任务行锁后再决定 `cancelled`/`batch_running`；实时和 Batch 在每次供应商调用前持久化请求块、模型、Prompt 版本和修复提示指纹，失败路径可复用该上下文。
- 验证：恢复不复活、取消恢复清理、审核 payload 和供应商失败请求指纹聚焦回归通过；Ruff、compileall、`git diff --check` 通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 修复 Batch 上传创建边界补修）

- 修改原因：最终复核发现修复 Batch 在写文件提交后到上传/创建/最终切换之间仍可能绕过取消复检；补修后聚焦回归暴露修复任务正常状态还包括 `validating`。
- 修改前：修复 Batch 只有初始提交路径具备完整取消边界；若按仅 `batch_running` 检查会错误中止现有 100 候选修复流程。
- 修改后：修复 Batch 在每次上传前后、创建前后和最终切换前锁任务行并复检 `batch_running/validating`；取消获胜时精确清理已创建资源且不写回 `batch_running`。
- 验证：100 候选修复 Batch、取消恢复、恢复不复活和供应商失败请求指纹聚焦测试通过；Ruff、compileall 通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 取消终态与清理重试闭环）

- 修改原因：最终复核进一步发现 validating 阶段取消未被明确保护、孤儿 Batch 取消失败后没有可重试任务，以及正常取消成功后仍保留已清理外部 ID。
- 修改前：validating 期间可能与结果处理交错；Batch cancel 失败只保留本地 pending 状态；取消响应仍暴露已清理的供应商 ID。
- 修改后：validating 允许进入取消流程，轮询在解析失败/最终提交/完成切换前重新锁行复检；Batch cancel 失败建立精确 `bailian_batch` 重试任务并由维护 Worker 执行；取消完成后清空供应商 Batch/文件 ID。
- 验证：取消失败重试、取消恢复、修复 Batch、恢复不复活和审核请求指纹聚焦测试通过；Ruff、compileall、`git diff --check` 通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 看门狗与 Batch 重试尾项闭环）

- 修改原因：最终复核发现维护看门狗可能与取消交错复活任务，以及 `bailian_batch` 取消重试未继续处理供应商返回的 output/error 文件。
- 修改前：看门狗查询活跃任务后直接写失败并可能重新排队；Batch cancel 重试成功后只删除重试任务，不处理返回文件。
- 修改后：看门狗查询使用任务行锁，已取消任务不会被终止或重新规划；Batch cancel 重试精确删除返回文件，失败则建立 `bailian_file` 重试任务。
- 验证：取消看门狗不重排、Batch 重试返回文件清理、取消失败重试和修复 Batch 聚焦测试通过；Ruff、compileall 通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-20 审核材料失败收敛与修复请求审计补强）

- 修改原因：最终复核发现审核 artifact 超限/权限异常可能让 Batch 任务停留在 `validating`，以及修复请求成功后会覆盖原有 `repair_hint` 指纹。
- 修改前：artifact 写入异常直接外溢；失败任务可能不再重排也不进入人工复核；修复后的请求上下文缺少 repair hint 指纹。
- 修改后：artifact 写入失败转为受控 `review_required`、不自动发布且清除原始暂存 payload/context；`_apply_model_payload` 保留既有 repair hint 指纹。
- 验证：artifact 写失败收敛、供应商失败请求指纹、修复 Batch、取消失败重试和看门狗聚焦测试通过；Ruff、compileall 通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 接管中断会话后的远端清理竞态补修）

- 修改原因：接管会话 `01a01da1-4fcd-7043-be00-2b6625c99228` 后的独立复核发现，Batch 提交异常恢复失败、维护看门狗终止带远端 ID 的任务、validating 阶段小批量修复调用以及活动跨 event_id 发布仍存在资源泄漏或并发窗口。
- 修改前：提交恢复失败路径可能丢失供应商返回的 output/error 文件；看门狗可能重新规划已有远端 Batch；小批量修复在供应商调用前后缺少 validating 状态复检；活动只按 event_id 加锁，两个不同活动可能并行竞争同一契约级去重范围；审核 artifact 未单独记录修复提示摘要。
- 修改后：提交异常统一精确取消并清理已创建 Batch/文件；看门狗取消并清理所有已知远端资源，失败项建立 `bailian_batch`/`bailian_file` 重试任务且不立即重复提交；小批量修复调用前后锁任务行复检；活动发布增加知识库/策略/规范级锁；artifact 增加 `repair_hint_sha256`，不保存提示正文。新增看门狗远端清理和修复提示摘要回归测试。
- 验证：相关 Ruff、compileall、`git diff --check` 和聚焦模型清洗测试通过；项目级 `gov quality` 将在本轮文档同步后重新执行。未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 提交失败清理状态保持补修）

- 修改原因：补查提交异常的本地兜底分支，发现恢复清理已进入 `pending_retry` 时，后续空文件清理可能把该状态覆盖为 `completed`；未进入恢复分支的已持久化供应商 Batch 取消失败也需要建立精确重试任务。
- 修改前：取消/文件删除失败的补偿状态可能被后续兜底覆盖，或仅记录日志而没有 `bailian_batch` 重试任务。
- 修改后：文件清理只在没有既有待重试状态时写入 `completed`；持久化 Batch 的取消响应会收集返回文件 ID，取消失败建立精确 Batch 重试任务并保留 `pending_retry`。
- 验证：模型清洗聚焦测试 9 项、Ruff 和 `git diff --check` 通过；项目级 `gov quality` 将在本轮文档同步后重新执行。未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 独立复核 P1 生命周期闭环）

- 修改原因：独立复核发现多 Batch 终态文件、提交异常取消竞态和 validating 修复后的 `shard_pending` 收敛仍有缺口；同时按人工审批幂等要求补查重复审批和实时契约重试并发。
- 修改前：终态 Batch 的 output/error ID 只留在 `result_counts`；提交异常最终兜底未锁任务行；取消只处理 `awaiting_model/model_processing`；重复审批可能重复进入冲突，实时精确重试读取未加行锁。
- 修改后：终态文件 ID 同步持久化到任务字段，取消/看门狗兼容读取历史摘要；提交异常锁行复检并保持 `cancelled`；取消将待发布分片转为明确人工复核；相同候选审批返回稳定结果，重复复核按请求摘要幂等，实时重试锁定任务/文档；旧分片无 Markdown 时不再把未知旧摘要当作当前正文摘要。
- 验证：模型清洗与人工管理聚焦测试通过，Ruff、compileall、`git diff --check` 通过；项目级 `gov quality` 和新的独立复核将在本轮文档同步后重新执行。未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 旧 Batch ID 兼容与质量范围补全）

- 修改原因：最终复核列出旧任务仅保存单数 `provider_batch_id` 时的兼容缺口，以及 canonical format/lint 未覆盖本轮核心 worker/test。
- 修改前：部分历史任务若只有单数 Batch ID，取消、看门狗或轮询可能漏掉供应商 Batch；核心模型 worker 和工作流测试未纳入 canonical format/lint。
- 修改后：统一兼容单数与复数 Batch ID 读取，并在轮询时归一化历史任务；管理端展示、取消和看门狗复用同一兼容逻辑；核心模型源码与工作流测试纳入 canonical format/lint。
- 验证：旧单数 Batch ID 取消回归、模型清洗聚焦测试、Ruff format/check 和 `git diff --check` 通过；项目级 `gov quality` 和新的独立复核将在本轮文档同步后执行。未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 看门狗远端终态对账补修）

- 修改原因：最终独立复核发现看门狗仅依据本地更新时间直接取消并删除远端 Batch，可能在供应商已完成时丢失 output/error 文件并留下不可恢复的本地任务。
- 修改前：带远端 ID 的 stale task 不查询供应商终态，统一取消/删除并置为 failed；即使存在已完成的历史 Batch 摘要，也可能删除其结果且不重新排队。
- 修改后：看门狗先查询每个已知 Batch 状态；查询失败保留远端 ID 并安排轮询，已完成或已有终态摘要保留结果文件并进入现有轮询/结果处理链；仅全为活动状态时才精确取消清理，清理成功后文档可重排，清理失败保留精确重试任务。
- 验证：看门狗终态对账回归、模型清洗聚焦测试、Ruff format/check 和 `git diff --check` 将在本轮修复后重新执行；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 看门狗终态对账与质量门禁复核完成）

- 修改原因：完成独立复核指出的 P1 修复，并重新执行文档索引与项目质量门禁。
- 修改前：看门狗修复后的状态对账和质量范围尚未完成最终回归，自动接口索引在最后一次 Worker 变更后待生成。
- 修改后：已完成远端 Batch 状态对账、结果保留、活动 Batch 清理后重排和查询失败保留引用；canonical Ruff 覆盖本轮变更的核心源码/测试路径；接口索引已重新生成。
- 验证：模型清洗工作流全量聚焦测试通过；项目 `gov quality` 的 format、lint、compileall、两组单元测试、集成测试和 Qwen 活动 E2E 全部通过；未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 看门狗恢复状态机最终收敛）

- 修改原因：复核继续发现仅在看门狗内查询后取消仍存在终态/活动混合、取消响应竞态、`validating` poll 入口拒绝和重复 planner 唯一键冲突。
- 修改前：看门狗仍可能自行 cancel/delete；部分状态交回 poll 后未规范为 `batch_running`；无远端 ID 的 realtime/batch 任务通过新 planner 重建，可能与原任务候选唯一键冲突。
- 修改后：所有仍有 Batch ID 的 stale task 统一归一为 `batch_running` 并交给现有 poll 处理；看门狗不再自行销毁供应商结果；无 Batch ID 的 realtime 复用原任务行重排，batch 复用原任务回到待批准，避免重复任务键。
- 验证：模型清洗工作流全量聚焦测试、看门狗 stale 状态参数化回归、Ruff check/format 和 `git diff --check` 通过；项目级质量门禁将在本轮文档同步后重新执行。未执行远端写入、清理或发布。

### 文档修改记录（2026-08-21 Batch 终态结果恢复与新数据范围补充）

- 修改原因：独立复核发现 Batch 全终态后的本地 finalize 崩溃和输出下载瞬时失败可能丢失供应商结果；用户进一步明确旧目标数据删除不备份，删除后只处理后续新上传数据。
- 修改前：全终态任务清空 Batch ID 后缺少可恢复入口；输出文件可能在本地结果提交前被删除；专项需求仍以“测试前历史清理”表述范围。
- 修改后：持久化终态 Batch 映射并让 watchdog/poll 可恢复；输出下载失败保留远端引用重试；本地结果事务提交后才执行供应商文件清理；专项需求改为精确删除旧目标数据、不建备份、只处理后续新数据。未在交接文档写入原始敏感文件名或路径。
- 验证：模型清洗全量工作流测试、两个新增 P1 回归、Ruff format/check 通过；canonical `gov quality`、文档索引、独立复核和远端清理/发布仍待本轮继续完成。

### 文档修改记录（2026-08-21 修复批次切换与 cancelling 清理回归）

- 修改原因：独立复核继续发现修复 Batch 切换后仍可能保留旧终态映射、供应商 `cancelling` 状态可能被过早清理，以及 `recent_activity` 专项仍要求重新上传历史样本；随后窄测发现修复 Batch 状态保存代码位于不可达 `return` 后。
- 修改前：修复批次可能再次读取已删除的旧输出；取消/维护清理可能在远端仍为 `cancelling` 时清除引用；相关专项文档与“只处理后续新数据”范围冲突；修复 Batch 的新 ID 和 `retry_round` 未可靠持久化。
- 修改后：修复 Batch 切换时清除旧终态映射并持久化新 Batch 状态；终态结果完成本地事务后移除恢复映射；取消/维护清理对 `cancelling` 保留精确重试任务与远端引用；`recent_activity` 改为旧目标数据精确删除、不备份、删除后只处理后续新数据；同步相关专项文档并重新生成接口索引。未在交接文档写入原始敏感文件名或路径。
- 验证：修复 Batch 100 条候选回归、管理员取消 `cancelling` 回归、维护清理 `cancelling` 回归均通过；Ruff format/check 通过。canonical `gov quality`、新的独立复核和远端清理/上传/发布仍待本轮继续完成。

### 文档修改记录（2026-08-21 终态收敛与远端清理顺序修复）

- 修改原因：全量单测发现终态映射清除过早，且本地 `_finish_model_task` 失败时远端输出可能已先被删除。
- 修改前：成功终态处理未保留预期的终态审计映射；本地结果事务提交后先清理供应商文件，再执行本地任务最终收敛，存在 finalize 失败后无法重读输出的窗口。
- 修改后：成功处理保留终态映射，修复 Batch 切换时清除旧映射；顺序调整为本地结果事务提交、任务最终收敛、供应商文件精确清理，finalize 失败时保留远端资源和恢复信息；同步回归断言和专项文档。未在交接文档写入原始敏感文件名或路径。
- 验证：相关模型清洗回归 4 项通过，Ruff format/check 通过；canonical `gov quality`、新的独立复核和远端清理/上传/发布仍待本轮继续完成。

### 文档修改记录（2026-08-21 清理补偿与取消崩溃安全修复）

- 修改原因：独立复核发现 Batch 修复轮次切换、终态后 publication 入队、管理员取消补偿、`cancelling` 处理和清理队列公平性仍有 P1 生命周期缺口。
- 修改前：新修复 Batch 可能与旧终态映射混读；终态本地任务已完成但 publication 入队异常时可能没有远端文件清理计划；取消在补偿 work item 落库前崩溃会遗失清理；一条孤儿 Batch helper 会提前清空 `cancelling` 引用；大量长期失败任务可能占满清理窗口。
- 修改后：新增共享 Bailian 精确清理 work item 服务；新 Batch ID、旧终态映射清除和旧文件清理任务在同一事务切换；终态结果提交前建立文件清理计划；取消本地状态提交前建立 Batch/文件补偿并由 maintenance 对账重建；所有取消入口只在供应商终态后删文件；清理按 kind 轮转并在连续失败后进入可观测人工处理状态。未在交接文档写入原始敏感文件名或路径。
- 验证：新增的取消 helper、取消对账、清理公平调度、人工阻塞回归与既有 7 项 Batch/取消回归均通过；Ruff format/check 通过。canonical `gov quality`、新的独立复核和远端清理/上传/发布仍待本轮继续完成。

### 文档修改记录（2026-08-21 取消异常引用保留断言校正）

- 修改原因：全量单测发现一条历史回归仍断言取消 RPC 异常后清空远端 Batch ID，与本轮 P1 修复的“保留引用并补偿清理”语义冲突。
- 修改前：取消异常测试要求 `provider_batch_ids` 为空，无法验证远端资源可追踪。
- 修改后：测试改为要求保留精确 Batch/input ID，并确认 `bailian_batch` 补偿任务存在；实现保持不变。未在交接文档写入原始敏感文件名或路径。
- 验证：该回归、清理生命周期新增回归和 Ruff format/check 通过；canonical `gov quality` 需在本次断言校正后重跑，远端清理/上传/发布仍未执行。

### 文档修改记录（2026-08-22 jx 千问 Batch 配置变更前核对）

- 修改原因：用户将范围补充为 jx 服务器，需要把本地策略改为 jx 远端目标，并记录实际只读核对结果，避免把本地 Docker 缺少 Dify 外部网络误当成 jx 故障。
- 修改前：本文只记录本地 `.env` 目标，且写成“不发布 jx”；未记录 jx 当前的 100 文件/500MB/Batch 关闭状态、服务清单和价格快照前置条件。
- 修改后：补充 jx 当前配置差异、同源服务与网络证据、远端写入待备份/精确命令确认的状态；未写入任何密钥、Token、真实用户 ID 或原始敏感文件名。
- 验证：只读 `ssh jx` 预检显示核心服务均运行约 17 小时、`docker_default` 网络包含 jx/Dify 服务、价格快照键齐全；修改与重建尚未执行。

### 文档修改记录（2026-08-22 jx 千问 Batch 配置备份、重建与回读完成）

- 修改原因：按用户确认继续执行 jx 远端配置，并把真实变更结果、回滚点和未覆盖的 Nginx 中间层限制写入交接，避免下次只看应用层配置。
- 修改前：jx `.env` 为 `BAILIAN_BATCH_ENABLED=false`、单批 `100/500MB`；本轮仅完成只读预检。
- 修改后：配置备份位于 `/opt/ai-management/backups/20260822-100452-qwen-batch-upload-config`；`.env` 与运行容器回读为 `MAX_UPLOAD_FILE_MB=200`、`MAX_UPLOAD_FILES=100`、`MAX_UPLOAD_BATCH_MB=200`、`BAILIAN_BATCH_ENABLED=true`、候选/Token 阈值 `100/200000`、Provider 分片 `40000/419430400`。API、普通 Worker、Batch Worker、realtime Worker、publication Worker、Scheduler 已完成强制重建。
- 验证：API `/health/ready` 为 `ready` 且 database/redis/dify 均为 `true`；目标服务重建后 `restart=0`，关键日志错误计数为 0；Nginx `/ai-management-test/` 返回 HTTP 200。只修改 `.env`，未修改数据库、Redis、Dify、上传卷或 OCR 服务；Nginx `client_max_body_size=128M` 未改；未执行 orphan 容器清理，未发起真实百炼 Batch。
