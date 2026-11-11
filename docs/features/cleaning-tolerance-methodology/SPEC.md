# 文档清洗容错、咨询方案方法与隔离测试修复 — Requirement Spec

- Feature ID: `cleaning-tolerance-methodology`
- Status: USER-APPROVED; GOVERNANCE REAPPROVAL PENDING
- Risk: `high`

## 1. Goal

在不重建现有 FastAPI/Celery/Dify 清洗架构、不影响 `recent_activity` 的前提下，修复正常知识单元因辅助字段、混合 PDF 或失效去重被错误阻止的问题；使咨询清洗保留有来源的解决方案并自动产生有证据边界的方法候选；验证四个内容模型策略的 Batch/Realtime 合同与最终知识库业务效果一致；提供可验证且与正式实例隔离的 jx 测试部署资产。

## 2. Non-goals

- 不自动恢复、改写或清理历史正式批次和 Dify 文档。
- 不新增 `record_type`、新状态体系、跨来源 Registry 或第二套清洗系统。
- 不改变 `recent_activity` 的模型请求、解析、重试或业务语义。
- 不改变正式和正常隔离运行默认 `Batch=false`，不让 `recent_activity` 进入 Batch，不为验收创建新 Dataset；不创建 Dify App 会话、不直接修改 Dify 业务数据库、不覆盖用户 Dify YAML、不 Git push 或生产发布。

## 3. Background / Evidence

- 咨询契约将分类、阶段、检索问法、evidence 标签和逐字方案匹配错误地作为全局硬门槛，造成全份或方案内容被拒绝。
- 帖子路径有 50 个引用上限与来源块覆盖强制，能连带拒绝合法知识单元。
- 混合 PDF 的 image-only 页面被提前送入 review 分支，未调用现有 RapidOCR 后端。
- 已删除或失效的远端发布成果仍可能阻塞去重。
- 用户已逐项确认容错、隐私、来源、状态、样本、隔离部署和验收边界，且授权实施与本地测试。

## 4. Actors and Scenarios

- 知识库运营者上传六类样本文档，期望正常内容进入通用知识库并可追溯。
- 咨询知识使用者检索某案例时，获得问题、必要条件、原文支持的方案；检索候选方法时，获得场景、判断、步骤及单来源证据边界。
- 运维者在 jx 临时切换到隔离测试 UI，验证其只消费测试 Redis、数据库、卷和 Dataset，并可安全恢复正式测试 UI。
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

仅已证明 404/删除、禁用/索引失效或错误归属的远端关联才释放精确重新发布；暂时不可验证的关联不可复用，但也不视为不存在。复用必须同时核验受控 metadata 和 Dify 实际分块正文 witness。正文 witness 是版本化的可检索父/子块快照，绑定稳定位置/标识、内容和启用状态；其顶层及嵌套 envelope 的 `has_more`/`total` 必须一致解释，每页在追加前计入全局条目上限，并拒绝重复、空页推进或总数不一致响应。父块或 hierarchical child chunk 缺失、禁用、错误或不一致时必须失败关闭。`recent_activity` 不参与本任务新增的 witness 读取。post shard planner 不得用本地 `succeeded`、pending 或 indexing 标记生成终态 `duplicate_noop`，也不得在 shard 已进入 indexing 后再关联后到条目；同知识库、策略、契约和最终正文 digest 的跨文件候选只能被携带为待远端复验关联。分片在重发前同样须以精确 Dataset/document ID、completed/enabled 状态、受控 metadata 和当前 parent/child witness 验证；验证读取超时或格式异常保留 publication-only 可重试状态，`create_document` 不得调用、不得盲目新建或回源重跑模型；已读到的 metadata 不一致才允许精确替换，验证有效则复用而不触发新的 Dify 索引。realtime 请求审计也必须在当前 token 围栏内持久化。提供最小隔离测试部署定义：专用数据库、固定 Redis DB 6、独立卷/别名/端口和固定测试 Dataset；既有 DB 6 可以非空，但启动前必须证明其键、队列与引用均属于该隔离测试，禁止清库作为恢复前提。启动 manifest 除短时效、预算与运行归属外，还必须绑定并在启动前重读 API/UI/OCR 的不可变 Docker image ID、受治理当前工作树指纹、wheel SHA-256 与镜像 label；测试模式在服务端强制 general-only。

