# 数据清理与清洗排障

## DATA-001：Dify 测试文档删除必须依赖精确 ID

- 适用：测试前清理和验收后清理。
- 触发：仅凭名称或模糊 Metadata 正则选择删除对象。
- 根因：正式资料可能包含测试样式名称，名称不能证明所有权。
- 解决：使用本地关联记录、明确测试 manifest 或精确 document ID；先生成候选计划和快照指纹，再执行删除。
- 非解决：仅凭名称相似、作者名或正则匹配删除。
- 验证：删除计划中的每个对象都有精确 ID 和可追溯测试归属，执行后正式清单不变。
- 预防：测试创建阶段保存 manifest 和关联 ID，清理阶段不重新猜测所有权。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:307)。
- 最后验证：2026-07-30。

## DATA-005：课程语义字段的列表响应必须先过契约归一化

- 适用：`course_route_catalog` 的 qwen-plus 结构化输出。
- 触发：模型把应为文本的方向、简介、人群或路线目标返回为字符串列表，候选被错误地标成契约无效或直接把 Python 列表写入正文。
- 根因：模型结构化输出满足 JSON 语法不等于满足业务字段类型；同一语义字段可能出现标量/短列表两种表达。
- 解决：在 Pydantic 结构化契约层只对允许的文本列表做确定性拼接；对象、URL/邮箱、营销承诺和未知字段仍拒绝或进入复核。
- 非解决：放宽为任意 JSON、把模型输出原样写库、或为通过测试删除链接门禁。
- 验证：模型契约回归覆盖列表归一化和 URL 拒绝；R20c 课程页面审核发布后 5 份文档均通过 Metadata/正文隐私扫描。
- 预防：为每个 AI 字段固定标量/列表/对象契约，列表归一化只允许明确的文本字段，并在发布前执行链接和隐私扫描。
- 证据：[R20c 交接状态](../../AI管理服务当前状态与后续测试交接.md:10)。
- 最后验证：2026-08-01。

## DATA-002：Redis DB 5 只有在证明独占后才可整库清空

- 适用：AI 服务测试数据清理。
- 触发：准备对 Redis DB 5 执行整库清空。
- 根因：数据库编号不能单独证明该 DB 只被本服务使用。
- 解决：确认 `REDIS_URL` 指向 DB 5、Dify 其他组件不使用 DB 5，先保存完整 RDB 和键清单；条件不满足时停止，不执行 `flushdb`。
- 非解决：只看到本服务使用 DB 5 就推断该 DB 独占。
- 验证：服务配置、Dify 配置、键清单和可恢复 RDB 同时证明隔离与回滚能力。
- 预防：优先删除精确 `ai:` 键；整库清理必须经过独占证明门禁。
- 证据：[V2 部署回滚手册](../V2从开发到服务器部署与回滚手册.md:155)。
- 最后验证：2026-07-30。

## DATA-003：同源子文档必须全有或全无

- 适用：清洗拆分、Dify 发布、课程/路线目录。
- 触发：同一源文件拆成多个远端文档，其中部分成功、部分失败。
- 根因：远端多文档写入与本地数据库事务不共享原子边界。
- 解决：先完成全部子项预检；任一失败时删除或停用本轮同源文档，补偿失败写外部删除任务，目录只在远端全部验证后单事务提交。
- 非解决：保留部分成功子文档并把源文件标记为完成。
- 验证：成功路径全部子文档可回读；失败路径远端和本地均无可见半成品，或存在可追踪补偿任务。
- 预防：同源发布统一使用预检、远端验证、本地提交和补偿编排。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:186)。
- 最后验证：2026-07-30。

## DATA-004：隐私门禁应进入 `review_required`

- 适用：咨询 DOCX/PDF、公众号、小报童、知识星球。
- 触发：自动清洗无法可靠确认脱敏、版面或身份残留。
- 根因：隐私或版面不确定性需要人工判断，普通失败状态无法表达安全复核门禁。
- 解决：无法可靠脱敏、无法确认版面或仍有身份残留时进入待复核，不伪装成普通文件失败；公开文本和 Metadata 同时扫描，别名映射只留本地受保护审计数据。
- 非解决：带不确定身份内容继续自动发布，或仅扫描正文而忽略 Metadata。
- 验证：任务进入 `review_required`，公开输出无受保护身份，映射未进入普通日志和报告。
- 预防：清洗策略把隐私不确定性作为独立终态和人工复核入口。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:98)。
- 最后验证：2026-07-30。

