# 文档清洗容错、咨询方案方法与隔离测试修复 — Requirement Spec

- Feature ID: `cleaning-tolerance-methodology`
- Status: USER-APPROVED; GOVERNANCE APPROVAL PENDING CURRENT FINGERPRINT
- Risk: `high`

## 当前交付范围（2026-09-29）

以 jx 正式运行版为只读基线，在现有 ys 专用服务验证清洗分支的完整差异。复用 ys 已有的 `general` 知识库及商业顾问、朋友圈顾问、日常写作、摘要画像四个 App；课程库关闭。既有调用方零改造，不要求真实小程序后台实例联调。完成条件为全部 15 份六类真实样本的上传、清洗、索引、正文/Metadata 回读和来源可核对检索，以及受影响接口、管理 UI 和四 App 业务验收；证据按 EV-01～EV-08 归档，并保留 AC-001～AC-007 的功能判定。

真实模型、Dify 索引/检索和四 App 及后台重试共用可持续核对的预算账本，含本任务旧账、未结算预留及当次最坏费用；累计上限 ¥5，北京时间单日上限 ¥4。整套费用上界不能证明在限额内时不启动付费阶段。咨询与知识星球各固定一份做 Realtime/Batch 真正对照，两路独立合格且语义一致前暂缓发布，通过后仅发布 Realtime canonical；默认 Batch 关闭。Markdown 的 17 张外链图不抓取或识别；PDF 内嵌图按现有 OCR 流程验证。仅精确清理本轮临时成果并保留合格最终成果。

## 1. Goal

在不重建现有 FastAPI/Celery/Dify 清洗架构、不影响 `recent_activity` 的前提下，修复正常知识单元因辅助字段、混合 PDF 或失效去重被错误阻止的问题；使咨询清洗保留有来源的解决方案并自动产生有证据边界的方法候选；更新 ys 现有专用服务候选，验证四个内容模型策略的 Batch/Realtime 合同与最终知识库业务效果一致，完成本次变更范围的远端验收。

### 本次交付的核心规则

交付目标是将当前候选受控更新到 ys 现有专用服务：API、管理 UI、清洗/OCR、模型处理、发布和调度组件同源运行。复用 ys 现有 Dify 1.13.3、PostgreSQL、Redis 和 Docker 网络，复核现有项目专用数据库/角色、Redis DB 6、指定四 App 与已有 `general` Dataset；现有服务和数据保持不变。入口限本机/SSH 隧道，Batch 默认关闭。普通测试问题先记录，完成其余安全有效的范围内测试后集中定位根因、作最小修复，再对同一最终候选复测。完成依据是同一候选的远端业务验收与共享服务前后对照，不能以本地 Quality、部署命令成功或 HTTP 200 代替。内容绑定的治理门禁仍按工具的实际状态执行。

## 2. Non-goals

- 不自动恢复、改写或清理历史正式批次和 Dify 文档。
- 不新增 `record_type`、新状态体系、跨来源 Registry 或第二套清洗系统。
- 不改变 `recent_activity` 的模型请求、解析、重试或业务语义。
- 不改变正常运行默认 `Batch=false`，不让 `recent_activity` 进入 Batch；不直接修改 Dify 业务数据库、不覆盖或删除已有 App/Dataset/卷、不 Git push 或部署到 jx。

## 3. Background / Evidence

- 咨询契约将分类、阶段、检索问法、evidence 标签和逐字方案匹配错误地作为全局硬门槛，造成全份或方案内容被拒绝。
- 帖子路径有 50 个引用上限与来源块覆盖强制，能连带拒绝合法知识单元。
- 混合 PDF 的 image-only 页面被提前送入 review 分支，未调用现有 RapidOCR 后端。
- 已删除或失效的远端发布成果仍可能阻塞去重。
- 用户已逐项确认容错、隐私、来源、状态、样本、隔离部署和验收边界，且授权实施与本地测试。

## 4. Actors and Scenarios