### FR-006 Batch/Realtime parity 与 canonical 发布

需要模型 Batch/Realtime parity 的策略仅为 `personal_business_consultation`、`wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post`；`recent_activity` 保持 realtime-only，`course_route_catalog` 不纳入本次对照且既有功能不得受损。四策略全部完成免费、本地、确定性的请求、解析、业务校验、metadata、chunking、parent/child、幂等与发布裁决合同验证；真实付费对照只覆盖 `personal_business_consultation` 和 `zsxq_post`。正常运行保持 Batch 关闭；显式 Batch acceptance 只在隔离环境、受控 manifest、策略白名单、预算和正式健康门禁同时成立时启用，且不得关闭 general-only。

Realtime 是不同 digest 时的固定 canonical 候选。Realtime 与 Batch 必须分别通过同一来源、隐私、事实、结构、方案、方法和检索意图校验。任一不通过或两者语义不等价时 parity 失败、两份均不发布，不以一边成功掩盖失败且不自动追加第三次调用。两者通过且 digest 相同时发布 Realtime，Batch 经远端有效性验证后 `duplicate_noop`；digest 不同但业务语义等价时仅发布 Realtime，Batch 只保留脱敏对照证据。不得主观改变 canonical transport，也不得为对照向固定 Dataset 写入两份语义重复文档。

## 6. Applicable Constraints

- 受保护身份不得通过正文、metadata、日志或普通测试报告泄露；不得虚构人物、数字、日期、价格、权益、结果或承诺。
- 测试样本及报告按用户确定的 15 个文件和脱敏策略执行；合成数据只能补逻辑边界，不能替代真实语义验收。
- 远端操作只能使用现有 API；不直接写 Dify PostgreSQL。Batch 默认关闭，真实模型、Dify 索引/检索和未结算 reservation 的累计成本受本任务累计 ¥5、日 ¥4 上限保护。
- 保持已存在的 API/状态向后兼容，使用现有处理详情表达部分成功与未采用原因。

## 7. State / Data / Permission Rules

