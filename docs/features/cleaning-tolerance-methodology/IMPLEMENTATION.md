# 文档清洗容错、咨询方案方法与隔离测试修复 — Implementation Spec

- Feature ID: `cleaning-tolerance-methodology`
- Status: Current contract; approval state is governed by `feature.json`
- Planner: GPT-5.6 Sol
- Implementer: GPT-5.6 Luna (single source write owner)
- Architecture: `docs/architecture/PROFILE.md`

## 当前 ys 实施范围（2026-09-29）

使用用户已提供的 `general` 知识库和四个已提供 App；课程库暂不启用。ys Compose 显式设置 `DIFY_COURSE_DATASET_ENABLED=false`；配置校验与 `/health/ready` 仅在启用课程库时要求课程 Dataset ID，管理 API 的课程知识库入口在关闭时拒绝请求。通用库仍须配置并实际可达；课程库不计入本轮检索与发布验收。隔离、预算、同源镜像、共享服务保护与业务验收要求继续生效。

## 已有 ys 服务的候选更新

本轮目标为在现有 ys 专用服务上受控更新当前候选，使用 `docker-compose.ys.yml` 和独立 `.env.ys`；API、管理 UI、清洗/OCR、模型处理、发布、调度使用同一冻结候选。共享 Dify 1.13.3、PostgreSQL、Redis、Docker 网络保持运行；复核已有项目专用 PostgreSQL 数据库/角色、Redis DB 6、指定四个 Dify App 和已有 `general` Dataset；课程库关闭。四 App 仅允许已授权的必要兼容修正与发布并保留前版本；不新建 App/Dataset，不改原有文档状态或共享卷。入口绑定回环，Batch 默认关闭。现有 jx 隔离资产仅作为免费合同测试资料；真实部署与验收目标为 ys，同一最终候选须有完整证据。不得重跑首次安装脚本或重复迁移。

## 1. Goal / Non-goals Reference

实现 [SPEC.md](SPEC.md) 的 FR-001 至 FR-006 与 AC-001 至 AC-007。保持现有 L2 FastAPI + Celery + SQLAlchemy + Streamlit + Dify 模块化单体的职责划分；不重建清洗流程、Registry、Dify DSL、基础设施或 API 状态模型。

## 2. Repository Evidence

- `knowledge_cleaner/consultation_rag.py` 定义当前严格的咨询 Pydantic 契约和响应校验。
- `ai_management/model_cleaning.py` 包含帖子/咨询模型载荷、归一化和 prompt；`ai_management/workers/tasks/model_cleaning.py` 负责任务重试、fallback、分片和 Dify 发布。
- `knowledge_cleaner/pdf_convert.py` 与 `knowledge_cleaner/rapidocr_backend.py` 构成 PDF 分类、提取与 OCR 后端。
- `ai_management/cleaning.py`、`ai_management/workers/tasks/cleaning.py` 与 `ai_management/api/routers/knowledge_admin.py` 包含去重、批次和管理路径。
- 当前 `docker-compose.ai.yml`、`docker-compose.ui.yml` 及 `ai_management/test_ui.py` 是已有本地合同测试资产，不作为本轮 ys 更新入口。

## 3. Design Decision

在现有模型归一化、验证和 Worker 中实施“核心单元通过即保留”的纵向容错：先归一化辅助字段，再按单元验证、记录拒绝原因，最后只在核心可用单元为零时触发一次全量重跑。用现有 details 结构表达部分成功，保持持久化状态枚举。

咨询契约添加/复用可选的同次方法候选字段，且将方案定位为有来源的忠实整理而非 quote 的逐字副本。关键事实、主体、条件、否定和行动均须回溯到当前 case advice evidence；确定性锚点屏障只用于拒绝已知的无依据扩写、主体置换、否定反转或新行动，不能以有限同义词表重新定义允许的改写。核心 case 字段按职责锚定同 case evidence：问题→problem、必要背景→background、表现→problem/background；未知辅助 kind 只有存在直接文本锚点时才兼容，不能跨 kind 借任意 quote。锚点要求 exact 或两个真正独立、非通用的中文短语（相邻重叠 n-gram 不得冒充两项事实）；“客户需求/用户数据/业务问题”等通用组合和单一短 ASCII 产品词不构成锚点。真实两份咨询原文对照仍是自由改写内容验收，不由确定性屏障替代。方法文档由当前 `_apply_model_payload` 的多文档生成路径写出，使用既有 `methodology` record type。每个候选固定为 `case_scoped`：只从一个已验证 `consulting_case` 内关联问题、条件和方案 evidence 组织；key/去重条件包含该安全 case 关联、情境和已验证 payload 指纹，不能以问题文字单独决定合并，也不跨 case/来源包装为通用方法论。

