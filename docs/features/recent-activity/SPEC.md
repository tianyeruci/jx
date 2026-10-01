# 最近活动清洗与商业顾问 Chatflow 接入 — Requirement Spec

- Feature ID: `recent-activity`
- Status: APPROVED
- Risk: `high`

## 1. Goal

本轮共享路径变更在现有 ys 专用服务按已批准差异验收计划回归，jx 正式服务只读。活动仍 realtime-only、默认/上限 50/200；六类 15 份不新增活动输入。共用 Worker、Dify 与商业顾问边界的兼容检查纳入 EV-06/EV-07，费用与资源边界沿用 `cleaning-tolerance-methodology` 当前合同。

在现有清洗与 Dify `general` 知识库链路中维护活动清洗策略，使商业顾问能根据自然表达识别活动列表/详情意图，按可配置数量展示活动名称、时间和状态，并正确处理正常、取消、延期、已完成和可选报名链接。

## 2. Non-goals

- 不新增查询期 Redis/PostgreSQL 查询、用户 API、SSE 字段或独立活动服务。
- 不把活动样本文档写入正式 Dataset。
- 不恢复课程、路线或课程卡片能力。
- 不让 Dify 相似度排序替代活动时间排序。
- 不在模型无法确认硬事实时猜测时间、地点、状态或报名链接。

## 3. Background / Evidence

- `Other/event_rag_cleaner` 已完成活动拆分、实时 Qwen、状态/时间/metadata 基础能力，可作为实现来源。
- 现有项目清洗管线已支持一个源文件输出多个 `CleanedDocument`，并按策略白名单投影 Dify metadata。
- Dify 1.13.3 Knowledge Retrieval 会解析 metadata 条件中的变量模板，并将 `doc_metadata` 返回给后续 Code 节点；因此 Chatflow 可以过滤、去重、排序和格式化活动。
- Dify 检索仍按相关度返回候选，不能单独保证“最近”顺序；Chatflow Code 节点必须按时间戳二次排序。

## 4. Actors and Scenarios

- 管理员在测试页选择“活动清洗策略”并上传 Markdown/TXT/DOCX。
- 清洗 worker 拆分活动、调用实时千问补充受控分类字段、设置 metadata、发布并回读 Dify 文档。
- 用户在正式商业顾问中使用“最近/近期/最新/有哪些/有什么/活动有哪些/所有活动”等自然表达查询活动；Chatflow 采用确定性正负规则与不确定表达轻量分类的混合意图识别。
- 管理员重新上传同名活动以更新；活动改名视为新活动；删除需要精确管理员操作。

## 5. Functional Requirements

### FR-001 活动清洗

活动策略必须支持 `.md`、`.markdown`、`.txt`、`.docx`，一个活动产生一个独立清洗文档；必要事实无法标准化时只跳过该活动，不阻断同文件其他活动。

### FR-002 实时模型与事实边界

默认使用实时 Qwen，凭据变量仅为 `BAILIAN_API_KEY`。模型只补充受控的活动类型、主题、行业和业务阶段；源文档优先决定名称、时间、地点、形式、状态、截止时间和报名链接。

### FR-003 活动 metadata

写入 `record_type`、`event_id`、`event_status`、`source_status`、`event_type`、`topic`、`industry`、`business_stage`、`delivery_mode`、`city`、`event_start_ts`、`event_end_ts`、可选 `registration_deadline_ts`、可选 `registration_url`、`source_title`、确定性截断的 `activity_summary`。不写入原文件名、源正文、批次 ID、哈希或用户身份。

### FR-004 状态

`event_status` 固定为 `normal`、`cancelled`、`postponed`、`completed`。源文档明确为已完成时必须入库；`cancelled` 保持终态；`normal`/`postponed` 在 `Asia/Shanghai` 下的有效 `event_end_ts < now` 时归一为 `completed`。延期无新时间时展示“延期未定”，不得把旧时间当成新时间。除延期未定外，名称、内容、状态或时间无法确定的活动跳过并记录块级错误。

### FR-005 Chatflow 活动列表与详情

活动意图分支使用当前 Chatflow 的 `task_state_json` 状态、意图解析与受控补查路径；活动列表/详情、案例/分析负向与普通咨询分支均按当前权威 DSL 路由。普通“最近/近期/最新活动”只查 `normal`/`postponed`、`event_end_ts > now` 且活动区间与 `[now, now+30天]` 相交的活动；延期未定不进入该结果。“所有活动”查四种状态，活动区间与滚动窗口 `[now-30天, now+30天]` 相交。结果去重后进行中优先、未来活动按开始时间升序、已结束活动按结束时间从近到远，数量由 Dify 环境变量 `ACTIVITY_MAX_RESULTS` 控制（缺省 50，限制 1～200），内部输出 `activity_max_results` 为数字。列表逐行输出活动名称、活动时间和活动状态；延期未定明确显示。只有指定具体活动时才输出简介、地点/形式和有效报名链接。