- 知识库运营者上传六类样本文档，期望正常内容进入通用知识库并可追溯。
- 咨询知识使用者检索某案例时，获得问题、必要条件、原文支持的方案；检索候选方法时，获得场景、判断、步骤及单来源证据边界。
- 运维者在 ys 更新现有项目专用服务，验证其只消费专用 PostgreSQL 数据库、Redis DB 6、卷、指定四 App 和已有 `general` Dataset；共享 Dify、PostgreSQL、Redis 及其他服务保持运行。
- 验收者对四个指定策略做本地确定性 Batch/Realtime parity，并只对咨询与知识星球执行预算内真实双 transport 对照。

## 5. Functional Requirements

### FR-001 单元级容错和有限重试

受影响的帖子和咨询清洗按独立知识单元保留内容。辅助字段缺失或不规范仅降级；来源、隐私、核心事实、可检索内容或必要条件不满足时拒绝该单元。仅模型异常或全部核心单元不可用时，对同一匿名化快照完整重跑一次；仍无可用核心内容为 `failed`，不产生新的 `review_required` 或原文兜底发布。

### FR-002 咨询方案和候选方法

`consulting_case` 保留问题、必要背景/目标/约束和有 evidence 回指的忠实方案整理。方案可作非逐字的忠实改写，但关键事实、主体、条件、否定和行动必须能回溯到当前 case 的 advice evidence；无依据扩写、主体替换、否定反转、新承诺或新行动必须丢弃。`primary_problem` 必须锚定该 case 的 `problem` evidence，必要背景锚定 `background` evidence，问题表现锚定 problem/background evidence；缺失或未知的辅助 kind 只在仍存在直接字段锚点时兼容，不能让显式不相容的 evidence kind 充当证明。具体锚点不得由“客户需求”“用户数据”“业务问题”等通用组合或单一短 ASCII 产品词充当；核心事实仍须有具体共享锚点。确定性校验只能拒绝已知无依据反例，不能把受控同义词白名单定义为产品允许的全部改写。同步产生可选 `methodology` 候选；每个 `case_scoped` 候选只能来自一个已验证 `consulting_case` 内关联问题、条件和方案的 evidence。不同背景的策略不得按问题文本误合并，单来源候选须表达证据边界，不能包装为跨来源验证的通用规律。

### FR-003 帖子引用与兼容解析

取消与文档规模无关的 50 个来源块上限和 `keep/drop` 全覆盖硬要求。保留对已知来源 ID、事实、数字/日期、隐私及可检索核心内容的校验。`recent_activity` 的现有行为不变。

### FR-004 混合 PDF 和错误分类

混合 PDF 可靠文字页保留，image-only 或低可靠页经既有 RapidOCR 处理后按页序合并；咨询图片页仍覆盖 OCR/匿名化检查。OCR/转换失败保持精确失败原因，不能误报为空正文或排除。

### FR-005 有效去重和测试隔离

仅已证明 404/删除、禁用/索引失效或错误归属的远端关联才释放精确重新发布；暂时不可验证的关联不可复用，但也不视为不存在。复用必须同时核验受控 metadata 和 Dify 实际分块正文 witness。正文 witness 是版本化的可检索父/子块快照，绑定稳定位置/标识、内容和启用状态；其顶层及嵌套 envelope 的 `has_more`/`total` 必须一致解释，每页在追加前计入全局条目上限，并拒绝重复、空页推进或总数不一致响应。父块或 hierarchical child chunk 缺失、禁用、错误或不一致时必须失败关闭。`recent_activity` 不参与本任务新增的 witness 读取。post shard planner 不得用本地 `succeeded`、pending 或 indexing 标记生成终态 `duplicate_noop`，也不得在 shard 已进入 indexing 后再关联后到条目；同知识库、策略、契约和最终正文 digest 的跨文件候选只能被携带为待远端复验关联。分片在重发前同样须以精确 Dataset/document ID、completed/enabled 状态、受控 metadata 和当前 parent/child witness 验证；验证读取超时或格式异常保留 publication-only 可重试状态，`create_document` 不得调用、不得盲目新建或回源重跑模型；已读到的 metadata 不一致才允许精确替换，验证有效则复用而不触发新的 Dify 索引。realtime 请求审计也必须在当前 token 围栏内持久化。在现有 ys 服务更新前复核项目专用 PostgreSQL 数据库/角色、Redis DB 6、独立卷/服务名/回环端口与共享服务隔离；课程关闭，不重跑首次安装脚本、不重复迁移、不以清库作为前提。启动 manifest 除短时效、预算与运行归属外，还必须绑定并在启动前重读 API/UI/OCR 的不可变 Docker image ID、受治理当前工作树指纹、wheel SHA-256 与镜像 label；服务端验证仅向指定 `general` 写入并拒绝课程写入。