PDF 处理以“每页可靠性”为单位调用现有 OCR，实现可选页码范围或等价小接口；合并后再进入既有匿名化/质量门禁。去重将远端有效性加入查询/复用判断，不引入新业务表；Dify witness 读取可检索父/子块的稳定标识、位置、正文和状态，统一解析顶层/嵌套 envelope 的分页声明，固定每个 parent/child 流的首次 `total`，逐页先计数再写入并拒绝后续 total 倒退、缺页、页指纹和 ID 进展异常，历史 witness 版本不兼容即不可复用。`recent_activity` 不参与该新增 witness 读取。post shard planner 只做可恢复协调，且不得向已领取的 `shard_indexing` 关系追加条目；它按新旧顺序遍历跨文件、同知识库/策略/契约的历史 shard，选择第一个最终正文 digest、精确 remote ID 和 witness 都合法的待核验候选，Worker 回读成功才复用。候选/共享 remote ID 与当前来源拥有、可 replacement 的 ID 明确区分；来源删除、替换删除和延迟删除任务均先查其他 live 本地引用，只有最后引用才删除远端对象。已知 metadata 失配精确替换、读取不确定保留 publication-only 可重试且不盲建/不回源重跑模型，新发布后持久保存 v2 witness。realtime request audit 与 provider/文档写入同样由当前 lease token 围栏。

Batch 和 Realtime 复用同一请求构造、Prompt/Schema、解析、业务校验、canonical 化、metadata/chunking、parent/child、去重和发布路径，不另建第二套业务清洗链。正常运行保持 Batch 关闭；只增加默认关闭、受项目专用资源、manifest、四策略白名单、预算和共享服务健康保护的 acceptance seam，不通过降低生产阈值触发。四策略做本地确定性 parity；真实 provider 对照只覆盖咨询和知识星球。Realtime 固定为不同 digest 时的 canonical 候选；任一 transport 未独立通过或语义不等价时均不发布，同 digest 只发布一次并验证 Batch `duplicate_noop`，语义等价但 digest 不同只发布 Realtime 并保留 Batch 脱敏证据。

ys 候选更新前复核独立 Compose 项目、服务名、卷、回环端口与项目专用 PostgreSQL/Redis 逻辑资源；服务端仅允许指定 `general` 写入并拒绝课程写入。累计预算保护独立于 general-only 开关。远端变更须通过 ys 预检、同源镜像、共享服务健康及精确命令门禁；真实模型及其他可能计费动作还须通过预算门禁。费用未核清不阻断已证明不会隐式调用模型的无付费准备或启动。

### Credible alternatives rejected

- 重新设计清洗状态/另起 pipeline：超过任务范围，且不能解决现有历史兼容与 Worker 幂等。
- 因一个辅助字段或坏 case 丢弃全份：违背单元级容错，放大模型成本。
- 取消来源、匿名化或事实校验：会让“容错”变成不安全内容入库。
- 因为混合 PDF 直接整份 OCR 或更换服务：无必要；只需修复逐页路由，咨询隐私门禁仍保留。
- 只在 Streamlit 隐藏课程入口：不能阻止 API 写入，隔离不成立。

## 4. Modules and Ownership