## DATA-006：清洗归零不能只用批次状态判断终态

- 适用：清洗批次本地状态归零和测试前重置。
- 触发：批次为 `partially_completed`，需要判断能否安全删除本地清洗状态。
- 根因：`partially_completed` 既可能表示取消模型任务后的稳定结果，也可能表示发布分片仍在排队；单一批次字段不能证明 Worker 已停止写入。
- 解决：批次只作为候选终态，同时检查文件、条目、模型任务和发布分片；仅当各层均无活动状态时允许清除。
- 非解决：无条件放行所有 `partially_completed`，或只接受 `completed` 而永久阻塞已经稳定的失败/复核批次。
- 验证：源码状态流确认 `partially_completed` 会伴随 `shard_pending` 中间态；契约测试固定各层终态集合，活动子状态继续使计划不可执行。
- 预防：涉及异步 Worker 的删除门禁必须验证父记录和所有异步子记录，不把聚合状态当作唯一事实源。
- 证据：[归零失败恢复与脚本修复记录](../jx文档清洗本地状态归零失败恢复与脚本修复记录-2026-08-02.md)。
- 最后验证：2026-08-02。

## DATA-007：课程目录 XLSX 的过期 dimension 会伪造“缺少表头”

- 适用：`course_route_catalog` 读取用户提供的课程信息与学习路线工作簿。
- 触发：工作簿 XML 含完整数据，但 `openpyxl` 默认只返回 A1，服务误报缺少 `课程链接` 等列。
- 根因：文件 `<dimension ref="A1">` 元数据过期，不是源表真的只有一个单元格。
- 解决：读取前调用 `reset_dimensions()`，流式读取并设置行/列上限；首行严格校验五列契约，空课程名称跳过并告警。
- 非解决：放宽必需列、猜测缺失字段或把模型输出当作结构事实。
- 证据：本地 `tests/test_course_route_xlsx.py` 与 jx 结构审核表导出记录。
- 验证：完整样本可读且解析结果覆盖全部课程/路线；普通多名称冲突仍阻止模型阶段，已知合集式链接标题与具体标题冲突则保留具体标题并记录告警。
- 预防：同一 SHA-256 的本地/服务器样本回归固定为 137 条有效源行、105 个课程实体、5 条路线；同链接普通多名称不得由模型猜名。
- 最后验证：2026-08-02，本地同 SHA-256 样本解析为 105 个课程、5 条路线，2 组已知合集式标题冲突自动解析并记录告警。

## DATA-008：PDF 表格结构不可靠不等于整份文档不可用

- 适用：普通 PDF、咨询 PDF 的清洗转换。
- 触发：表格抽取无法可靠对齐，或者 Dify 分片发布失败后批次停在处理中。
- 根因：表格只影响表格结构可信度；有效正文仍可继续。PDF 转换入口与清洗策略入口此前还不是同一条路径，手工脚本能生成 Markdown 不代表正式策略会复用其结果。
- 解决：PDF 先生成 Markdown，再由共享 `knowledge_cleaner.markdown_table_cleaner` 清洗；可靠 GFM/简单 HTML 表格转为语义句，坏行按表聚合审计，无法可靠对齐的整表保留线性单元格文本。`table_review:*` 仅为内部审计，不进入用户告警；最终发布正文再次执行无 Markdown/HTML 表格门禁。只有无文字层、残留敏感身份或正文为空才进入 `review_required`。发布失败仍按精确 shard ID 重试，成功后刷新文件聚合状态并清除陈旧文件级错误。
- 非解决：因为一个表格解析不稳定就丢弃整份 PDF，或只重试父批次而不核对分片。
- 证据：jx 发布报告 `20260802-cleaning-fix-10d83fd1a999e-deploy` 与分片重试结果。
- 验证：`tests/test_markdown_table_cleaner.py`、`tests/test_cleaning_table_integration.py`、`tests/test_pdf_cleaning_policy.py` 和 `tests/test_pdf_markdown_refine.py` 覆盖表格转换、坏行单表告警、坏表保留、PDF 入口复用和非阻断行为；精确重试后分片状态为 `succeeded`，文件聚合状态刷新且不再保留陈旧错误。
- 预防：所有服务使用同一完整 wheel（含 `ai_management` 与 `knowledge_cleaner`），PDF CLI 仅作为共享核心的诊断入口；发布后核对 Alembic head、包导入、表格清洗审计和分片状态。
- 最后验证：2026-08-02，jx 新版本 `20260802-cleaning-fix-10d83fd1a999e` ready，迁移头 `i5f6a7b8c9d0`，近期严重日志为 0。