### FR-006 Batch/Realtime parity 与 canonical 发布

需要模型 Batch/Realtime parity 的策略仅为 `personal_business_consultation`、`wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post`；`recent_activity` 保持 realtime-only，`course_route_catalog` 不纳入本次对照且既有功能不得受损。四策略全部完成免费、本地、确定性的请求、解析、业务校验、metadata、chunking、parent/child、幂等与发布裁决合同验证；真实付费对照只覆盖 `personal_business_consultation` 和 `zsxq_post`。正常运行保持 Batch 关闭；显式 Batch acceptance 只在隔离环境、受控 manifest、策略白名单、预算和正式健康门禁同时成立时启用，且不得越过项目专用 Dataset 隔离。

Realtime 是不同 digest 时的固定 canonical 候选。Realtime 与 Batch 必须分别通过同一来源、隐私、事实、结构、方案、方法和检索意图校验。任一不通过或两者语义不等价时 parity 失败、两份均不发布，不以一边成功掩盖失败且不自动追加第三次调用。两者通过且 digest 相同时发布 Realtime，Batch 经远端有效性验证后 `duplicate_noop`；digest 不同但业务语义等价时仅发布 Realtime，Batch 只保留脱敏对照证据。不得主观改变 canonical transport，也不得为对照向目标 Dataset 写入两份语义重复文档。

## 6. Applicable Constraints

- 受保护身份不得通过正文、metadata、日志或普通测试报告泄露；不得虚构人物、数字、日期、价格、权益、结果或承诺。
- 测试样本及报告按用户确定的 15 个文件和脱敏策略执行；合成数据只能补逻辑边界，不能替代真实语义验收。
- 远端操作只能使用现有 API；不直接写 Dify PostgreSQL。Batch 默认关闭，真实模型、Dify 索引/检索和未结算 reservation 的累计成本受本任务累计 ¥5、日 ¥4 上限保护。
- 保持已存在的 API/状态向后兼容，使用现有处理详情表达部分成功与未采用原因。

## 7. State / Data / Permission Rules

- `succeeded` 需发布、索引、metadata 和正文回读完成；`excluded` 仅限通过校验的不适合商业知识库；技术/内容为空失败为 `failed`。
- `duplicate_noop` 是经远端 metadata 与版本化、可检索正文 witness 验证的有效成果复用，不能当内容排除；本地 shard 状态、远端删除状态、失效 ID、缺失/禁用子块或失效 witness 必须使该关联不可复用。
- Worker delivery lease 与每文档 provider 调用次数分离：完成/精确重试释放旧 lease，持有当前 token 的 Worker 才能 reservation、写入文档状态或完成任务；Worker 必须在每个条目和限流等待后、费用预留前续租/重验 token，失 lease 异常直接终止旧 Worker，不得被修复、兜底或失败处理吞掉；显式正数 `model_attempts` 必须与该 document 的完整、有效 durable reservation/call-ID ledger 数量严格一致，缺失、损坏、冲突或外来账本均 fail-closed；消息重投不能消耗第三次 provider 调用。
- 咨询案例的核心问题、必要背景和问题表现必须由各自允许种类的来源证据完整锚定；不能以局部词重合把未出现的产品定位、供应链或其他新命题混入核心字段。历史 ledger 不完整、外来、重复或计数不一致时，在任何新的 Provider `create_batch` 前失败关闭；已上传 repair JSONL 仅进入带 `historical_ledger_inconsistent` 原因的精确人工处理 outbox，不回填或重建历史账本。
- terminal Provider Batch 的本地 owner 只能在其全部精确所属文件已删除，或已可靠转移到携带 `model_task_id`、`batch_id` 和文件类别的独立 file outbox 后关闭；file 删除在同一事务中消费自己的 owner。任一残余文件或 active sibling 的 owner/outbox 均须保留重试，不能因兄弟 Batch 终态、混合取消或模糊关联被一并清除。
- ys 更新必须在服务端限制写入指定 `general` Dataset 并拒绝课程写入，不能只隐藏前端控件。
- 测试 Dify 文档和未来清理权限必须保存精确 batch/file/item/shard/document ID 关联。