- `succeeded` 需发布、索引、metadata 和正文回读完成；`excluded` 仅限通过校验的不适合商业知识库；技术/内容为空失败为 `failed`。
- `duplicate_noop` 是经远端 metadata 与版本化、可检索正文 witness 验证的有效成果复用，不能当内容排除；本地 shard 状态、远端删除状态、失效 ID、缺失/禁用子块或失效 witness 必须使该关联不可复用。
- Worker delivery lease 与每文档 provider 调用次数分离：完成/精确重试释放旧 lease，持有当前 token 的 Worker 才能 reservation、写入文档状态或完成任务；Worker 必须在每个条目和限流等待后、费用预留前续租/重验 token，失 lease 异常直接终止旧 Worker，不得被修复、兜底或失败处理吞掉；显式正数 `model_attempts` 必须与该 document 的完整、有效 durable reservation/call-ID ledger 数量严格一致，缺失、损坏、冲突或外来账本均 fail-closed；消息重投不能消耗第三次 provider 调用。
- 测试模式禁止 general 以外知识库的所有写入口，不能只隐藏前端控件。
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
| RD-004 | 隔离部署 | 复用既有基础设施的逻辑隔离，服务端强制 general-only | 用户指定的最小改动、Dify/Redis/容器隔离要求 | 新建完整基础设施或仅 UI 隐藏 |
| RD-005 | 部署与真实模型 | 本地验收先完成；远端预检、精确命令和费用上限仍是远端执行门禁 | 防止隔离测试变成未受控真实写入/费用 | 无上限模型调用或隐式远端写入 |
| RD-006 | Batch/Realtime parity 与发布 | 四策略本地确定性 parity、咨询与知识星球真实付费 parity；Realtime 固定为不同 digest 时的 canonical 候选 | 覆盖咨询 case/methodology 与帖子 shard 两种代表产物，同时避免重复费用和语义重复入库 | 四策略全部真实付费、逐字比较模型文案、主观挑选结果、让 recent_activity 进入 Batch |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 辅助字段错误或一个坏单元不会阻塞其他合格单元；只有零核心内容/调用异常触发一次完整重跑，且无第三次、无新 `review_required`、无源文发布 | 覆盖 realtime；正常 Batch 保持禁用，隔离 acceptance seam 按 FR-006 例外验证 |
| AC-002 | 指定咨询对照显示已有方案未被逐字过滤；候选方法关联对应 case/evidence，并保留不同背景的策略差异 | 原文无方案时不编造 |
| AC-003 | 合法 50+ 来源引用可处理，未知来源、虚构事实、匿名化残留和无核心知识被拒绝；`recent_activity` 回归不变 | 真实/合成覆盖分开报告 |
| AC-004 | 混合 9 页 PDF 的图像页进入 OCR，所有页按原顺序保留；OCR/转换失败有准确 `failed` 分类 | 覆盖咨询隐私路径 |
| AC-005 | 已证明删除/失效远端成果不阻止重传，有效远端成果复用且无重复/越权删除；超时/格式异常保持可重验且 `create_document=0` | 使用精确关联、metadata 和实际远端正文 witness 验证 |
| AC-006 | 隔离部署资产经本地校验，测试模式无法写 course；jx 部署仅在预检和费用/命令确认后执行 | 固定测试 Dataset |
| AC-007 | 四策略本地确定性 Batch/Realtime parity 通过；咨询与知识星球完成预算内真实双 transport 对照，并按 Realtime canonical、同 digest `duplicate_noop`、不同 digest 单份发布或失败均不发布的规则留下 Dify/检索证据 | 不要求模型措辞、远端 ID、时间戳逐字一致 |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error/negative | Security/permission | Compatibility/migration |
|---|---|---|---|---|---|
| AC-001 | 多 case 中可用单元发布 | 辅助字段缺失、坏单元混入 | 两次零核心内容失败 | 匿名化/来源保留硬门槛 | 历史状态不改写 |
| AC-002 | 有方案 case 与关联方法发布 | 同题不同背景策略 | 原文无方案、无依据方法 | evidence 必须可追溯 | 保持既有 record type |
| AC-003 | 51+ 合法引用 | 空 topic/query/confidence | 未知 block、虚构数字 | 来源 ID 和 PII 拒绝 | `recent_activity` 不变 |
| AC-004 | 文字+图像页合并 | 低可靠/空白页 | OCR 失败/冲突/超限 | 咨询图像仍匿名化 | 既有 OCR 后端兼容 |
| AC-005 | 有效远端复用 | 删除标记释放重传 | 无法验证远端失败 | 精确关联删除 | 历史记录可读 |
| AC-006 | general-only 测试配置 | 独立 Redis/DB/alias | course 写入口被拒绝 | 无 secrets 输出 | 不影响正式服务 |
| AC-007 | 四策略请求/解析/结果/发布合同一致；两项真实对照各自合格 | 同 digest、不同 digest 但语义等价 | 任一 transport 失败、语义不等价、预算不足 | 只写 canonical、无重复 Dataset 文档、无第三次调用 | recent_activity realtime-only；生产默认不变 |

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | 高风险工程变更的行为基线 | Generated and approved |
| Adaptive Prototype | Not needed | 无新 UI/交互设计；隔离保护可由契约测试验证 | Not needed |
| Implementation Spec | Required | 单一 GPT-5.6 Terra 写入负责人的可审计边界；机器生成批准块以 feature record 的内容绑定状态为准 | Generated; current reapproval pending |
| ADR / Ops docs | Recommended when verified | 隔离运行手册已存在；仅验证后同步可复用排障经验 | Triggered by verification |

## 13. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