## DATA-009：转换 Markdown 开关必须同时覆盖写入、持久化和清理

- 适用：PDF/DOCX 转换后、清洗策略处理前 Markdown 的测试环境核验。
- 触发：已经配置 `SAVE_CONVERSION_MARKDOWN`，但 jx 找不到产物，或开启后服务因 production 校验无法启动。
- 根因：旧实现把开关与 `APP_ENV=production` 硬绑定，默认目录只存在于清洗 Worker 的临时容器文件系统；API 启动清理与 Worker 写入也没有共享同一挂载。
- 解决：保存行为只由 `SAVE_CONVERSION_MARKDOWN` 决定；API 和清洗 Worker 共同挂载名为 `conversion-markdown` 的专用宿主机目录。开启时按 `批次ID__文件ID__规范化原文件名.md` 原子写入、权限设为 `0600`，跨重启保留并按 7 天清理；关闭时不写新文件，并由 API 启动和夜间维护精确删除旧产物。
- 非解决：全局把 jx 的 `APP_ENV` 改为 `test`、只重启 API、直接查看 Worker 容器临时层，或把产物目录指向上传根目录。
- 验证：`tests/test_ai_management.py -k conversion_markdown` 覆盖开关、命名、生产环境禁止开启、7 天保留/过期及专用目录门禁；Compose/发布契约固定双服务挂载和正式发布默认关闭。
- 预防：环境变量变化后至少重建 API 与清洗 Worker；正式环境发布脚本保持 `SAVE_CONVERSION_MARKDOWN=false`，测试产物只允许 SSH 访问且不得写入日志或 HTTP 响应。
- 证据：[AI 管理页清洗操作说明](../AI管理页文档清洗操作说明-2026-07-28.md:162)；jx 发布 `20260802-conversion-md-be372c68d875` 的合成 DOCX 实测确认标题、正文和表格均进入 Markdown，宿主机产物名正确且权限为 `0600`，实测产物随后已精确删除。
- 最后验证：2026-08-02，jx 当前开关为 false，API/Worker 配置与共享挂载一致，ready 且近期严重日志为 0。

## DATA-010：课程目录上传链路必须自动门禁且源文件原子发布

- 适用：`course_route_catalog` 上传、模型补全和课程/路线知识库发布。
- 触发：上传后出现人工费用确认或结构审核入口，模型只成功部分条目，或模型输出改写了名称/ID/链接。
- 根因：旧流程把结构审核和模型费用审批暴露给测试页，且模型任务失败只更新单条记录，未将同源记录统一阻断。
- 解决：默认自动路径由 `COURSE_CATALOG_AUTO_PUBLISH` 控制；价格快照和 1.0 费用上限门禁通过后自动入队。名称、链接、稳定 ID、路线关系和顺序只来自解析结果；模型仅写语义字段。任何自动门禁或发布回读失败都将同源文档/文件统一标记 `CATALOG_AUTO_PUBLISH_BLOCKED`。
- 非解决：放宽费用/模型校验后“先发布能用的课程”，或允许模型选择课程名称、路线和链接。
- 证据：`tests/test_course_route_xlsx.py`、`tests/test_model_cleaning_workflow.py` 的自动审批、价格阻断、源身份保持和样本回归测试。
- 验证：同一来源的课程/路线候选必须整体进入模型与发布门禁；任一价格、模型或回读失败都不产生部分正式数据。jx 真实样本已完成 110/110 模型候选和 110/110 Dify 文档回读，0 失败、0 待复核。
- 预防：课程目录默认走确定性结构解析、价格快照和模型契约三道自动门禁；关闭自动开关时才走结构审核表。任何异常按源文件原子失败并保留可审计告警。
- 最后验证：2026-08-02，本地目标测试和 jx 真实样本回归通过。

## DATA-011：Batch 轮询的瞬时数据库异常不能终止供应商任务

