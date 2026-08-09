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

## DATA-029：课程目录普通上传不得隐式全量替换

- 适用：`course_route_catalog` 合并工作簿、课程/路线权威表和学习路线进度。
- 触发：普通上传完成后，批次未包含的旧课程/路线/合集消失，或已有学习路线进度被清空。
- 根因：本轮新增的“源登记/路线冲突”门禁把正常的完整工作簿上传误判为未授权，导致 `course_route_catalog` 批次失败；随后残缺目录被写入，商业顾问无法得到路线、课程卡和点评。
- 解决：恢复原有 `course_route_catalog` 清洗与逐文档增量 upsert 路径，不改变课程/路线清洗字段；完整 Excel 的课程、路线、合集成员按源顺序逐条同步，缓存仅在提交后重建。合集 ID 按路线/阶段作用域隔离同名连续块，单个连续块内混入其它路线的行不进入该合集；商业顾问仅在 `learning_route` 展示阶段内课程合集名称和按合集裁剪的课程名称。
- 非解决：不在普通上传完成时删除批次未包含的旧目录，不按文件名或时间窗口清理历史，不将不完整/失败批次当成完整目录发布；不把课程合集名称写回课程原始字段。
- 验证：本地原始课程目录解析为 105 门课程、5 条路线、33 个合集、103 条成员关系；新增同名合集/跨路线连续块回归，`tests/test_course_route_xlsx.py`、`tests/test_course_route_catalog.py` 与 `tests/test_course_collections.py` 通过。页面验收曾定位到远端阻断日志：`课程目录存在未登记完整源的路线结构冲突`。
- 预防：上传后先核对批次所有文件/条目为成功，再核对课程、路线、合集和成员关系数量及 Redis 快照；发现课程数异常时停止商业顾问验收并回滚发布。
- 证据：`ai_management/course_import.py`、`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`ai_management/courses.py`、`tests/test_course_route_catalog.py`。
- 最后验证：2026-08-07。

## DATA-030：课程目录模型缺失方向/目标不能用技术兜底伪造

- 适用：`course_route_catalog` 的模型丰富和原子候选发布。
- 触发：模型未返回路线方向或目标，但批次仍被补写默认语义并进入发布。
- 根因：本轮把原有路线中性兜底删除，导致没有独立方向/目标的路线整批失败；同时错误地把该清洗策略当成需要改造的范围。
- 解决：恢复原策略的中性路线兜底（方向使用“课程学习路线”，目标使用路线名称），课程来源字段和课程模型结果仍优先；课程合集仍按原始连续块逐文档同步，重复名称按路线/阶段隔离；仅在商业顾问输出的 `learning_route` 阶段增加按当前阶段裁剪的课程合集与课程名称展示。
- 非解决：不把模型生成的收益承诺、链接或内部 ID写入目录；不以合集名称覆盖课程名称、阶段顺序或课程原始顺序。
- 验证：`tests/test_model_cleaning_workflow.py` 覆盖来源语义优先及方向/目标缺失时的中性回退；样本目录解析统计为 105/5/33/103。
- 预防：课程清洗策略变更必须单独评估，商业顾问展示字段只能在服务端响应装饰层增加；任何目录计数下降或路线出现其它路线课程先停止发布。
- 证据：`ai_management/workers/tasks/model_cleaning.py`、`ai_management/course_route_catalog.py`、`ai_management/courses.py`、`tests/test_model_cleaning_workflow.py`。
- 最后验证：2026-08-07。

## DATA-031：课程合集必须按路线/阶段作用域隔离，展示层还要裁剪到当前阶段

- 适用：`course_route_catalog` 的连续课程合集解析、`learning_route` 阶段展示和课程卡后的路线验收。
- 触发：一次学习路线的阶段里出现其它路线的课程；同名课程合集被合并；或 `course_collections` 展示出不属于当前阶段的课程名称。
- 根因：旧实现把合集成员关系按全局合集名称/ID 派生。同名合集在不同路线或阶段之间发生碰撞时，全局映射会把另一条路线的成员带入；仅过滤合集名称不能修复成员关系本身。另一个风险是展示层直接读取合集全局成员，而不是当前阶段的课程集合。
- 解决：解析层为同名连续合集增加路线/阶段作用域，并按原始连续块和出现次序生成成员关系；连续块中显式属于其它路线的行不进入当前合集，阶段字段冲突只记录诊断而不破坏源顺序。课程合集 ID 在普通成员变化时保持稳定，只有同一作用域重复块才使用冲突后缀。服务端展示层再将合集成员与当前阶段 `course_ids` 求交集，新增可选 `course_collections: [{"name": "课程合集", "course_names": ["课程名称"]}]`；原 `course_ids`、`course_names`、`course_collection_names` 不变。
- 非解决：不修改课程原始清洗字段，不把合集名称写回课程，不在普通上传时全量替换或删除未包含的旧目录，不新增 SSE 事件/HTTP 字段，不依赖用户侧过滤来兜底串线。
- 验证：本地真实样本解析为 `105/5/33/103`；跨路线同名合集、连续块混入其它路线、阶段交集裁剪均有回归。jx 批次 `019fdb04-2cd5-7c2a-81b4-a4e645424d6f` 的 `110/110` 条目成功；页面路线首问展示完整两阶段及合集课程名称，阶段确认后课程卡后的 `course_recommendation_note` 仅一条。
- 目录基线注意：该批次前已存在来源 `course-catalog-20260807-150001.xlsx`（21 门课程），普通增量同步不会自动删除它，因此线上总课程数为 `126`。这不是解析器把样本解析成 126；若产品要求仅保留本批次 105 门，必须对该精确来源执行独立、可审计的清理和复验。
- 预防：每次课程路线发布至少核对本地解析统计、批次候选/终态统计、Redis/数据库四项目录计数，以及页面 `learning_route → course_cards → course_recommendation_note` 顺序；发现总数比本批次多时先定位 `source_file`，不要直接删除整库或重跑全量替换。
- 证据：`ai_management/course_route_catalog.py`、`ai_management/courses.py`、`tests/test_course_route_xlsx.py`、`tests/test_course_collections.py`、`docs/执行计划合集/20260807-商业顾问全链路发布与目录验收.md`。
- 最后验证：2026-08-07。

## DATA-026（已扩展）：PDF 文字层优先，质量告警不等于整份文档失败

- 适用：共享 PDF→Markdown 核心和所有使用 PDF 输入的清洗策略。
- 触发：多页文字型 PDF 的首页或末页只有少量文字，正文页均有可用文字层。
- 根因：旧分类器把含图低文字页统一归为 uncertain，导致有完整原生文字层的正文在转换前被硬阻断。
- 解决：只要每页都有原生文字层、总文字量达到安全门槛，即使部分页面含图或低文字也优先走文字转换；首页/末页无图低文字继续记录 `PDF_BOUNDARY_LOW_TEXT_WARNING`，含图低文字记录 `PDF_TEXT_LAYER_IMAGE_PAGE_WARNING`，质量告警不投影到 Dify Metadata 或用户文本。
- 非解决：因为单页含图或低文字就整份 PDF 拒绝，或把 image-only、隐私残留等硬阻断降级成普通质量告警。
- 仍阻断：image-only 页、纯扫描 OCR 失败、空正文和隐私残留。咨询 PDF 含图片时额外触发 `PDF_CONSULTATION_IMAGE_PRIVACY_REVIEW`，人工确认前不得发布。
- 验证：PDF、咨询和清洗集合覆盖文字层+插图放行、image-only 拦截、边界告警、咨询图片隐私和低文字全篇不误走 OCR。
- 预防：所有 PDF 入口继续复用共享分类核心；新增边界例外必须同时补首尾、页中、含图、纯扫描和隐私门禁回归，不得按单一策略放宽。
- 证据：`knowledge_cleaner/pdf_convert.py`、`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`docs/执行计划合集/资料钩子 PDF 清洗入库根因定位方案.md`。
- 最后验证：2026-08-07。