| Module ID | Ownership paths | Responsibility |
|---|---|---|
| `semantic-contract` | `knowledge_cleaner/consultation_rag.py`; `ai_management/model_cleaning.py`; `tests/test_consultation_rag_integration.py`; `tests/test_model_cleaning_workflow.py`; `tests/test_cleaning_tolerance_methodology_regression.py` | 核心/辅助字段、单元级验证、方案/方法候选、帖子容错与 prompt |
| `cleaning-workers` | `ai_management/workers/tasks/model_cleaning.py`; `ai_management/workers/tasks/cleaning.py`; `ai_management/workers/tasks/maintenance.py`; `ai_management/cleaning.py`; `ai_management/services/dify_references.py`; `ai_management/models.py`; `migrations/versions/o2p3q4r5s6t7_source_delete_tombstone_generation.py`; `tests/test_model_cleaning_*.py`; `tests/test_ai_management.py`; `tests/test_dify_client.py` | 两次上限、状态/details、Batch/Realtime parity、canonical 多文档发布、去重/补偿及清洗持久化迁移 |
| `pdf-ocr-route` | `knowledge_cleaner/pdf_convert.py`; `knowledge_cleaner/rapidocr_backend.py`; `tests/test_pdf_ocr_quality.py` | 混合 PDF 逐页 OCR、顺序、精确错误分类与隐私覆盖 |
| `isolated-test-deploy` | `docker-compose.ys.yml`; `docker-compose.cleaning-test.yml`; `docker-compose.cleaning-test-ui.yml`; `Dockerfile.cleaning-test`; `Dockerfile.cleaning-test-ocr`; `Dockerfile.cleaning-test-ui`; `scripts/jx_*cleaning*`; `ai_management/config.py`; `ai_management/api/routers/knowledge_admin.py`; `ai_management/api/routers/health.py`; `ai_management/test_ui.py`; `tests/test_cleaning_test_deploy_contract.py`; `tests/test_test_ui.py`; `tmp/preflight-contract-*/isolated.env`; `tmp/start-compensation-contract-*/compose-calls.log*`; `tmp/attest-debug/a`; `tmp/f.env`; `uv.lock` | ys 专用服务定义、默认关闭的 Batch acceptance seam、指定 `general` 与课程关闭状态 服务端隔离、预算/诊断/回滚资产及隔离依赖锁；两份精确测试调试证据纳入模块所有权 |
| `contract-docs` | `docs/执行计划合集/jx隔离清洗测试接续执行计划.md`; `docs/troubleshooting/data-cleaning.md`; `docs/troubleshooting/deployment.md`; `docs/troubleshooting/dify.md`; `docs/troubleshooting/testing.md`; `AI管理服务当前状态与后续测试交接.md`; `scripts/generate_project_index.py`; `tests/test_governance_contract.py` | 当前唯一执行计划、已验证的稳定规则与动态脱敏验收记录、项目索引生成与治理合同 |
| `acceptance-runner` | `scripts/full_cleaning_acceptance.py`; `scripts/jx_release_data.py`; `ai_management/acceptance_budget.py`; `tests/test_release_acceptance_contract.py` | 15 份真实输入的费用与完成证据、保留优先清单及精确清理保护 |

## 5. Dependency Direction

### Allowed

- `model_cleaning` 和 `consultation_rag` 定义模型语义；Worker 调用它们，不能反向依赖 Worker。
- PDF 转换层依赖 OCR 后端抽象；清洗 Worker 只消费转换结果与稳定错误。
- 测试 mode 设置/服务端 guard 位于 API/设置边界；UI 显示其诊断结果但不作为唯一限制。
- 部署脚本仅组合现有镜像/Compose/health API，不直接操作 Dify 数据库。

### Forbidden

- `recent_activity` 不因共享 helper 的调整改变请求策略。
- 不在 Dify DSL 或用户修改的 `dify/*.yml` 中实现清洗逻辑。
- 不使 OCR、测试 Worker 或 UI 连接正式 Redis/上传卷/正式 API alias。
- 不让渲染层伪造辅助字段、原文方案或方法。

## 6. Reuse Decisions

沿用现有 Pydantic/schema、details、CleaningDocument、Dify publication shard、RapidOCR、Compose 镜像和知识库管理 API。只为“逐页 OCR”“单元拒绝原因”“ys 专用 Dataset 写入隔离”在当前职责模块中增加小接口；不创建泛化 shared/service 层。

## 7. Contract Impact