- 适用：百炼 Batch 长轮询、数据库连接短暂断开或 Worker 重启。
- 触发：本地轮询遇到瞬时 PostgreSQL/网络异常，但供应商 Batch 仍处于 `in_progress`。
- 根因：把一次本地读取异常误当作供应商终态，会把本地任务标为失败而留下外部 Batch 继续运行。
- 解决：只有供应商明确返回 `failed/expired/cancelled` 等终态才失败；瞬时异常保留 `batch_running`、记录脱敏错误并重新调度轮询，已有任务模式不因环境开关改变。
- 非解决：为测试强制取消所有未完成 Batch，或在异常时自动切换实时模式。
- 验证：本地瞬时异常回归和 jx 发布后轮询日志通过；课程实时回归确认新任务无 Batch provider ID。
- 预防：Batch 任务保存供应商清单和本地聚合进度，监控下一次轮询时间与供应商终态分离。
- 证据：jx 发布 `20260802-course-catalog-context-fix-db4f37c71fd8` 及顶部 0.14 状态记录。
- 最后验证：2026-08-02。

## DATA-012：精确源删除必须同步释放去重和批次聚合

- 适用：同名/同内容重传前的源文件删除。
- 触发：删除 Dify 文档后，文件已 `source_deleted` 但批次仍显示处理中，或重复保护未释放。
- 根因：删除接口只更新文件墓碑，没有刷新同批次的终态聚合。
- 解决：按精确 `file_id` 和完整原文件名删除全部远端引用；设置 `source_deleted_at/dedupe_released_at` 后，在没有活动文件时同步将批次设为 `completed`。
- 非解决：按文件名模糊删除、直接清 Dify 数据库或强制修改批次状态而不核对文件/文档。
- 验证：jx 历史失败批次和 duplicate noop 占位均精确删除后去重释放、Dify 引用为 0、批次聚合为 `completed`，随后同名重传可进入实时任务。
- 预防：测试页只提供精确源删除入口，重传前必须核对 tombstone、去重释放和远端引用数量。
- 证据：jx 发布 `20260802-course-catalog-context-fix-db4f37c71fd8` 及顶部 0.14 状态记录。
- 最后验证：2026-08-02。

## DATA-013：强制归零必须先静默本地写入者并拒绝活动供应商任务

- 适用：确认全部为测试数据、需要清除仍为 `processing/pending` 的本地清洗状态。
- 触发：普通归零脚本因活动批次门禁拒绝，但测试需要立即重新上传。
- 根因：仅停止测试页不能阻止 Worker、Scheduler 或供应商模型任务继续写入；绕过门禁会造成备份与删除目标不一致。
- 解决：新增独立 `force` 模式；先停止本地清洗/模型/发布写入服务，保存前后目标清单和可恢复备份；发现活动模型供应商任务、待处理 Dify 删除任务或无法证明归属的上传目录时停止；否则只删除本地清洗表、精确 Redis 清洗键和批次 UUID 上传目录。
- 非解决：修改批次状态伪造终态、取消未知供应商任务、整库清 Redis、调用 Dify 删除接口或按名称删除知识库文档。
- 验证：jx 强制归零前后清洗表、Redis 清洗键和上传目录均为 `0`，无关外部删除任务守恒，Dify 删除操作为 `0`，备份恢复、API ready 和测试页 HTTP `200` 通过。
- 预防：强制归零前必须验证写入者已静默、供应商任务无活动、备份可恢复且目标目录为批次 UUID；任何一项无法证明都停止。
- 证据：`scripts/jx_reset_cleaning_local_state.sh`、2026-08-03 强制归零备份与脱敏报告。
- 最后验证：2026-08-03。

## DATA-009：严格咨询 v2 契约失败必须保留复核终态