## DATA-027：Dify 删除已不存在文档必须按幂等成功处理

- 适用：重复上传预检、源文件删除、Dify 文档精确清理和去重状态释放。
- 触发：远端文档已经不存在，但源删除接口返回 502，`source_deleted` 未完成，后续重复上传仍被旧去重记录阻断。
- 根因：删除接口把 Dify 的 404 当作普通请求失败；删除语义本身是幂等的，“已不存在”与“刚刚删除”都表示目标已不在远端。
- 解决：仅对删除请求的 HTTP 404 视为成功；401、403、429、5xx 和其他异常继续失败并记录脱敏上下文。删除成功后按精确文件/分片范围释放去重状态，不按文件名或时间窗口扩大清理。
- 非解决：忽略所有 HTTP 错误、直接修改 Dify 数据库、重建整个知识库或删除整批历史数据。
- 验证：`tests/test_dify_client.py` 覆盖 404 删除幂等行为；jx 重复上传预检源删除返回 200，旧远端 ID 数量为 0，去重状态成功释放。
- 预防：所有外部删除 API 明确区分幂等 404 与权限/网络错误；发布和清理报告保存精确目标 ID、状态和错误码。
- 证据：`knowledge_cleaner/dify.py`、`ai_management/cleaning.py`、`tests/test_dify_client.py`、`docs/执行计划合集/20260807-商业顾问全链路发布与目录验收.md`。
- 最后验证：2026-08-07。

## DATA-028（历史错误规则，已废止）：课程路线行缺少方向/目标时不能阻断整批导入