- Public API / SDK: 现有管理 API 的响应 details 可增加原因字段；不删除/改名既有字段或状态。
- Events/messages: 重试尝试数跨 Worker 保持总上限两次，消息重投不得增加生成。
- Persistent schema/migration: 优先复用已有列/JSON details；若证据表明无法表达，则先停下升级契约，不擅自新增表。
- Backward compatibility: 历史批次按原快照解释；新规则仅进入新处理流程和显式恢复。
- Security/privacy: 来源/匿名化/事实为硬门槛；ys 服务端须验证仅向指定 `general` 写入且课程入口关闭。
- Operational behavior: Batch 默认关闭；acceptance seam 仅限四个指定策略和 ys 项目专用资源。ys 只读预检、费用 reservation、同源镜像、共享服务健康和精确命令门禁通过后，真实 Batch/Realtime 对照只运行咨询与知识星球；recent_activity 始终 realtime-only。
- Retry safety: 普通条目发布重试只接受已通过 source/OCR/model 门禁的 `DIFY_PUBLICATION_FAILED` 或 `SOURCE_ATOMIC_ROLLBACK`；历史 source/OCR 失败即使遗留 `retryable=true` 也只能从仍存在的源文件重跑，否则返回 `410`。模型错误不经普通 file/item retry 重建任务。
- Provider budget: 每个真实 provider 调用在行锁和当日预算锁内消耗文档级额度并持久化 reservation；新 reservation 必须保存稳定 call ID、文档、实际 transport 与不可变定价快照。显式正数 `model_attempts` 仅在本 document 的完整有效 ledger 数量严格相等时可被接受；调用准入与日/累计费用汇总都以 task batch 的完整 document ID 集合复核 ledger，缺失、null、损坏、重复、外来或计数不一致的历史均 fail-closed，不重写历史以恢复额度。realtime 返回 usage 后只结算匹配 call ID，将该项预留替换为实际费用；usage 缺失则保留未结算预留。小批量 Batch repair 明确按 realtime 计价；Batch 的未结算 reservation 持续占用预算，直到可逐调用核对的结算证据存在。
- Realtime lease fencing: delivery `attempts` 仅审计 Worker 领取，不能替代持久 `model_attempts`/reservation ledger 的两次 provider 调用上限。领取新 token 时只恢复已失 lease 的 `model_processing` 文档；reservation、usage/文档写入和完成都在当前未过期 token 下检查。每个条目和限流完成后由当前 token 原子续租，再在短窗口内预留并发起请求；`_ModelLeaseLost` 在所有重试、修复、兜底和失败分支直接透传，旧 Worker 不得写入失败或清除新 token。完成、失败和精确重试释放 lease，旧 Worker 失 lease 后不再产生 provider 或持久化副作用。
- Advice grounding: 每个方案/候选方法只可使用当前 case 的 advice evidence。忠实改写允许保留，但每个衔接行动子句须有当前 advice anchor；派生字段中新增且未被 advice 明示的付款/收费条件、承诺、合同、角色置换或操作性升级必须被丢弃。该确定性屏障不声称覆盖自然语言全部语义；丢弃字段不否决可回溯的独立 case，也不触发额外模型调用。
- Consultation core grounding: `primary_problem`、必要背景和问题表现必须各自满足允许 evidence kind 的完整命题来源锚定；局部文本重合不是放行自由扩写的理由。历史 durable ledger 不能完整证明既有调用时，不得通过修复路径回填、重建或再提交 Batch；已上传的 repair 输入只进入精确、人工介入的 outbox。
- Terminal cleanup ownership: 成功删除的 Provider file 在同一事务中消费该 file owner；terminal Batch 只有在所有 owned file 已删除或已可靠转移到带 task/batch/category 的独立 file outbox 后才可消费其 Batch owner。否则保持 Batch outbox 以便精确重试；active sibling 和不完整 owner 不得被 terminal sibling 连带关闭。
- Test isolation: ys 启动同时验证项目专用 PostgreSQL DB/role、独立 Redis effective DB、指定 `general` Dataset 且课程关闭、Batch 关闭、日预算与累计 ¥5 边界；为避免运行库 URL parser 覆盖，Redis URL 不得包含 `db` query 参数。短时效 manifest 除 image tag 与 Git provenance 外还须记录并立即比对 API/UI/OCR Docker image ID、当前受治理工作树指纹、wheel SHA-256 与镜像 label，拒绝旧 wheel、同 tag 重建或重标后的不一致镜像。
- Acceptance spending: 预算账本跨进程、重启和候选更新持续存在。显式 ys 验收范围内的 Realtime、受限 Batch、Dify 索引/检索、四 App 及后台重试均计入本任务旧账、实际结算和未结算预留；缺失 usage 的预留继续占用预算。免费解析后按实际逻辑单元、分片、允许重试和冻结查询集核算整套最坏费用；累计不超过 ¥5 且北京时间单日不超过 ¥4 才启动付费阶段。无法核清费用时继续免费回归，不启动相关付费动作。
- Acceptance Batch: 仅允许冻结 manifest 内的策略、样本、任务和对应逻辑单元走受限小样本 Batch 入口，正常阈值与默认关闭状态不改。咨询和知识星球各固定一份，同一逻辑单元 Realtime 和 Batch 各一次，失败不追加第三次调用；两路独立合格且语义一致前禁止发布，通过后仅发布 Realtime canonical。禁止复制输入、另建批次或重启 runner 重置次数与费用。
- Acceptance state: 同一 `ModelCleaningTask` 顺序执行受限 Batch 对照与 Realtime canonical；Batch 只保存无正文的校验投影、稳定调用 ID 和结果审计，不修改共享 `CleaningDocument` 或触发发布。Realtime 用同一冻结请求来源执行共享纯校验器；两路各自合格且业务语义等价时，才在任务审计 JSON 内形成持久 release receipt。模型完成、普通发布、分片重试/替换和条目重试共同检查 receipt；缺失或不一致时零发布。普通任务仍走原流程。
- Acceptance ledger: 模型 reservation/usage 仍以 `ModelCleaningTask.result_counts` 为权威；本次 Dify/App/runner 外部付费动作使用同一受保护 campaign 总账文件。稳定 operation ID 不依赖进程 run ID，文件锁与原子替换保护并发；先持久预留再发起外部副作用，崩溃或缺少结算依据时继续占用预算。按 operation ID 对已结算实际、未结算预留和尚未预留的计划上界去重求和，不以 `max` 漏计或双计。
- Acceptance recovery: 外部请求前持久化 `external_started`，调用完成记录保存经校验的脱敏 verdict 与证据；完成状态不能单独证明通过。检索重放恢复原 segment 内容指纹和关键点映射；Business 重放复核当前 retained source、segment、每个关键点引用、分支、回答和泄漏证据；工具 App 重放保留 HTTP/SSE 判定；摘要绑定原 conversation 并验证实际持久化。已完成操作不重复调用，证据缺失或外部结果未知时失败关闭。
- Retention: 清理清单先区分最终保留、新建临时和复用共享对象。远端删除必须同时证明对象由本轮创建、所有 live owner 均在临时删除集、没有 retained owner；复用对象只解除临时关联，最终保留对象不得存在待执行删除任务。
- Canonical parity: 两种 transport 各自完成相同验收后才比较业务语义；不比较自然语言逐字相同、远端 ID 或时间戳。Realtime 为固定 canonical，Batch 只可形成经验证的 `duplicate_noop` 或脱敏对照证据；任一失败或语义不等价时不发布任何一份，也不自动增加第三次调用。