- 适用：个人商业咨询 DOCX/PDF 清洗、模型契约和发布门禁。
- 触发：模型返回 `consulting_case_v2` 但省略必填字段、把 EvidenceItem 数组写成字符串，或违反证据状态/结果等级约束。
- 根因：早期提示词没有完整展开 v2 字段和数组对象结构；模型修复轮次也缺少针对列表、E2/E3 和事实状态的结构性提示。
- 解决：提示词明确完整顶层字段、EvidenceItem/DecisionUnit 结构和来源覆盖规则；重试提示只修正结构，不放宽来源证据门禁。
- 验证：jx 测试页咨询批次最终 `completed`，1 个样本进入成功、2 个样本安全进入 `review_required`、0 个失败；未把不可靠模型结果自动发布到 Dify。
- 非解决：删除必填字段、把模型字符串强转为对象、放宽证据等级或为了全绿跳过人工复核。
- 边界：`review_required` 是可追踪的安全终态，不得为追求全成功而猜测来源、降级 Pydantic 校验或直接写入知识库。
- 预防：普通 `execute` 保持终态门禁；强制模式使用独立确认词并仅用于已确认的测试数据。
- 证据：`AI管理服务当前状态与后续测试交接.md` 的 0.19 节及 2026-08-03 jx 备份目录。
- 最后验证：2026-08-03。

## DATA-014：PDF 质量告警与硬阻断必须分级记录

- 适用：PDF 共享转换核心、上传清洗和 OCR 队列。
- 触发：PDF 转换遇到表格/版式/OCR 低置信度、无文字层、页数超限、OCR 失败或隐私/事实冲突。
- 根因：PDF 类型、结构质量和安全门禁是不同维度；把可继续处理的告警统一当失败会丢弃正文，把硬阻断当告警会造成不安全发布。
- 解决：表格/版式/OCR 低置信度属于可继续处理的 `warning`，统一写入 `PDF_CONVERSION_WARNING`；混合无法分类、页数超限、OCR 失败、关键事实冲突、无正文、隐私残留属于 `blocked`，写入 `PDF_CONVERSION_BLOCKED` 并保持文件 `review_required`/`failed`。
- 非解决：为了全绿把 warning 改成成功、把硬阻断降级为 warning，或把正文/原始文件名写入问题日志。
- 统一入口：问题中心复用 `SystemEventLog`，通过 `/internal/v1/admin/pdf-conversion-issues` 查询、按精确 `issue_id` resolve；普通日志清理不得删除 `status=open` 的 PDF 事件。
- 安全边界：事件只保存脱敏代码、页数、后端、知识库代码和内部文件 ID，不保存正文、原始文件名或密钥。
- 验证：`tests/test_pdf_conversion_issues.py` 覆盖状态/知识库/严重性过滤和精确解决；`tests/test_pdf_markdown_refine.py` 覆盖共享核心与历史 CLI 入口一致。
- 预防：上传链路固定先分类、再执行 OCR/清洗和隐私门禁；问题中心查询和精确 resolve 纳入契约测试。
- 证据：`var/reports/20260805-plan-merge/issues.jsonl`、`tests/test_pdf_conversion_issues.py`、jx 远端代表性 PDF 回归。
- 费用门禁：验收付费清洗累计上限由机器环境变量 `JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN` 配置，默认 5.00 元；超限在新任务前 fail-closed，不改变长期产品配额。
- 最后验证：2026-08-05，本地定向测试通过；jx 远端 PDF Workflow 临时配置已恢复基线并退役。

## DATA-015：精确清理必须覆盖批次和文件作用域事件

- 适用：验收批次、PDF 转换问题中心和审计日志清理。
- 触发：测试批次与 Dify 文档已按 manifest 删除，但问题中心仍显示该批次的 `open` PDF 告警。
- 根因：批次级事件使用 `CleaningBatch.id`，PDF 转换事件使用 `CleaningFile.id`；只按批次 ID 删除日志会留下文件作用域孤儿。
- 解决：从 manifest 指定的批次读取其 `CleaningFile.id`，将批次 ID 与文件 ID 合并为精确资源集合后删除对应 `SystemEventLog`；不得按时间、文件名或模糊匹配扩大范围。
- 验证：新增 `tests/test_release_data_cleanup.py` 契约断言；jx 两轮代表性 PDF 清理后批次/Dify/Redis/事件均无本轮残留，问题中心 `status=open` 总数为 0。
- 非解决：只删除 `CleaningBatch.id` 对应日志、按时间窗口清理或按文件名猜测资源归属。
- 预防：新增事件资源类型时同步登记其精确归属 ID，并在清理 manifest/回归中验证父子作用域的审计行均被处理。
- 证据：`scripts/jx_release_data.py`、`tests/test_release_data_cleanup.py`、`var/reports/20260805-plan-merge/issues.jsonl`。
- 最后验证：2026-08-05。

## DATA-016：手工 PDF 转换成功不代表正式清洗入口复用该脚本