- 适用：`course_route_catalog` 课程与学习路线工作簿的模型丰富、严格解析和原子发布。
- 触发：路线行没有独立方向/目标，模型未返回对应字段，导致整批文档进入 `CATALOG_AUTO_PUBLISH_BLOCKED`，用户无法得到完整路线。
- 根因：路线的方向/目标是辅助检索字段，不应被误当作课程 ID、来源或收益承诺；旧流程在缺失时直接抛出通用字段错误，把可确定的路线名称也一并阻断。
- 原处理曾对 `route` 使用中性的默认方向“课程学习路线”，目标缺失时回退为路线名称；这会掩盖模型/源字段缺失并降低语义质量。
- 现处理：方向或目标缺失时严格阻断并保留真实文档级错误；课程/路线 ID、来源文件、课程卡与阶段顺序门禁保持严格。
- 解决：该历史规则已废止；当前按 DATA-030 恢复原策略的中性兜底，并由逐文档增量同步和响应层展示隔离处理；本条仅用于解释旧批次行为。
- 非解决：不要把旧批次的严格阻断当作当前规则，也不要删除方向/目标的中性兜底或把展示层课程合集字段写回清洗原始字段。
- 验证：当前测试覆盖缺失方向/目标的中性回退、错误保留、连续课程合集边界与路线展示隔离；旧批次不回写。
- 该条目保留历史批次事实，不代表当前行为；当前修复详见 DATA-030。
- 预防：导入前静态检查路线必需的可推导字段；模型字段缺失时只允许白名单中性的技术兜底；批次报告记录被兜底的文档数量和原因。
- 证据：`ai_management/workers/tasks/model_cleaning.py`、`tests/test_model_cleaning_workflow.py`、`ai_management/course_import.py`、`docs/执行计划合集/20260807-商业顾问全链路发布与目录验收.md`。
- 最后验证：2026-08-07。

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
- 预防：旧批次没有新 policy 快照时 fail-closed；新批次创建时固定策略/Metadata/chunking 快照；每个逻辑文章/帖子单独调用一次 Dify `create-by-text`，多个文件和逻辑条目不拼接为一个远端正文；`DIFY_SHARD_MAX_ITEMS` 仅保留为兼容/防御性校验，单条正文仍受字节上限约束。
- 证据：`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`ai_management/workers/tasks/model_cleaning.py`、`ai_management/workers/tasks/maintenance.py`、`tests/test_model_cleaning_workflow.py`、`tests/test_ai_management.py`。
- 最后验证：2026-08-06，本地相关回归通过；jx release `20260806-165915-full-23162b2` readiness、同源镜像和容器烟测通过。

## DATA-023：非模型策略不得在本地预写 Dify 控制分隔符

- 适用：`lead_magnet`、`book` 等确定性策略，以及所有最终进入 Dify 父子块发布的正文。
- 触发：资料钩子文件没有生成 `CleaningDocument`，文件反复处于 pending/retry，错误为 `正文包含保留的 Dify parent 分隔符`。
- 根因：确定性策略在本地调用分块渲染器，把 `<<<DIFY_PARENT>>>` / `<<<DIFY_CHILD>>>` 写入源 Markdown；后续统一边界校验把这些内部控制符当成用户正文，形成必然失败的自相矛盾链路。
- 解决：确定性策略只保存表格清洗后的源 Markdown；发布 Worker 将保存的父子块分隔符、上限和 Metadata 一起传给 Dify，由 Dify 执行分块。该修复不回写历史批次。
- 非解决：删除边界校验、把控制符直接当普通正文上传、或用占位符掩盖空正文。
- 验证：`tests/test_large_scale_cleaning.py` 覆盖资料钩子不含控制分隔符；jx release `20260806-165915-full-23162b2` 容器内策略烟测通过，readiness、Dify 可达和发布报告门禁通过。
- 预防：确定性策略回归必须同时断言正文非空且不含两个 Dify 控制分隔符；模型输出/发布渲染若需要控制符，必须只在对应发布阶段生成。
- 证据：`ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py`、`tests/test_large_scale_cleaning.py`、`/opt/ai-management/var/reports/v2/20260806-165915-full-23162b2-deploy`。
- 最后验证：2026-08-06。

## DATA-024：书籍 PDF 转换硬门禁失败不是模型降级场景

- 适用：`book` 策略上传 PDF 的 PDF→Markdown 转换阶段。
- 触发：文件终态为 `CONTENT_EMPTY_AFTER_CLEANING`，错误消息包含 `PDF_TYPE_REVIEW_REQUIRED`，没有本地 `CleaningDocument` 和模型任务。
- 根因：PDF 被判定为混合、低文字层或无法判定页面，转换器拒绝产生可安全入库的 Markdown；既然没有源 Markdown，就不能执行“忽略模型结果、恢复源 Markdown”的降级。
- 解决：保持明确失败并记录完整转换异常；应改用可读文字层/可安全 OCR 的 PDF 或单独修复 PDF 转换输入，不把原始 PDF 二进制冒充 Markdown 发布。该边界不属于 `review_required` 绕过范围。
- 非解决：用空占位正文、直接上传 PDF 二进制、把转换失败伪装成模型成功，或只把批次标记为 completed。
- 验证：jx 测试批次 `019fd5cf-1141-7fde-a595-cc09dea7f87e` 查询显示无模型任务，文件错误为 `CONTENT_EMPTY_AFTER_CLEANING`，容器内复现转换异常为 `PDF_TYPE_REVIEW_REQUIRED`。
- 预防：PDF 验收必须区分“有非空源 Markdown 的模型失败降级”和“没有源 Markdown 的转换硬失败”；两者分别检查源快照和转换诊断。
- 证据：`ai_management/cleaning.py`、`knowledge_cleaner/pdf_convert.py`、`ai_management/workers/tasks/cleaning.py`、上述精确批次记录。
- 最后验证：2026-08-06。

## DATA-025：认知分享模型必须返回证据绑定知识单元

- 适用：`wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post` 的新模型输出。
- 触发：模型任务已完成且返回 `keep/drop`，但最终正文几乎只是来源块，无法说明模型完成了问题、结论、推理和行动提取。
- 根因：旧模型 schema 只表达来源块保留/排除和主题标签；如果校验器把没有知识单元的 `keep` 当成成功，就会把“模型走过”误认为“完成知识提取”。
- 解决：新契约要求 `keep/review` 且存在保留块时返回至少一个知识单元；每个单元必须引用 `keep` 来源块，数字/日期不得超出引用证据。缺少知识单元或证据不闭合即为模型契约失败；新批次有非空源 Markdown 时忽略模型结果并自动降级发布。
- 非解决：把整篇原文包一层标题伪装成提取结果、放宽来源块覆盖、接受模型编造的数字/日期，或让契约失败进入人工审批队列。
- 验证：历史 `zsxq_post` 批次的模型结果记录 `knowledge_units_count=0`，其 `keep` 条目因此只能证明旧式筛选，不证明知识提取；本地知识单元独立文档和缺失知识单元契约测试通过；jx release `20260806-172003-full-23162b2` 容器契约烟测通过。
- 预防：Prompt、Pydantic schema、来源证据校验和渲染器必须同步升级；模型输出审计只保存结构/计数和脱敏原因，不记录正文。
- 证据：`ai_management/model_cleaning.py`、`ai_management/workers/tasks/model_cleaning.py`、`tests/test_model_cleaning_workflow.py`、`/opt/ai-management/var/reports/v2/20260806-172003-full-23162b2-deploy`。
- 最后验证：2026-08-06。

## DATA-032：课程域归零后不得自动启动写入服务

- 适用：jx 测试服务器 `course` 文档域从上传、清洗、数据库、Redis、上传目录到 Dify 的整链路归零。
- 触发：归零脚本已报告目标为 `0`，服务重启后课程批次和 Dify 文档又出现。
- 根因：旧脚本清理完成后自动启动 API/Worker；旧测试页、浏览器或代理仍持有上传请求并在服务恢复后重试，API 日志可见新的 `POST /internal/v1/admin/knowledge-bases/course/upload-batches`，这不是数据库或 Redis 回滚。
- 解决：使用服务器专用 `jx_reset_course_document_data_test.sh`；执行阶段先静默写入者，成功后保持服务停止；关闭旧上传客户端后，再显式执行脚本的 `start` 子命令。执行前后均用 `plan` 核对课程 Dify 文档、批次、课程和路线范围。
- 非解决：重启后再判断删除是否成功、整库 `FLUSHDB`、按名称删除 Dify 文档、或把新出现的数据误判为数据库回滚。
- 验证：2026-08-07 jx 实测执行前为 `22` 个课程 Dify 文档、`1` 个批次、`21` 门课程、`1` 条路线；执行后停服复核及独立 `plan` 均为 `0`。备份为 `/opt/ai-management/backups/20260807-182804-course-document-data-reset`，操作说明见 [jx 课程文档域全链路归零测试脚本说明](../jx课程文档域全链路归零测试脚本说明.md)。
- 预防：归零脚本的成功路径不得自动启动写入服务；启动前关闭测试页和旧上传请求，并将服务启动作为单独、可观察的运维动作。
- 证据：jx 服务器 `/opt/ai-management/var/reports/20260807-182846-course-document-data-plan`、`/opt/ai-management/backups/20260807-182804-course-document-data-reset`，以及 [课程文档域全链路归零测试脚本说明](../jx课程文档域全链路归零测试脚本说明.md)。
- 最后验证：2026-08-07。

## DATA-033：完整清洗验收生成的 Dify 测试文档必须显式按 manifest 清理

- 适用：`jx_full_cleaning_baseline.sh`、`full_cleaning_acceptance.py` 运行后的测试数据收尾。
- 触发：验收报告和 `cleanup manifest` 已生成，但 Dify 知识库中仍能看到本次验收新增文档。
- 根因：验收脚本负责上传测试样本、生成精确 manifest 和安全摘要；该入口本身不调用 `jx_release_data.py plan-clean/clean`。manifest 是删除授权边界，不是已经完成删除的证明。
- 解决：验收结束后先用同一份 manifest 做精确 plan 和前置校验，再执行 manifest-only clean；清理范围同时覆盖 manifest 指定的 Dify 文档、清洗批次、Redis key 和测试会话。重启/重构不会自动恢复或删除这些文档。
- 非解决：按文档名称、创建时间、知识库总数批量删除；恢复 Dify dump 后再判断；只删 Dify 文档而留下对应本地批次和 Redis 状态。
- 验证：jx release `20260809-125124-full-e06470c` 的 general manifest 目标 71 篇文档已按精确 manifest 删除；cleanup 报告显示删除 Dify general 文档 71 篇、清洗批次 19 个、测试会话 1 个，删除后目标文档重叠数为 0；course manifest 目标为 0，非目标课程目录保持不变。
- 预防：完整验收流程必须把“生成 manifest”和“按 manifest 清理”作为两个可观察阶段；最终报告同时记录清理退出码和删除后精确核对结果。
- 证据：`scripts/jx_full_cleaning_baseline.sh`、`scripts/jx_release_data.py`、`var/reports/jx-deploy-20260809/issues.jsonl`、jx 报告 `/opt/ai-management/var/reports/v2/20260809-125124-full-e06470c-baseline`。
- 最后验证：2026-08-09。

## DATA-043：PDF 转 Markdown 的目录必须映射正文父块并在输出中删除

- 适用：`lead_magnet`/钩子资料 Markdown 的确定性父子分块脚本，尤其是含 `1/`、`2/` 等编号目录的大文档。
- 触发：目录条目没有全部形成父块；清洗结果仍把目录作为正文保留；只依赖正文长度阈值会漏掉短但必须独立检索的章节。
- 根因：旧逻辑只把“目录”作为合集信号，要求多个正文 H2 足够长后才切父块；没有把目录条目与正文标题建立结构化映射，且前置目录合并逻辑可能移除首个有效父边界。
- 解决：先识别导航标题后的连续列表条目；统一编号、空白、标点、Markdown 链接和独立页码后，与后续同层级正文标题做唯一匹配；每个唯一匹配标题强制形成父块并保护其不被短块裁剪；删除导航标题和条目行，保留标题、作者和简介等前置内容。PDF/OCR 仅在同层级标题数量相同且至少 95% 行序精确一致时使用顺序回退，禁止语义模糊猜测。
- 非解决：按具体作者、文件名、文章标题、字符位置或关键词特判；把目录条目本身作为知识父块；用编辑距离或模型猜测不唯一标题；覆盖原始 Markdown 或写回远端输入目录。
- 验证：2026-08-09，本地与 jx 真实目录均为 `files=5`、`warnings=0`；含目录文档 `25/25` 条目匹配、`25/25` 映射父边界、删除 `701` 个目录字符；修复后总计 `parents=59`、`children=169`；标准库回归 `40/40`、500-case fuzz `failures=0`；隔离输出二次运行文件哈希全部一致。
- 预防：目录规则变更必须同时检查“目录删除、正文标题父块覆盖、前置内容保留、正文可见字符守恒、幂等”；pytest 不可用时记录未执行并运行包内无依赖断言，不联网安装依赖。
- 证据：`文件清洗样本/钩子资料清洗/chunk_lead_magnets.py`、`validate_chunker.py`、`test_generic_chunking.py`；本轮脱敏报告见 `var/reports/20260809-lead-magnet-toc-parent/`。
- 最后验证：2026-08-09。

## DATA-040：知识星球标题也必须纳入匿名化边界

- 适用：独立 `qwen_rag_cleaner.py` 的 H1、`source_title`、debug 和输出文件名。
- 触发：原始 H1 或文件名包含作者名，正文已匿名但 Metadata/文件名仍暴露原名。
- 根因：旧 `load_source` 只对正文调用匿名化，直接使用原始 H1 作为标题和输出命名输入。
- 解决：提取作者后先匿名化 H1，再用于 `SourceDoc.title`、Metadata、debug 和清洗文件名；正文重复标题删除仍使用原始标题匹配，避免残留导出标题。
- 非解决：只隐藏正文、只改文件名而保留 `source_title`，或把作者名替换成空字符串导致标题语义丢失。
- 验证：2026-08-09 jx 合成 H1/作者同名回归中标题与正文均不含原名；真实 3 篇清洗结果作者泄漏为 `0`。
- 预防：清洗验收同时扫描 Markdown、Metadata、JSON/debug、输出文件名和标题，不只扫描正文。
- 证据：[2026-08-09 jx 隔离测试记录](../../var/reports/20260809-zsxq-rag-jx/README.md)、[脱敏 issues](../../var/reports/20260809-zsxq-rag-jx/issues.jsonl)、`文件清洗样本/知识星球模型清洗/qwen_rag_cleaner.py`。
- 最后验证：2026-08-09。

## DATA-041：知识星球认知链覆盖审计必须先做结构兼容，再执行严格证据门禁

- 适用：v2 独立 `qwen_rag_cleaner.py` 的 qwen-plus 认知链抽取、覆盖审计和遗漏单元补抽。
- 触发：模型以 JSON Object 返回浮点评分、超出数组上限、单字符串 `caveats` 或过短 `source_actions.evidence_quote`；严格 Pydantic 校验导致首轮/审计失败，清洗文档减少或整篇误落 DROP。
- 根因：JSON Object 只保证 JSON 语法，不保证字段类型、数组边界和证据长度；原实现让结构校验在确定性证据门禁和定向修复之前截断流程。另有来源外方法/案例术语只在 `business_mapping` 检查，可能漏到问题、洞察或检索问法字段。
- 解决：诊断分数允许小数；Prompt 明确受控数组边界和枚举；单字符串 `caveats` 只做无语义数组包装；`GroundedSourceItem.evidence_quote` 先允许结构解析，最终仍由 `validate_unit` 严格执行最小长度和逐字命中校验；首轮与修复失败写入脱敏双层诊断；对全部知识单元语义字段统一拦截来源不存在的 `5Why/SOP/价值链/门店试点/跨部门会议`，并进入有限定向修复。
- 非解决：去掉逐字证据校验、用去空白匹配伪造 quote、把审计失败直接当 DROP，或把所有模型返回字段无上限放行。
- 验证：2026-08-09 jx 最新覆盖重生成 `coverage_audit=true`，3/3 无文档级错误，`KEEP/5`、`KEEP/2`、`KEEP_WITH_MAPPING/2`；`cleaned=3`、`metadata=3`、`dropped=0`；73 条输出证据逐字命中匿名化原文，作者泄漏 0，来源外术语命中 0，metadata 无 `case_id`。本次模型覆盖审计仍报告部分压缩/映射问题，未将其误报为全覆盖通过；不因审计补抽而放宽证据门禁。
- 预防：模型 Schema 负责结构，Python 负责来源证据和语义门禁；每次模型或 Prompt 调整都要先本地回归，再用并发 1 覆盖 jx 输出，并核对 `summary/debug/cleaned/metadata` 的守恒关系。
- 证据：[v2 jx 测试记录](../../var/reports/20260809-zsxq-rag-v2-jx/README.md)、[脱敏 issues](../../var/reports/20260809-zsxq-rag-v2-jx/issues.jsonl)、`文件清洗样本/知识星球模型清洗_v2/qwen_rag_cleaner.py`。
- 最后验证：2026-08-09。

## DATA-034：验收 manifest 之外的独立测试 Dataset 和当前 release 批次必须单独对账

- 适用：完整清洗验收、课程域测试归零和同一 release 内再次运行的测试上传共存时的数据收尾。
- 触发：manifest 目标已清空，但知识库仍显示测试文档，或数据库/Redis 仍有本次 release 的清洗批次。
- 根因：manifest 只授权它捕获的精确 Dify 文档和批次；历史独立 Dataset（例如 `business-course-test`）以及验收后新产生的当前 `build_version` 批次不会自动进入旧 manifest。课程域脚本结束后重建目录快照还可能重新写入一个空的课程快照 key。
- 解决：先只读核对 Dify Dataset 的真实名称与 ID，再通过 Dify API 删除独立测试 Dataset 中的精确文档；课程数据使用课程域归零脚本；当前 release 使用精确 `build_version` 生成新的 v3 manifest，清理其 Dify 文档、批次级联数据、发布分片、Redis key 和上传目录；最后再删除已无课程数据的精确 `ai:course:snapshot` key。
- 非解决：按文档名称、创建时间、Dataset 总数或 Redis DB 批量删除；直接修改 Dify 数据库；用旧 manifest 推断后续测试批次已清理；整库 `FLUSHDB`。
- 验证：2026-08-09 jx 实测 `business-course-test` 文档 `110→0`、配置 `course` 文档 `22→0`、当前 release 批次 `6→0`；最终 general 保留 `264` 篇，课程快照 key 为 `0`，API ready、测试页 HTTP `200`。
- 预防：验收收尾必须同时输出 Dataset inventory、当前 release `build_version` 对账、数据库/Redis/上传目录精确复核和最终服务健康检查；空快照 key 的删除必须放在所有会调用 `rebuild_snapshot()` 的清理动作之后。
- 证据：`scripts/jx_reset_course_document_data_test.sh`、`scripts/jx_release_data.py`、`var/reports/jx-deploy-20260809/issues.jsonl`、jx 备份 `/opt/ai-management/backups/20260809-141924-course-document-data-reset` 和清理报告 `/opt/ai-management/var/reports/v2/20260809-142437-current-build-cleanup`。
- 最后验证：2026-08-09。

## DATA-035：Dify 直接 API 上传文档可能脱离本地清洗批次

- 适用：Dify Dataset 与 AI 管理服务本地清洗批次、发布分片并存的测试环境清理。
- 触发：旧 manifest 或本地 `CleaningBatch` 已清空，但 Dify 知识库仍显示测试文档。
- 根因：直接调用 Dify `create-by-file`/`create-by-text` 的路径可以创建远端文档而不写入本地 `CleaningBatch`/`CleaningDocument` 关联；只按本地关联生成 manifest 会漏掉这些远端文档。本次实测 `general` 的 `263` 篇中有 `237` 篇没有本地关联。
- 解决：冻结上传写入者；对 Dify Dataset 做只读全量 inventory；将全部精确 Dify document ID 与已证明属于测试范围的本地批次/Redis/上传目录合并生成 v3 manifest；通过现有 API 清理链路删除并复核 Dify、数据库、Redis、上传目录。不得用名称、时间窗口或总数猜测删除范围，也不得直接修改 Dify 业务数据库。
- 非解决：只删除本地 `CleaningBatch`、只执行旧 manifest、整库 `FLUSHDB`、按文档名称/创建时间批量删除，或直接删除 Dify PostgreSQL 业务记录。
- 验证：2026-08-09 jx 使用报告 `/opt/ai-management/var/reports/v2/20260809-143906-general-document-cleanup` 完成 `general=263→0`；`course`、`business-course-test`、`business-general-test` 均为 `0`；本地清洗表和清洗 Redis 键为 `0`，课程快照精确删除后为 `0`。
- 预防：清理完成的判据必须同时包含 Dify Dataset inventory、本地数据库、Redis、上传目录和写入日志观察窗口；不能以“本地批次为 0”替代 Dify 全量核对。
- 证据：`scripts/jx_release_data.py`、`scripts/jx_reset_all_document_data.py`、jx 计划报告 `/opt/ai-management/var/reports/v2/20260809-143812-all-document-data-plan`、清理报告 `/opt/ai-management/var/reports/v2/20260809-143906-general-document-cleanup`。
- 最后验证：2026-08-09。

## DATA-036：意外上传事件必须生成带归属门禁的事故清理 manifest

- 适用：已证明为意外上传、但没有完整验收 manifest 或测试页运行标签的 jx `general/course` 清洗批次。
- 触发：数据复发后只有精确本地批次和关联 Dify 文档可观察；现有 `capture-ui-manifest` 因批次不带测试页标签而拒绝，且不能安全套用 UI 或自动验收 scope。
- 根因：原 v3 清理只允许自动验收和已带 UI 运行标签的手工测试，以防按本地关联或文件名扩大删除范围；意外公网写入不具备该预先标记的 provenance。
- 解决：`scripts/jx_release_data.py capture-incident-manifest` 使用独立 `manual_incident_cleanup` scope 和短期 run ID。它只接收明确 batch ID；只从这些批次的本地 document/shard 收集精确 Dify ID；并要求当前所有课程和路线都能由这些文档的 catalog 审计字段证明归属。可选 `CourseDirection` 表只删除实际存在且已证明不在批次外的 code，避免把文档语义方向误当成表 code。随后仍必须 `plan-clean`、停写入者、`clean`、按私有结果精确移除上传目录并复核。
- 非解决：伪造 UI 标签或旧 manifest、按创建时间/文件名/build version 直接删除、将全量课程/路线写入 manifest、用文档语义方向值删除 `CourseDirection`、整库清 Redis，或直接修改 Dify PostgreSQL。
- 验证：2026-08-09 jx 首次 capture 因方向命名空间不一致安全拒绝，修正归属门禁后仅生成 3 批次、`course=22`、`general=4`、21 门课程和 1 条路线的 0600 manifest/plan；清理后两个 Dataset、清洗表、课程/路线、精确 Redis 键、课程快照和上传卷均为 `0`，API ready、测试页 HTTP `200`。
- 预防：意外写入的正常处置顺序是先阻断写入入口、再备份和只读 inventory，最后生成事故 manifest；没有完整归属证明时应停止并保留现场，而不是扩大删除条件。
- 证据：`scripts/jx_release_data.py`、`tests/test_release_data_cleanup.py`、`var/reports/jx-unexpected-knowledge-20260809/issues.jsonl`、jx 报告 `/opt/ai-management/var/reports/v2/20260809-073700-unexpected-knowledge-cleanup`。
- 最后验证：2026-08-09。

## DATA-037：咨询会议 RAG 抽取必须将可回溯事实、建议与未来动作分层门禁

- 适用：独立 `consultation_rag_cleaner.py` 将咨询会议 Markdown 拆分为相似案例的本地/jx 隔离测试。
- 触发：模型返回的 evidence quote 无法在匿名化原文定位，或把顾问建议、计划、准备、预计等未来动作写入 `relevant_background` / `problem_signals`；模型也可能把未经原文支持的建议渲染为案例结论。
- 根因：JSON Schema 只能约束结构，不能证明引用确实来自匿名化输入，也不能区分“原文出现过”与“已经发生的事实”。仅靠 Prompt 无法稳定阻止这两类语义越界。
- 解决：先进行确定性匿名化；只接受可在匿名化原文逐字定位的 background/problem evidence（允许明确省略号连接的顺序片段）；无同时具备两类核心证据的案例直接丢弃。advice 仅从可定位 advice evidence 派生；对背景和问题表现保守剔除建议、判断、计划与未来动作。检索问法先剔除纯英文项，不足三条时只围绕已通过校验的 `primary_problem` 生成中文兜底问法；结构/语义错误最多触发一次模型定向修复。
- 非解决：把模型改写后的 advice 当作事实写回、因追求案例数量放宽 evidence 门禁、把原文姓名/正文写入日志或报告，或为每个字段再发起模型调用。
- 验证：本地 `test_consultation_rag_cleaner.py` 覆盖未验证建议剔除、核心证据不足案例剔除、建议/未来动作不进入事实字段、中文检索问法和匿名输出；2026-08-09 jx 隔离三样本验收生成 9 个案例，父块、Dify 标记块、metadata 与检索钩子数量一致，事实字段扫描为 0 项命中。随后直接使用 jx `/opt/ai-management/tmp/模型/咨询会议总结/` 的实际 2 份 Markdown 复测；首轮 1 成功/1 因英文问法失败，加入兜底后最终 2/2 成功、8 个案例、英文问法 0 条。
- 预防：每次修改 Prompt、Schema 或渲染逻辑后，先执行 dry-run，再用阶段不同的三份样本复跑；报告只保存匿名 source ID、计数、类别和脱敏校验结果。
- 证据：`文件清洗样本/咨询会议模型清洗/consultation_rag_cleaner.py` 的 evidence/事实过滤与校验；`test_consultation_rag_cleaner.py` 回归；jx `/opt/ai-management/tmp/consultation-rag-isolated-20260809/` 隔离目录（不含服务配置、Dify、数据库或 Redis 写入）。
- 最后验证：2026-08-09。

## DATA-038：Qwen 结构化抽取应关闭思考、固定低随机参数，但不能承诺逐字复现

- 适用：百炼 OpenAI 兼容接口上的 Qwen 3.7 非思考结构化 JSON 抽取。
- 触发：思考模式占用输出配额、增加非流式请求超时/截断风险；同一匿名输入在多次新调用中可能产生不同的案例顺序、措辞或 advice 数量。
- 根因：Qwen 3.7 默认可启用思考，且服务端生成即使指定 seed 也只尽量复现，不保证字节级确定性。
- 解决：调用时使用 `extra_body={"enable_thinking": false}`、非零低温度 `0.01` 与固定 `seed=1234`；保持单次结构化抽取和至多一次定向修复。成功结果写入本次输出目录后默认跳过同一来源，避免无必要的新调用改变已验收结果。
- 非解决：把 temperature 设为 0、无限重试直到得到偏好的文案、因结果措辞变化而跳过 evidence/匿名/事实校验，或把测试凭据写入源码和 `.env`。
- 验证：本地回归断言请求参数；jx 同一匿名转化样本双跑在低温度下均得到相同案例数量、类别集合和阶段，全部通过结构、证据、中文问法及事实字段扫描；允许仍存在字节级差异，故全量前保留小样本人工抽检。
- 预防：使用官方兼容接口文档核对模型参数；对新的模型别名或快照先做重复小样本验收，不用单次成功代替稳定性结论。
- 证据：`文件清洗样本/咨询会议模型清洗/consultation_rag_cleaner.py:34`、`:568`，`test_consultation_rag_cleaner.py:49`；[百炼 Chat Completions 参数文档](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)。
- 最后验证：2026-08-09。

## DATA-039：知识星球独立脚本的 qwen-plus 契约失败不得落成 DROP

- 适用：独立 `qwen_rag_cleaner.py` 对知识星球/认知分享 Markdown 的隔离清洗。
- 触发：`qwen-plus` 使用 `json_object` 返回缺字段、非法枚举或错误数组类型；模型异常后输出目录出现 `dropped`，但没有可验证的内容判断。
- 根因：`json_object` 不等于 Pydantic Schema；旧 Prompt 没有完整 JSON 骨架，顶层契约错误无法进入知识单元修复；处理逻辑还用“没有 accepted unit”代替模型失败状态。
- 解决：Prompt 明确不可信原文边界、完整键/数组/枚举骨架；契约错误只走一次 SegmentResult 结构修复；仍失败或 `decision` 与 `knowledge_units` 冲突时返回 `ERROR`，不生成 `cleaned/dropped`；仅对限流、超时、连接和 5xx 重试。证据 quote 使用严格字面匹配，debug/summary 只保存匿名 `source_id/output_id` 和脱敏错误字段。
- 非解决：把契约错误重试到成功、把契约失败当作 `DROP`、放宽 Pydantic 字段/枚举或用去空白匹配伪造 evidence 通过。
- 验证：2026-08-09 jx 隔离测试首轮 3/3 契约失败、9 次调用且被旧实现误判为 DROP；修复后同 3 篇为 `KEEP_WITH_MAPPING/2`、`KEEP/3`、`KEEP_WITH_MAPPING/2`，共 7 次调用、0 错误；合成注入样本为 `DROP/0` 且未生成 `industry/case_id`。原始输入哈希不变，未写服务/Dify/数据库/Redis。
- 预防：先 dry-run，再用并发 1 的小样本验证；以 `ERROR` 与 `DROP` 分开统计；人工复核 `direct`/`mapped` 边界后才能扩大批量。清洗脚本的异常日志不得写原始路径、Pydantic `input_value`、正文或模型 JSON。
- 证据：[2026-08-09 jx 隔离测试记录](../../var/reports/20260809-zsxq-rag-jx/README.md)、[脱敏 issues](../../var/reports/20260809-zsxq-rag-jx/issues.jsonl)、`文件清洗样本/知识星球模型清洗/qwen_rag_cleaner.py`。
- 最后验证：2026-08-09。

## DATA-042：咨询会议批量清洗必须防止并发覆盖、半成品跳过和旧产物残留

- 适用：独立 `consultation_rag_cleaner.py` 对咨询会议 Markdown 做多文件、可续跑的模型清洗。
- 触发：批量运行中断或重复启动；输出目录出现半成品；输入源减少后旧 `SRC_*` 案例仍被下游批量导入；Windows 上锁文件检查导致进程异常退出。
- 根因：普通覆盖写可能留下截断 JSON/Markdown；仅凭结果 JSON 判断完成会跳过缺 sidecar 或缺父子块的源；共享输出目录没有互斥锁和模型请求总上限；排队任务会在等待模型槽位前先读入大正文；Windows 的 `os.kill(pid, 0)` 不是安全的存活探测；聚合文件过滤旧 sidecar 但不会自动移除旧案例文件。
- 解决：采用同目录临时文件 + `os.replace` 原子写；sidecar 保存源文件 SHA-256 指纹，只有 JSON、sidecar、父块和 Dify 块全部齐全且指纹一致才跳过；把并发槽位覆盖“读取→模型→写出”整个源文件生命周期；增加跨平台运行锁、共享请求预算和错误码；完整批次按当前输入的 `SRC_*` 集合精确清理脚本生成的孤儿产物，`--limit` 局部测试不清理其他批次。
- 非解决：多个进程复用同一输出目录、把 `--max-model-requests` 当成文档数量、用文件存在即视为成功、按名称正则清理所有 Markdown，或在 Windows 继续使用 `os.kill(pid, 0)` 判断锁归属。
- 验证：本地 `test_consultation_rag_cleaner.py` 共 20 项通过，包含 100 文件并发模拟、请求预算、半成品/源变更重跑、孤儿产物清理、生命周期并发槽位和 Windows 锁行为；2026-08-09 jx 实际目录 2 份 Markdown 以并发 1 强制覆盖完成 2/2、8 个案例、2 次请求，英文检索问法 0、事实字段污染 0、指纹缺失 0、Dify 标记块 8/8、输入路径/密钥泄漏 0，锁已释放；覆盖前同一输出目录续跑为 0 次请求、2 个跳过、0 个失败。
- 预防：几百份文档按 10~50 份批次执行，先 `--dry-run`，固定 `--concurrency 1~2`、重试和请求上限；每批核对 `run_report.json`、父块/Dify/metadata/hooks 守恒关系后再继续，不在 jx 测试中改服务配置、Dify、数据库或 Redis。
- 证据：`文件清洗样本/咨询会议模型清洗/consultation_rag_cleaner.py`、`test_consultation_rag_cleaner.py`、`README_运行说明.md`；jx 隔离输出 `/opt/ai-management/tmp/consultation-rag-final-audit-20260809-v2/run_report.json`（仅保存测试结果，不写入服务数据）。
- 最后验证：2026-08-09。