### FR-006 同名更新与发布

稳定 `event_id` 由规范化活动名称生成；同名重新上传替换原 Dify 文档，改名创建新活动。替换必须先完成新文档创建、索引和 Metadata 回读，再删除旧文档；新链路失败时保留旧文档。Dify metadata 字段创建和类型冲突检查必须幂等且失败关闭。

## 6. Applicable Constraints

- 保持现有清洗上传、Dify 发布、补偿删除和 metadata 回读契约兼容。
- Chatflow 直查的活动数量受实际 `top_k`/候选覆盖能力限制；配置必须以真实回读证据为准，并记录容量上限。当前本地静态证据仅证明 DSL 的三个活动检索节点各自配置了 `top_k: 50`，以及 Code 节点会把显示数量钳制到 1～200；它不能证明 Dify 1.13.3 在真实检索、去重与受控补查后能端到端提供 200 个不同活动。禁止将“最高 200”表述为全库枚举或已验证的端到端保证。本轮 ys 差异验收仅回归共享路径兼容，不新增活动样本或远端容量测试。
- 共享 Provider/Dify 清理只以仍 live 的 publication shard/reference 判断活动远端对象是否受保护；历史 replacement 或已被精确转移的引用不能阻断其独立 file outbox 收敛。该约束不改变活动 realtime-only、metadata、Chatflow、DSL 或公开 API 语义。
- 不输出密钥、原始活动正文、私有来源信息或内部标识。
- 正式切换前必须备份现用商业顾问 DSL 与相关 Dify 配置。

## 7. State / Data / Permission Rules

- 普通管理员可通过既有测试页上传；Dify 发布沿用现有管理员与服务凭据边界。
- Chatflow 不读取项目数据库或 Redis；活动事实来源是 Dify 文档正文和 metadata。
- 活动删除只允许已有精确文档归属证据的管理员操作，不按文件缺失、名称正则或时间窗口批量删除。

## 8. Error and Boundary Behavior

- 活动名称、状态、时间或正文无法确定时，该活动进入跳过/失败审计，不产生可检索文档；同一文件其他活动继续处理，并记录总块数、成功数、跳过数和每个跳过块的稳定错误码/原因。
- 同一文件中其他活动继续处理；模型失败不阻断后续活动。
- Chatflow 无候选时明确说明当前窗口没有符合条件的活动，不编造活动。
- 动态 metadata 条件或 `doc_metadata` 传递在目标环境验证不通过时，不将依赖它的活动检索判为通过；本轮仅在 ys 验证受影响兼容，jx 正式 Chatflow 保持只读。

## 9. Assumptions and Resolved Decisions