- 适用：`scripts/pdf_markdown_refine.py` 与 `personal_business_consultation` 的 PDF 入库链路。
- 触发：手工脚本可以生成 Markdown，但正式批次仍在 PDF 表格审计或后续模型阶段失败。
- 根因：手工脚本是 CLI 包装器，正式策略此前直接调用 `knowledge_cleaner.pdf_convert.convert_consultation_pdf`，两者没有共享 Markdown 表格清洗阶段；“脚本能转”不能证明“正式策略已使用脚本输出”。
- 解决：正式策略和普通 Markdown 入口统一调用共享 PDF/Markdown 规范化与表格清洗核心；CLI 提供 `--clean-tables` 以复现转换后清洗，`raw` 也执行统一规范化和表格清洗，只跳过具体策略语义处理。
- 非解决：只验证手工 CLI、把表格告警当作整份 PDF 失败，或为修复表格而改变 `raw_upload` 原文语义。
- 验证：`tests/test_cleaning_table_integration.py`、`tests/test_pdf_markdown_refine.py` 覆盖正式策略和 CLI 的相同清洗结果；远端原批次通过精确模型任务重试完成入库，未重新上传源文件。
- 预防：排障时同时核对 CLI 入口、正式策略调用点、共享核心包导入和转换审计，不以单独手工脚本结果代替服务链路证据。
- 证据：`var/reports/20260805-markdown-table-cleaning/issues.jsonl`。
- 最后验证：2026-08-05，本地定向回归通过。

## DATA-018：不完整 Markdown 表格必须跳过并继续正文处理

- 适用：PDF 转 Markdown 后的 `personal_business_consultation` 清洗、Markdown CLI 复现和表格问题中心。
- 触发：表头/分隔线列数不一致、重复表头、行列数不匹配或 HTML 表格结构无法可靠对齐。
- 根因：无法证明列语义对齐时，继续拼接单元格会制造错误事实；只记录 `table_review` 也会让下游误以为表格已被安全清洗。
- 解决：可靠表格转为带表头语义的句子；坏行按表聚合内部审计；结构不可靠的完整表格块保留线性单元格文本，保留其前后正文继续处理。日志只包含表序号、行号、动作和稳定原因，不包含正文；最终正文不得保留 Markdown/HTML 表格。
- 非解决：猜测列对齐、把整份 PDF 置为失败、保留坏 Markdown 表格，或为每个坏单元格重复打日志。
- 验证：`tests/test_markdown_table_cleaner.py`、`tests/test_cleaning_table_integration.py`、`tests/test_pdf_markdown_refine.py` 通过；jx 原批次转换审计显示 Markdown 非空、`blocking_reasons=[]`、唯一表格告警非阻断；精确重试后批次/文档成功，Dify 索引完成。
- 预防：每个文档按表格聚合告警；`BAILIAN_BATCH_ENABLED=false` 时模型仍走 realtime，不因表格告警切换 Batch。
- 证据：`knowledge_cleaner/markdown_table_cleaner.py`、`ai_management/cleaning.py`、本轮脱敏 `var/reports/20260805-full-test-jx-fix/issues.jsonl`。
- 最后验证：2026-08-05。

## DATA-017：内容模型契约失败必须区分字段语义并脱敏跳过

- 适用：`personal_business_consultation` 模型输出、重试和知识库发布门禁。
- 触发：结构化结果在 `risks_or_limits` 等字段使用了不允许的 `EvidenceItem.status`，内容模型文档记录 `MODEL_CONTRACT_INVALID`、进入 `failed` 且没有知识库分片；课程目录仍按严格源级策略处理。
- 根因：模型返回值违反 `ConsultationCaseV2` 的字段状态契约；这不是 PDF 表格告警导致的阻断。旧重试提示没有把 `risks_or_limits` 的允许状态单独展开，错误消息还可能直接带出 Pydantic 输入摘要。
- 解决：提示词保留严格字段契约；只有无可用模型响应才在文档级重试一次，非空契约错误不再发送修复提示，直接记录脱敏路径/错误代码并跳过，不回显模型正文或原始输入。
- 验证：`tests/test_model_contract_safety.py` 和 `tests/test_model_cleaning_workflow.py` 覆盖字段契约、无响应单次重试、契约失败不重试和错误脱敏；失败条目无 Dify 发布分片。
- 非解决：把 `customer_fact` 自动改写为 `consultant_judgment`，或把表格 warning 当成模型契约失败的替代解释。
- 预防：保持模型契约严格校验；错误日志只保存脱敏代码，不把模型正文写入事件或数据库；批量处理通过失败记录接口按页/CSV 导出，不要求逐条审批。
- 证据：`var/reports/20260805-markdown-table-cleaning/issues.jsonl`。
- 最后验证：2026-08-05，本地定向回归通过。

