# AI 管理服务当前状态与后续测试交接

> 更新时间：2026-08-07
> 用途：保存最新线上事实、回滚点、数据状态和开放事项。新任务先读 `AGENTS.md` 与 `docs/README.md`，再按功能域读取本文顶部最新状态。  
> 本文不是需求或设计基线；权威顺序以 `docs/V2决策记录.md` 和 `docs/README.md` 为准。第 1～8 节为历史过程记录，不再作为当前执行指令。

## 0.37 2026-08-06 学习路线阶段课程集名称与 jx 完整发布（当前最新）

- 已按用户确认补充 `learning_route` SSE 事件的阶段字段 `course_collection_names: string[]`。字段只返回课程集名称，不返回课程名称或 ID；名称按该阶段现有 `course_ids` 的原始目录顺序首次出现去重，没有课程集时返回空数组。实现从既有 `ai:course:snapshot` 的 `collections` 运行时派生，不新增 Redis Key、数据库字段或迁移。
- 本地验证：课程/路线定向回归 `22 passed`，商业顾问/路由/应用回归 `13 passed`，全量 pytest 收集 `339 passed`；目标代码 Ruff、Python 编译、功能索引检查和 `git diff --check` 通过。
- jx 快速发布第一次在核心导入 smoke 阶段发现旧运行时缺少 `knowledge_cleaner.markdown_table_cleaner`，未切换 release，自动回滚并确认旧服务 readiness 正常；随后改用包含 `ai_management` 与 `knowledge_cleaner` 的完整 wheel 发布成功。release `20260806-195815-course-collections`，wheel SHA-256 `d53b6603d0d467e1f397f33c3a0f2af21ba8c3d83ef59f8fdffd9eab037a6692`，发布前备份 `/opt/ai-management/backups/20260806-195815-course-collections-predeploy`，报告 `/opt/ai-management/var/reports/v2/20260806-195815-course-collections-deploy`。
- 发布后 API、Worker、实时/Batch 模型 Worker、发布 Worker、Scheduler、UI 使用同源新镜像；`health/ready=ready`，数据库/Redis/Dify 均可达，迁移为 `j6a7b8c9d0e1 (head)`，测试页 HTTP 200，OCR autoscaler timer 为 `active`。队列空闲时 OCR Worker 按 autoscaler 退出，不作为发布失败。
- 远端直接 SSE 验收通过：`learning_route` 事件存在，路线 `AI高手提效课` 返回 2 个阶段；每个阶段均有数组字段，示例阶段课程集名称为 `公众号AI爆款`、`AI写作课`/`AI智能体写作`/`AI阅读博主`/`AI知识库`。合成会话已删除；未清理正式课程、路线、Dify 数据、Redis 整库或持久卷，未执行 Git commit/push。

## 0.38 2026-08-07 商业顾问学习路线、综合点评与课程目录全链路验收（当前最新）

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
