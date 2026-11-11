# 千问 Batch/实时清洗一致性与商业顾问活动查询修复 — Requirement Spec

- Feature ID: `qwen-batch-cleaning`
- Status: USER-APPROVED SHARED CONTRACT UPDATE; GOVERNANCE REAPPROVAL PENDING
- Risk: `high`

## 1. Goal

> **Current shared-path boundary (2026-09-13):** for jointly owned paths,
> `cleaning-tolerance-methodology` and `recent-activity` provide the current
> authority. Activity remains realtime-only with default/maximum `50/200`.
> Normal runtime keeps `BAILIAN_BATCH_ENABLED=false`; an explicit, fail-closed
> Batch acceptance seam is allowed only in the approved isolation boundary.
> No new `review_required` or source Markdown fallback is produced, and existing
> test DB 6/Dify data is preserved unless a later exact cleanup approval exists.

完成四个内容模型策略的千问实时/Batch 双链路验证与修复，并同步修复商业顾问活动查询的语义路由和多活动展示：

- 认知分享策略（微信公众号、知识星球、小报童）保持各自知识单元结果契约。
- 咨询会议总结保持独立的咨询案例/证据结果契约。
- 最近活动保持活动事实/Metadata 契约，只使用实时千问，不使用 Provider Batch。
- Batch 与实时共享请求、校验、归一化、发布和 Metadata 投影逻辑；验证目标是契约与入库语义一致，不要求模型文案逐字一致。
- 所有问题先在本地和测试阶段集中记录，统一修复后再发布 jx；测试结果保留供人工查看。

## 2. Non-goals

- 不改变生产默认 `BAILIAN_BATCH_ENABLED=false`、候选数阈值 `100` 或估算输入 Token 阈值 `200000`。
- 不让咨询会议总结和认知分享共用业务 Schema、证据边界或输出用途。
- 不让最近活动进入 Batch，不新增活动查询期 PostgreSQL/Redis 服务或公开 API。
- 不自动修复、删除或改写历史清洗结果；既有测试 DB 6、测试数据库、卷和 Dify 文档默认保留，清理必须另有精确对象授权。
- 不触碰 course、商业会话、用户画像、系统日志或其他无精确关联的数据。

## 3. Background / Evidence

- 当前代码已有 `choose_call_mode`、Batch Worker、Dify 发布和 Metadata 回读，但此前 jx Batch 验收未完成，不能以取消的部分运行宣称一致性。
- 当前 Chatflow 活动意图仍使用固定词集合，活动结果默认上限为 3 且列表混入详情字段。
- 项目排障规则要求 Dify 创建、索引、Metadata 回读和本地终态分别核对；同源多文档发布不得留下无法解释的部分成功。

## 4. Actors and Scenarios

- 管理员在 jx 测试页上传认知分享、咨询会议总结和最近活动样本。
- Worker 按服务器配置选择实时或 Batch；模型任务完成后统一进入策略契约校验和 Dify 发布。
- 用户在商业顾问中查询自然表达的近期/所有活动，或追问具体活动详情。
- 验收人员对比实时/Batch 的本地终态、Dify 文档/Metadata/索引和 Chatflow 结果；测试数据保留供人工复核。

## 5. Functional Requirements

### FR-001 活动语义识别

商业顾问使用“确定性正/负规则 + 不确定表达轻量分类”的混合意图识别。必须支持“最近、近期、最新、有哪些、有什么、活动有哪些、所有活动”等自然表达；案例请求和活动分析/复盘/转化问题优先排除活动列表路由。指定活动名称或上一轮明确活动的唯一序号/指代进入详情分支。

### FR-002 活动查询与展示

- 近期活动：未来 30 天内的 `normal`/`postponed` 活动。
- 所有活动：前后 30 天窗口内的四种状态 `normal`、`cancelled`、`postponed`、`completed`。
- 列表默认上限为 50 条、最高 200 条，仍由当前 Chatflow 变量和边界控制。
- 列表每条一行，格式为“活动名称｜活动时间｜活动状态”，延期未定明确显示“延期未定”。
- 指定活动详情展示名称、时间、状态、地点/形式、简介和有效报名链接。
- 无候选时明确说明无符合条件的活动，不编造事实。

### FR-003 三类清洗结果边界

- 认知分享继续输出知识单元、来源块绑定和业务相关性；不强行套用咨询案例结构。
- 咨询会议总结继续输出 `consultation-rag-v1` 案例、事实状态、证据和检索问法；不因认知分享“宽松”而降低自身证据边界。
- 最近活动继续由源文档决定名称、时间、地点、形式、状态、截止时间和报名链接，模型只补受控语义字段。

### FR-004 模型调用一致性