## DATA-019：知识库正文必须来自最终 Markdown，而不是源文件或中间策略正文

- 适用：用户上传 PDF、DOCX、Markdown、TXT 后进入任意清洗策略、模型/复核和 Dify 发布的完整链路。
- 触发：本地清洗结果已变更，但知识库仍出现原始扩展名、原始正文或 Markdown 表格；或者 `raw` 上传绕过了表格清洗。
- 根因：正式 PDF 入口与 CLI 共享转换核心分叉；`raw` 分支关闭表格清洗；转换产物保存时机早于清洗；策略/模型/复核后的正文缺少统一最终无表格门禁；Dify 名称和正文发布未绑定最终清洗产物。
- 解决：所有支持的输入先统一转换为 Markdown，再执行表格清洗；`cleaned` 进入策略语义处理，`raw` 只跳过语义处理；策略、模型、人工复核和文章 shard 发布前统一执行最终 Markdown/无表格门禁，并用同一正文调用 Dify `create-by-text`。Dify 名称固定为 `.md`，同名追加序号，`content_format=markdown`。
- 非解决：只运行 `scripts/pdf_markdown_refine.py`、只改某一个策略、只在 API 层替换文件名、把 `table_review` 当作用户 warning，或直接上传源 PDF/DOCX 二进制到 Dify。
- 验证：本地全量 pytest、发布契约和文档契约通过；jx 新数据 `standard_document` 与 `raw` 的 PDF、DOCX、Markdown 均 `succeeded`，最终存储无表格，Dify 名称和 Metadata 边界通过；测试数据按精确 manifest 清理。
- 预防：新增策略必须复用统一输入/最终发布函数；回归至少覆盖 PDF、DOCX、Markdown、`cleaned`、`raw`、表格和同名序号；禁止以历史数据状态替代新数据回读证据。
- 证据：`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`ai_management/workers/tasks/model_cleaning.py`、`knowledge_cleaner/markdown_table_cleaner.py`、jx release `20260805-cleaning-unify-0d1125af5d56` 及报告目录。
- 最后验证：2026-08-05。

## DATA-020：review_required 开关只放宽非空内容复核门禁

- 适用：四个内容模型策略的新建上传批次和 `CLEANING_REVIEW_REQUIRED_ENABLED` 配置。
- 触发：jx 测试环境需要验证非空短文本、隐私/来源复核或模型 `review` 是否会阻塞后续模型/知识库链路。
- 根因：`review_required` 同时承载安全复核和产品流程阻断；测试环境若直接改代码或删除错误状态，会失去原始审计和批次一致性。
- 解决：默认值保持 `true`；仅在明确的测试环境设置为 `false`，并把策略/配置快照写入批次。非空复核原因只保留内部审计，文章策略使用保留来源块、咨询 v2 使用模型保留来源块；没有可保留正文仍为 `review_required`。
- 非解决：关闭开关后绕过空正文、模型空结果、模型契约、Dify 硬约束、发布补偿或 `course_route_catalog` 源级原子门禁；也不能用客户端公共 Metadata 放宽服务端规则。
- 验证：本地清洗与模型工作流测试通过；jx release `20260806-111200-full-8f59f14169c9` 中空文档批次 `019fd511-11ea-718b-bfed-0ba266f08d4d` 仍为 `review_required`，唯一非空批次 `019fd515-596b-78b2-8733-05cb5ab460c4` 成功到达 Dify 条目详情。
- 预防：新批次只读取配置快照；发布前核对默认值、测试环境值和策略范围；报告只记录错误码、批次/文件内部 ID 和状态，不记录正文或密钥。
- 证据：`var/reports/20260806-review-switch/issues.jsonl`、jx 发布报告 `/opt/ai-management/var/reports/v2/20260806-111200-full-8f59f14169c9-deploy`、[当前状态交接](../../AI管理服务当前状态与后续测试交接.md:10)。
- 最后验证：2026-08-06。

## DATA-021（历史规则）：策略空正文异常曾归入 review_required 而不是普通失败重试

- 适用：Markdown/TXT 清洗策略返回空文档或抛出明确“没有可清洗正文”异常的 Worker 路径。
- 触发：空上传在策略解析阶段抛出带修饰词的空正文异常，最终文件却变成 `CLEANING_FAILED`。
- 根因：Worker 只匹配固定的“没有可清洗正文”文本，未覆盖“没有可清洗的知识星球正文”等同义空内容错误，导致空内容被当作可重试的普通异常。
- 解决：扩展空内容识别标记；明确空内容直接写入 `CONTENT_EMPTY_AFTER_CLEANING` 并进入 `review_required`，不发送模型任务、不发布 Dify；非空解析、契约和基础设施异常继续按各自重试/失败规则处理。
- 非解决：捕获所有 `ValueError` 都标记为 review、把空文件填充占位正文、或用关闭 `review_required` 开关绕过空内容。
- 验证：本地新增空策略 Worker 回归通过；jx 修复后空文档批次 `019fd511-11ea-718b-bfed-0ba266f08d4d` 为 `review_required`，模型任务和发布分片均为空。
- 预防：新增策略的空源/空结果测试必须覆盖异常文本与空返回两条路径；聚合状态以文件/条目状态为准，不以异常退出码推断产品语义。
- 证据：`ai_management/workers/tasks/cleaning.py`、`tests/test_ai_management.py`、`var/reports/20260806-review-switch/issues.jsonl`。
- 最后验证：2026-08-06。

> 该条目保留历史验收事实；当前新批次空正文统一按 DATA-022 的边界进入 `failed`，不再依赖人工复核绕过。

## DATA-022：新批次模型失败必须降级为源 Markdown 发布

- 适用：PDF/DOCX 转换后 Markdown、上传 Markdown、表格清洗、模型清洗和 Dify 发布的连续链路。
- 触发：大量文件在模型 `review_required`、模型契约失败、无响应或任务异常后无法进入知识库；文件/批次聚合长期停留在 `processing`。
- 根因：旧逻辑把模型复核和技术失败都当成需要人工处理的终点，且任务或 Dify 分片异常时没有总是刷新文件/批次聚合。
- 解决：新 `cleaned` 批次保存服务端 `_cleaning_review_policy`。除 `course_route_catalog` 外，只要表格清洗后的 `cleaned_markdown` 非空，模型复核/失败就保留内部原因、明确忽略模型返回值，恢复源 Markdown 为 `pending` 或 `shard_pending`；空源、转换失败和 Dify 硬约束失败仍为 `failed`。所有 Dify 写入统一进入单并发 `ai_publication` 队列；5 分钟看门狗重新调度长期未更新的文档/模型/分片任务。
- 日志：`CLEANING_FILE_FAILED`、`DIFY_PUBLICATION_SHARD_FAILED` 和源 Markdown 降级事件记录 `file_id/batch_id/strategy_code/extension/attempts/phase/exception_type/脱敏 exception_message`；不记录正文、密钥、原始敏感文件名或完整路径。
- 非解决：把空正文填充占位符、把 `course_route_catalog` 降级为文本发布、直接修改 Dify 数据库、增加未确认的多文档 API，或仅把批次标成 `completed`。
- 验证：本地模型/清洗/分片回归覆盖源 Markdown 恢复、模型输出不落入最终正文、Dify 分片失败后聚合刷新和发布队列；发布后需用批次/文件/分片精确 ID 检查 Dify 索引、父子块、Metadata 回读和文件状态。
- 预防：旧批次没有新 policy 快照时 fail-closed；新批次创建时固定策略/Metadata/chunking 快照；多个文件不拼接为一个 Dify 正文，文章分片遵守 `DIFY_SHARD_MAX_ITEMS`（默认 200）和字节上限。
- 证据：`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`ai_management/workers/tasks/model_cleaning.py`、`ai_management/workers/tasks/maintenance.py`、`tests/test_model_cleaning_workflow.py`、`tests/test_ai_management.py`。
- 最后验证：2026-08-06，本地全量回归通过；jx release `20260806-144000-full-9afc8b78bc35` 的合成 Markdown 批次成功到达 Dify 并按精确 file ID 清理。