## 8. Error and Boundary Behavior

- 单个不安全或无来源单元的拒绝不得阻塞其他独立可用单元；但删除关键限制后会误导的内容不能保留。
- 没有方案/方法时不编造或暗示原文没有；既有可用 case 不额外触发模型调用补提。
- 冲突的 OCR 文本、缺页、低质量重试耗尽、发布失败和无法验证的去重关系必须给出稳定错误码和 `failed`。
- 混合 PDF 选择性 OCR 不得让咨询图片跳过脱敏。

## 9. Assumptions and Resolved Decisions

| Decision ID | Question / assumption | Decision | Evidence / reason | Alternatives rejected |
|---|---|---|---|---|
| RD-001 | 哪些字段阻止入库 | 只把核心内容、来源、隐私、事实和必要条件设为硬门槛；辅助字段降级 | 用户确认的单元级容错边界 | 因分类、阶段、问法或置信度整份重跑 |
| RD-002 | 方案/方法漏提如何处理 | 同次结果中有可用 case 时保留并记录质量缺口，不额外调用；同时修复逐字方案过滤并同次产出候选方法 | 避免费用和重试放大，同时改善方案/方法完整性 | 原文兜底、每次漏提补提、把单来源说成通用规律 |
| RD-003 | 混合 PDF | 保留文字页并 OCR 图像/低可靠页，按页序合并 | 已证实现有 OCR 存在，路由提前拒绝才是根因 | 更换 OCR 服务或整份拒绝 |
| RD-004 | ys 现有服务更新 | 复核项目专用数据库、Redis DB 6 和服务边界；复用指定四 App 与已有 `general`，课程关闭。预算保护独立于 general-only 开关 | 用户确认的现有 ys 资源与验收边界 | 重跑首次安装脚本或新建 App/Dataset |
| RD-005 | 部署与真实模型 | 当前候选在 ys 现有专用服务受控更新；入口仅回环，Batch 默认关闭。付费阶段前核清凭据、历史结算、未结算 reservation 和全部 15 份、双路对照、索引/检索、四 App 及允许重试的完整最坏费用；累计不超过 ¥5、北京时间单日不超过 ¥4，必要时分日执行；不能证明时继续免费准备 | 推进 ys 实际验收并保护共享服务、数据和费用 | 无上限模型调用、隐式远端写入或把旧 jx release 结论当作 ys 证据 |
| RD-006 | Batch/Realtime parity 与发布 | 四策略本地确定性 parity、咨询与知识星球真实付费 parity；Realtime 固定为不同 digest 时的 canonical 候选 | 覆盖咨询 case/methodology 与帖子 shard 两种代表产物，同时避免重复费用和语义重复入库 | 四策略全部真实付费、逐字比较模型文案、主观挑选结果、让 recent_activity 进入 Batch |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 辅助字段错误或一个坏单元不会阻塞其他合格单元；只有零核心内容/调用异常触发一次完整重跑，且无第三次、无新 `review_required`、无源文发布 | 覆盖 realtime；正常 Batch 保持禁用，隔离 acceptance seam 按 FR-006 例外验证 |
| AC-002 | 指定咨询对照显示已有方案未被逐字过滤；候选方法关联对应 case/evidence，并保留不同背景的策略差异 | 原文无方案时不编造 |
| AC-003 | 合法 50+ 来源引用可处理，未知来源、虚构事实、匿名化残留和无核心知识被拒绝；`recent_activity` 回归不变 | 真实/合成覆盖分开报告 |
| AC-004 | 冻结白名单中混合、纯扫描和长 PDF 的需 OCR 页进入 OCR，所有页按原顺序保留；OCR/转换失败有准确 `failed` 分类 | 覆盖咨询隐私路径 |
| AC-005 | 已证明删除/失效远端成果不阻止重传，有效远端成果复用且无重复/越权删除；超时/格式异常保持可重验且 `create_document=0` | 使用精确关联、metadata 和实际远端正文 witness 验证 |
| AC-006 | 同一最终候选的 API、管理 UI、清洗/OCR、模型处理、发布和调度在 ys 运行，`/health/ready` 通过；四个指定 App 与已有 `general` 实际绑定和调用，课程写入由服务端拒绝；项目 PostgreSQL/Redis 读写隔离；现有 Dify 服务与数据前后不受影响 | 入口限本机/SSH 隧道；Batch 默认关闭；可能计费动作另满足 ¥5 累计、¥4 单日预算门禁 |
| AC-007 | 四策略本地确定性 Batch/Realtime parity 通过；咨询与知识星球完成预算内真实双 transport 对照，并按 Realtime canonical、同 digest `duplicate_noop`、不同 digest 单份发布或失败均不发布的规则留下 Dify/检索证据 | 不要求模型措辞、远端 ID、时间戳逐字一致 |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error/negative | Security/permission | Compatibility/migration |
|---|---|---|---|---|---|
| AC-001 | 多 case 中可用单元发布 | 辅助字段缺失、坏单元混入 | 两次零核心内容失败 | 匿名化/来源保留硬门槛 | 历史状态不改写 |
| AC-002 | 有方案 case 与关联方法发布 | 同题不同背景策略 | 原文无方案、无依据方法 | evidence 必须可追溯 | 保持既有 record type |
| AC-003 | 51+ 合法引用 | 空 topic/query/confidence | 未知 block、虚构数字 | 来源 ID 和 PII 拒绝 | `recent_activity` 不变 |
| AC-004 | 文字+图像页合并 | 低可靠/空白页 | OCR 失败/冲突/超限 | 咨询图像仍匿名化 | 既有 OCR 后端兼容 |
| AC-005 | 有效远端复用 | 删除标记释放重传 | 无法验证远端失败 | 精确关联删除 | 历史记录可读 |
| AC-006 | ys 现有组件、指定四 App 与 `general` | 专用 PostgreSQL/Redis/卷/回环端口 | 配置缺失或共享资源误用时拒绝启动 | 无 secrets 输出；四 App 仅按已授权范围兼容修正并保留前版本，不新建 Dataset | Dify 1.13.3 及既有业务资源不受影响 |
| AC-007 | 四策略请求/解析/结果/发布合同一致；两项真实对照各自合格 | 同 digest、不同 digest 但语义等价 | 任一 transport 失败、语义不等价、预算不足 | 只写 canonical、无重复 Dataset 文档、无第三次调用 | recent_activity realtime-only；生产默认不变 |

本周期测试验收范围按直接受影响模块、完整现有差异和隔离风险分组执行：A/B 为 unit，C 为 integration，D/E 为 feature validation；保留 100k、既有 5F 及全部直接相关安全/部署/迁移检查，仅排除两个无直接关联的正式数据 reset 用例。该范围调整测试合同与验证证据，不改变 FR/AC 产品语义、正式资源边界、预算规则或旧全量失败证据。

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | 高风险工程变更的行为基线 | Generated and approved |
| Adaptive Prototype | Not needed | 无新 UI/交互设计；隔离保护可由契约测试验证 | Not needed |
| Implementation Spec | Required | 单一 GPT-5.6 Terra 写入负责人的可审计边界；机器生成批准块以 feature record 的内容绑定状态为准 | Generated; current content-bound approval recorded in feature.json |
| ADR / Ops docs | Recommended when verified | 隔离运行手册已存在；仅验证后同步可复用排障经验 | Triggered by verification |

## 13. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