四个内容模型策略 `personal_business_consultation`、`wechat_public_account_cognition`、`zsxq_post`、`xiaobot_post` 在免费本地确定性测试中覆盖 realtime/Batch 双模式；真实 Provider 对照只覆盖咨询与知识星球。正常运行和普通隔离运行均默认 Batch 关闭；只有明确的隔离 Batch acceptance seam、manifest、白名单、预算和正式健康门禁全部通过时才能显式走 Batch，且不得关闭 general-only 或降低生产阈值。最近活动始终走实时。实时和 Batch 必须共用 Prompt 版本、Schema、结构/业务校验、异常分类、Metadata 投影和 Dify 发布路径。只有调用异常或零可用核心内容时，才可对同一来源快照完整重跑一次；任一 transport、重投或新 batch ID 不得把跨来源的总上限扩展为第三次调用，有可用 case 但方案/方法遗漏也不得追加调用。历史调用未知或额度用尽时不得新增付费调用，回放不冒充当前真实结果。仅采用当前模型实际支持的结构化 JSON、关闭思考、低随机参数和固定 seed 等稳定性措施；不支持的参数不得强行传入。

### FR-005 Batch 生命周期与结果守恒

Batch 只在存在活动供应商任务时继续轮询；临时网络/数据库异常保留活动状态并重排，明确供应商终态才进入结果处理。结果按 `custom_id` 校验输入/输出守恒、重复、缺失、坏 JSON 和子任务失败，不得静默成功或产生半成品。

### FR-006 入库一致性

两 transport 均须验证清洗终态、受控 Metadata 字段名/类型/确定性值、chunking、parent/child、来源绑定、关键语义和发布计划；共用发布路径由本地/fake 合同覆盖。模型措辞、文档拆分数量、provider/远端 ID、时间戳和完成顺序允许不同；差异必须能由独立知识单元边界解释。Realtime 是不同 digest 时的固定 canonical 候选：任一 transport 不合格或语义不等价时两份都不发布；同 digest 只发布 Realtime并以共享有效成果验证 Batch `duplicate_noop`；不同 digest 但语义等价时只发布 Realtime并由其完成 Dify 索引、父子块回读和检索，Batch 仅保留未发布脱敏对照，不能声称独立远端检索或双写。

### FR-007 文档命名与跨源去重

- 所有发布到知识库的文档名称必须以对应原始文件的安全 stem 为前缀；同一原始文件拆分出的多个文档在该前缀后追加稳定分片/知识单元标识，模型生成标题不得覆盖原始文件前缀。
- 在同一知识库、同一策略/结果契约范围内，按最终规范化正文做跨原始文件、跨批次精确去重；保留一个经远端有效性验证的可追溯发布记录，重复项形成 `duplicate_noop` 关联而不是内容排除。不同策略/结果契约之间不得互相去重。
- 去重必须具备数据库约束或等价并发锁，重试、并行 Batch 和 realtime 不能产生重复远端文档。

### FR-008 模型结果未采用与失败边界

- 模型返回但未通过契约校验时，保存脱敏审核材料和受控模型结果，包括策略、清洗条目、请求/响应指纹、稳定错误码、字段路径和校验摘要；普通报告不得保存原始文件名、正文或身份信息。
- 新链路不产生新的 `review_required`，不使用源 Markdown 或占位正文兜底。存在独立可用核心单元时保留合格单元并记录拒绝原因；调用异常或零可用核心内容时才按总上限完整重跑一次，两次仍失败则进入明确 `failed`。
- `excluded` 仅表示模型明确判定不适合商业知识库且来源证据与判断均通过；技术失败、结构失败或未经验证的排除不得记为 `excluded`。

### FR-009 jx 测试与部署

恢复和验收前只读核对已有测试数据、Redis DB 6、队列、卷、镜像、Dataset 与费用，不预先清理旧状态。测试数据默认保留；任何删除都需要后来对精确对象的明确授权，不按名称、时间或整库操作。四策略先做免费本地 parity；本地质量、治理和独立复审闭合后才进入 jx 只读预检，预检和预算通过后只对咨询与知识星球做真实付费 parity。权威商业顾问 DSL 保持只读，不因本次清洗验收导入、回退或发布 DSL。

## 6. Resolved Decisions