## 8. Vertical Implementation Slices

| Slice | Decision IDs | Acceptance IDs | Test seam | Test level | TDD? | If no, why | Modules/files | Focused validation |
|---|---|---|---|---|---|---|---|---|
| S1 单元级合同 | RD-001, RD-002 | AC-001, AC-002, AC-003 | 归一化/验证函数和 prompt 测试 | unit/integration | Yes | — | `consultation_rag.py`, `model_cleaning.py`, focused tests | 单元级保留、辅助降级、方案改写、50+ 引用 |
| S2 Worker/去重 | RD-001, RD-002 | AC-001, AC-002, AC-005 | realtime/retry/publish 任务和 dedupe 查询 | unit/integration | Yes | — | `model_cleaning.py`, `cleaning.py`, task tests | 两次上限、historical retry fail-closed、实际 transport 计价、methodology 文档、删除释放 |
| S3 OCR 路由 | RD-003 | AC-004 | PDF 类型分类/后端 fake | unit | Yes | — | `pdf_convert.py`, `rapidocr_backend.py`, OCR tests | 图文混合、页序、错误分类、隐私路径 |
| S4 隔离资产 | RD-004, RD-005, RD-006 | AC-006, AC-007 | settings/API guard/Compose/transport 静态合同 | unit/static | Yes | — | test Compose、settings/router/UI/scripts/tests | 指定 `general`、课程写拒绝、Redis DB 6、alias/volume、Batch acceptance、canonical、累计 ¥5 / 北京时间单日 ¥4 限制 |
| S5 分组回归 | RD-001--RD-006 | AC-001--AC-007 | 六类样本清单、四策略本地 parity、两策略真实对照与现有测试 | integration/system | No | 真实模型调用和远端写入受部署门禁限制 | reports/scripts/test suite | 先记录、后统一修复、重跑；按直接相关集合分组，不宣称全仓 PASS |