| Decision ID | Question / assumption | Decision | Evidence / reason | Alternatives rejected |
|---|---|---|---|---|
| RD-001 | 查询时是否新增数据库/Redis | 采用 Chatflow 直查；数据库只保留既有上传/发布状态 | 目标是低复杂度，Dify Retrieval 输出包含 `doc_metadata` | 服务端查询精确列表，作为容量升级触发器 |
| RD-002 | Dify 动态 metadata 是否可用 | 在目标测试环境验证字符串和数值变量；本轮在 ys 验证受影响兼容 | Dify 1.13.3 运行时解析模板，官方文档允许变量值；时间类型仍有常量限制 | 仅静态过滤或强制新增查询服务 |
| RD-003 | 活动身份 | 规范化名称稳定生成 `event_id`；改名即新活动 | 用户已确认活动名称唯一且状态按名称更新 | 依赖业务 ID；当前模板没有稳定 ID |
| RD-004 | 模型模式 | realtime Qwen，变量仅 `BAILIAN_API_KEY` | 用户明确要求实时模型且脚本已有验证 | Batch Chat/Batch File、`DASHSCOPE_API_KEY` fallback |
| RD-005 | 报名链接 | 有效链接默认随正常活动展示；缺失不显示 | 用户确认链接有时存在，且希望减少二次追问 | 默认隐藏或模型猜测链接 |
| RD-006 | 活动状态 | 四种状态均入库；取消保持终态，正常/延期结束后转已完成；时区为 `Asia/Shanghai`，严格比较 `< now` | 用户要求已完成活动可入库和查询；取消仍是独立结束状态 | 把已完成活动跳过或把取消改成已完成 |
| RD-007 | 活动查询窗口 | 普通查询为未结束可报名活动的未来 30 天窗口；“最近所有活动”为与前后 30 天滚动窗口相交的四种状态 | 用户确认已完成活动在范围内也要被“所有最近活动”使用 | 所有查询只查未结束或无过去边界 |
| RD-008 | 活动摘要与数量 | 摘要取源内容并确定性截断；Dify 环境变量 `ACTIVITY_MAX_RESULTS` 缺省 50、限制 1～200，并映射为内部数字变量 | 当前权威 Chatflow 支持受控补查后的较大活动结果集；详情才展示摘要，避免列表过长 | Qwen 摘要或固定不可调数量 |
| RD-010 | 意图识别 | 混合规则与轻量分类；案例/分析负向优先，指定活动或上一轮唯一指代进入详情 | 覆盖自然表达并保持普通/案例分支隔离 | 仅固定关键词或所有活动词都进入列表 |
| RD-009 | 当前测试清理边界 | 默认保留原有数据与最终成果；只精确清理本轮创建、全部 live owner 属于临时删除集且无 retained owner 的对象；复用对象只解除临时关联；归属不明时停止相关删除 | 当前已批准 ys 差异验收的保留优先边界；历史一次性删除授权不重放 | 模糊按名称/时间删除、删除原有活动数据或整库清理 |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 测试页能发现并选择“活动清洗策略”，支持 Markdown/TXT/DOCX 上传 | |
| AC-002 | 样本多活动被拆成独立文档；每个文档清洗、metadata 设置、回读和入库成功 | |
| AC-003 | 正常/取消/延期/已完成、过期归一化、延期未定、截止时间和报名链接映射正确，失败活动不阻断其他活动 | |
| AC-004 | 同名更新替换旧文档，改名产生新文档，精确删除不影响其他文档 | |
| AC-005 | Dify 1.13.3 测试 Chatflow 动态 metadata 字符串/数值筛选成功，Code 节点能读取 `doc_metadata` | 本轮 ys 仅复验受影响兼容，不新增活动源 |
| AC-006 | 自然表达的近期活动返回未结束的正常/延期活动；“所有活动”返回滚动窗口内四种状态，默认最多 50 条、最高 200 条并可由 Dify 变量调整，名称/时间/状态逐行展示；无效 `task_state_json`、案例/分析负向与受控列表/详情补查保持当前 DSL 边界 | |
| AC-007 | 指定活动返回名称、时间、状态、地点/形式、简介和有效报名链接；无链接不虚构；普通商业问题和案例检索行为保持原契约 | |
| AC-008 | 本地质量和目标环境 SSE/Chatflow 验收有可定位证据 | 本轮目标为 ys 共享路径 EV-06/EV-07；历史正式发布证据保留，jx 只读 |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error/negative | Security/permission | Compatibility/migration |
|---|---|---|---|---|---|
| AC-001 | 策略发现、上传、文件类型校验 | 空文件、错误扩展名、未知策略 | 未授权策略不能创建批次 | 复用现有管理员边界 | 现有策略不变 |
| AC-002 | 3 个活动独立清洗并入库 | 混合有效/缺时间/模型失败 | 单活动跳过/失败不影响后续 | 报告不含正文和密钥 | Dify metadata 回读 |
| AC-003 | 三种状态和链接事实保留 | 延期无新时间、无链接、多链接 | 禁止猜测硬事实 | 仅 allowlist 字段出库 | 旧 metadata 不受影响 |
| AC-004 | 同名替换和改名新增 | 重复上传、发布中断、旧文档删除失败 | 补偿/重试可观测 | 精确文档 ID | 无数据库迁移 |
| AC-005 | 动态条件、`doc_metadata` 和排序 | 时间窗口边界、重复 child、候选为空 | Dify 变量未解析则阻断正式切换 | 不访问 Redis/数据库 | 固定 Dify 1.13.3 |
| AC-006 | 两种自然语言活动查询真实问答 | 0/1/50/超过 200 候选、已取消/延期/已完成、窗口边界、无效状态 JSON、受控补查和混合排序 | 无候选时明确说明 | 不泄露内部字段 | 保持 SSE 契约 |
| AC-007 | 列表与详情展示隔离、链接展示和普通分支隔离 | 延期未定、链接缺失/非法、报名截止、普通问题、案例问题 | 不得由模型补链接或硬事实 | 仅公开有效业务字段 | 原商业顾问回答边界不回退 |
| AC-008 | 质量和发布报告齐全 | 命令失败、远端异常、DSL 发布失败 | 失败批次不得宣称完成 | 密钥脱敏 | 备份可回滚 |

The human approves the behavior/test intent. The human is not expected to author unit-test cases.

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | Standard/high feature source of truth | Generate |
| Adaptive Prototype | Not needed | 目标是现有清洗管线和 Chatflow 接入，隔离 Dify 实测本身就是最小可行验证 | Skip |
| Implementation Spec | Required | Sol → Luna execution contract | Generate |
| ADR / Ops docs | Recommended if release produces reusable failure knowledge | 只在验证出可复用根因后更新 | Defer until evidence |

## 13. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