| Decision ID | Decision | Reason |
|---|---|---|
| RD-001 | 活动采用混合意图识别；案例/分析负向优先 | 覆盖自然表达并控制误路由 |
| RD-002 | 活动列表默认 50 条、最高 200 条，名称/时间/状态逐行；详情保留完整业务字段 | 与当前只读权威 DSL 及 recent-activity 合同一致 |
| RD-003 | 最近活动固定实时；四个内容模型策略保留服务器可配实时/Batch 双模式 | 活动数据量小且需快速反馈，其他策略已有双通道 |
| RD-004 | 三类清洗结果不共用业务边界，只共享稳定调用与发布工程机制 | 结果用途和事实边界不同 |
| RD-005 | 生产 Batch 阈值和默认关闭保持不变；隔离验收可通过受保护 seam 强制覆盖允许策略 | 小样本可验证真实 transport，同时不改变正常成本行为 |
| RD-006 | 一致性按结构、Metadata、索引、来源和关键语义验收，文案可有差异 | Qwen 新调用不保证字节级复现 |
| RD-007 | 既有测试数据默认保留；恢复、上传和付费前先核对归属、队列、费用和远端有效性 | 当前明确禁止清理 DB 6、测试数据库、卷或 Dataset；旧清理授权不适用于本任务 |
| RD-008 | 发布名称始终由原始文件安全 stem + 稳定分片标识生成；同契约最终正文跨源/跨批次去重 | 可区分来源并消除重复知识；不同契约保持边界 |
| RD-009 | 未采用结果保存脱敏原因；新链路不新增 review_required、不用源文兜底，零核心内容或调用异常才完整重跑一次 | 保留证据并阻断低质量内容，同时遵守两次总上限和当前状态边界 |
| RD-010 | 四策略本地 parity、咨询与知识星球真实付费 parity；Realtime 固定 canonical | 覆盖咨询与帖子两类产物，控制费用并避免语义重复写入 |

## 7. Acceptance Criteria

| ID | Observable result |
|---|---|
| AC-001 | “最新活动有哪些”“最近活动有哪些”“活动有哪些”等自然表达正确进入活动列表；分析/案例问题不误入 |
| AC-002 | 活动列表默认最多 50 条且硬上限 200，名称/时间/状态逐行；四种状态和延期未定显示正确 |
| AC-003 | 指定活动可返回完整详情，报名链接只在有效时出现 |
| AC-004 | 最近活动无 Batch；四内容策略完成免费本地确定性 parity，咨询与知识星球在隔离门禁和预算内完成真实 realtime/Batch 对照 |
| AC-005 | 实时/Batch 的状态、Metadata、chunking、父子块、索引、回读、来源绑定和关键业务语义符合各自策略契约，并按 Realtime canonical 规则只发布一份有效成果 |
| AC-006 | Batch 临时错误可恢复轮询，终态后停止轮询；重复/缺失/坏结果可诊断且无半成品 |
| AC-007 | jx 恢复前完成费用、队列、镜像、DB 6、测试 DB/role、卷、Dataset 和正式健康只读核对；既有测试数据保留，权威 DSL 不变 |
| AC-008 | 所有远端文档名称保留原始文件安全 stem；同策略/契约跨源重复正文只有一个有效发布，其余经验证为 duplicate_noop |
| AC-009 | 模型结果未采用会保存脱敏可定位原因；没有新的 review_required、源文兜底或第三次调用，excluded/failed 语义可区分 |
| AC-010 | 本地质量、聚焦回归、jx 真实验收、问题统一修复和相关文档同步完成 |

## 8. Model-generated Test Intent

| Acceptance | Happy path | Boundary / negative | Compatibility / safety |
|---|---|---|---|
| AC-001 | 自然表达、活动列表、指定活动 | 活动分析、复盘、案例、普通业务负例 | 保持原普通/案例路由 |
| AC-002 | 1/3/50 条和混合状态 | 0 条、超过 50/200、延期未定、窗口边界 | 不泄露内部 Metadata |
| AC-003 | 名称、时间、状态、地点、简介、链接 | 无链接、截止、无新延期时间 | 不猜测事实 |
| AC-004 | 四策略本地双模式、两策略真实对照 | 默认关闭、acceptance seam、recent_activity 禁止 Batch | 任务模式快照兼容；生产默认不变 |
| AC-005 | 两 transport 独立合格后执行 canonical 裁决 | 文案/digest/拆分差异、Metadata 类型冲突、Dify 索引失败 | 三类策略边界独立；只发布 Realtime canonical |
| AC-006 | 提交、轮询、完成、修复 Batch | 瞬时异常、供应商失败、重复/缺失/坏 JSON | 无孤儿、幂等 |
| AC-007 | 只读恢复清单、jx 隔离启动、数据保留 | 归属/预算/健康门禁失败即停止 | 不删除任何未精确授权数据，不改权威 DSL |
| AC-008 | 原始 stem 命名、跨源去重、duplicate_noop | 并发/重试/分片、失效远端成果 | 不跨契约去重、不盲信本地成功 |
| AC-009 | 未采用结果的脱敏原因和明确失败 | 缺失/坏结构/零核心/调用异常 | 无 review_required、无源文兜底、无第三次调用 |
| AC-010 | 质量通过后部署 | 任一问题统一记录、修复后新批次重跑 | 文档和状态证据可追溯 |

## 9. Artifact Plan

| Artifact | Decision |
|---|---|
| Requirement Spec | Generate/current |
| Adaptive Prototype | Not needed;已有代码与 Dify 隔离验收 seam |
| Implementation Spec | Required/current |
| ADR / troubleshooting | 仅在验证出可复用根因后更新 |

## 10. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