S5 以当前执行计划冻结的六类 15 份原始输入白名单、编码和 PDF 页结构为准；四策略模型 parity 与六类输入是不同维度。模型运行前根据原始文本及 PDF 页面冻结来源关键内容、查询和预期引用；不得以待测转换结果独自生成标准。

| 证据组 | 实施切片 | 主要证据 | 关联 AC |
|---|---|---|---|
| EV-01 | S4/S5 | 15 份原字节、上传、管理 UI/API 终态 | AC-006 |
| EV-02 | S3/S5 | GB18030、混合/扫描/长 PDF、OCR、页序和预标内容 | AC-002、AC-004 |
| EV-03 | S1/S2/S5 | 单元容错、两次上限、双路对照、费用和发布 hold | AC-001、AC-003、AC-007 |
| EV-04 | S2/S5 | Dify 分块、Metadata、正文、索引及直接检索 | AC-005、AC-007 |
| EV-05 | S5 | 商业顾问 RAG/SSE、朋友圈与写作会话、摘要 Worker→Workflow→持久化 | AC-006 |
| EV-06 | S1/S2/S5 | 旧接口、鉴权、错误、SSE 与来源特定负例 | AC-003、AC-006 |
| EV-07 | S2/S4/S5 | 旧记录、迁移、重复/失效、共享引用、资源隔离 | AC-005、AC-006 |
| EV-08 | S2/S4/S5 | 最终保留、临时对象精确清理、Provider 文件和删除队列 | AC-005、AC-006 |

## 9. Canonical Quality Commands

- Format/lint/typecheck/unit/integration：采用 `.codex/governance/project.json` 的命令；本周期以完整差异、模块所有权和隔离风险确定直接相关集合，保留旧全量命令及失败证据，不把集合结果宣称为全仓 PASS；变更后将新增相关文件纳入必要的 ruff/compile/pytest 覆盖。
- Feature focused：先运行本特性的合同、Worker、PDF/去重、隔离部署 guard 测试，再运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .codex/governance/gov.ps1 quality`。

## 10. Rollout / Migration / Rollback

- **实施优先级：**以 SPEC 的“本次交付的核心规则”为准，主路径是在 ys 现有专用服务受控更新并以同一最终候选验收。每次接续先确认已部署范围、尚缺的必要门禁及范围内测试结果；只补当前候选必需的检查，门禁满足后推进实际部署与验收。
- 本地：不迁移生产数据；创建脱敏 issue report，分组基线后统一修复和回归。
- ys：先只读预检共享 Dify/PostgreSQL/Redis、网络、端口、镜像、指定四 App 与 `general`、项目专用数据库和 Redis DB 6、费用账本、隐式任务与可回退边界；在有效精确命令批准后部署同源 API/UI/OCR 镜像及各 Worker、Scheduler。启动前证明不会因旧任务或 Worker 自动恢复触发模型费用；真实付费 parity 仅限咨询和知识星球。费用无法核清只停止可能计费动作。
- 失败处置：本地隔离 UI 合同测试失败时仅处理该测试环境，不以其状态代替 ys 验收。ys 更新失败时仅操作本项目专用服务，保留更新前候选及匹配配置；数据库不直接 downgrade 丢弃代际字段。不执行 `down -v`，不删除共享卷、数据库、Redis 或 Dify 文档。

## 11. Escalation Triggers During Luna Implementation

- 需要新数据表、状态枚举、公共 API 破坏性变化、Dify DSL 改动、OCR 新服务/模型或变更正式 `recent_activity` 行为。
- 无法用现有 details 表达部分成功且不影响兼容。
- 服务端无法证明指定 `general` 与课程关闭状态、项目专用 PostgreSQL/Redis、Worker、卷和网络与共享服务隔离。
- 真实样本暴露受保护身份、无法建立来源回指，或 OCR 有影响事实的冲突。

## 12. Open Questions

None

<!-- gov:implementation-approval:begin -->
## 13. Approval
- Requirements approved and current: YES
- Architecture ready: YES
- Implementation plan approved: YES
- Ready for Luna: YES
<!-- gov:implementation-approval:end -->
