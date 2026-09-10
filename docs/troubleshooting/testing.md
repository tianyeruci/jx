# 测试与验收排障

## TEST-001：基线不能被单项失败提前截断

- 适用：完整基线和远端 runner。
- 触发：权限失败、异步任务仍在处理或单项断言失败。
- 根因：runner 把请求失败、处理中和终态失败混为一类，或遇错立即退出。
- 解决：每组独立记录退出码，异步任务轮询到明确终态，保留所有差异；统一修复后从头重跑。
- 非解决：中途修改服务行为后继续把同一批次称为基线。
- 验证：基线报告包含全部测试组和终态，修复后使用新批次从头执行。
- 预防：runner 默认聚合失败并在末尾统一返回非零退出码。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:56)。
- 最后验证：2026-07-30。

## TEST-005：长套件无终态时必须拆分并保留阻塞证据

- 适用：本地清洗/模型全量回归。
- 触发：合并 `pytest -q` 超过工具等待上限且没有退出码，直接被口头写成通过。
- 根因：数据库夹具初始化、10 万候选分片或多个工作流文件叠加后，单个命令耗时可能超过交互等待窗口；无输出不代表无断言失败。
- 解决：先确认 jx 健康和日志，再安全终止已确认的 pytest 进程；按测试文件分组重跑并记录每组退出码、数量和最慢用例。
- 非解决：并发启动共享 SQLite 测试、只看进程存在、或把超时当作通过。
- 验证：R20c 分组收集清单 205 项全部通过；大规模清洗、AI 管理、模型工作流及其余契约组分别有可回读结果。
- 预防：把大规模分片和慢数据库夹具列入单独测试组，统一设置可观测超时并保存每组结果。
- 证据：[R20c 交接状态](../../AI管理服务当前状态与后续测试交接.md:10)、[2026-08-05 合并验收脱敏报告](../../var/reports/20260805-plan-merge/issues.jsonl)。
- 最后验证：2026-08-05。

## TEST-006：受保护测试夹具路径必须与当前工作区一致

- 适用：依赖受保护正文/结构化样本的咨询、清洗和全链路测试。
- 触发：测试收集或执行阶段找不到约定的受保护样本文件。
- 根因：测试曾硬编码一个与当前工作区目录结构不一致的路径；不能由测试代码推断受保护样本内容和版本。
- 解决：先核对 `docs/README.md` 和实际样本目录，再修正测试路径；若样本确实未挂载，仍按输入阻塞处理。不得生成替代正文、复制真实数据或把跳过写成通过。
- 非解决：用空 JSON/合成结构冒充受保护样本，或因追求全绿而放宽断言。
- 验证：2026-08-05 已将 `tests/test_consultation_v2.py` 指向当前工作区的受保护样本；相关测试与当前收集到的全量 328 项测试均通过。历史交接中的 384 项与当前收集清单不一致，已单独记录，不能当作当前数量。
- 预防：运行前检查受保护夹具挂载和版本指纹；报告区分“代码失败”“环境阻塞”和“未执行”。
- 证据：[本轮 issues.jsonl](../../var/reports/20260805-full-test-jx-fix/issues.jsonl)。
- 最后验证：2026-08-05。

## TEST-007：Windows pytest 超时遗留进程会锁住 SQLite 收尾文件

- 适用：Windows 本地使用 SQLite 测试库的 pytest 分组回归。
- 触发：组合测试超过工具等待窗口后，下一次测试收尾删除 `test_ai_management.db` 报 `WinError 32`，或测试命令无输出长期保持运行。
- 根因：外层命令超时不会可靠终止子 pytest 进程；遗留进程仍持有 SQLite 文件句柄，导致断言已通过但会话收尾退出码为非零。
- 解决：先用进程命令行确认占用者确实是本任务启动的 pytest，再只终止这些精确进程；随后按测试文件或 `-k` 分组重跑，并分别记录断言结果和收尾状态。默认测试数据库改为按 pytest 进程生成临时路径；显式 `TEST_DATABASE_PATH` 时才使用指定路径。
- 非解决：直接删除 SQLite 文件、终止所有 Python 进程、并发运行共享 SQLite 夹具或只看外层超时退出码。
- 验证：2026-08-05 首次组合超时后按命令行确认并精确结束遗留 pytest 进程；随后使用仓库根目录 `PYTHONPATH` 的全量 `pytest -q` 共 328 项通过，结束后无非 shell pytest 进程。旧 SQLite 文件未删除。
- 预防：长套件使用可观测分组和足够超时；默认使用进程隔离的临时 SQLite 路径；报告分别记录“断言失败”“测试进程超时”和“Windows 收尾锁文件”。
- 证据：本轮脱敏测试终端记录及 `tests/conftest.py` 的 SQLite 夹具逻辑、本轮 [issues.jsonl](../../var/reports/20260805-full-test-jx-fix/issues.jsonl)。
- 最后验证：2026-08-05。

## TEST-008：模型工作流测试必须显式固定 Batch 开关

- 适用：模型清洗审批、排队和 realtime fallback 的测试。
- 触发：本地环境默认 `BAILIAN_BATCH_ENABLED=false` 时，需验证 Batch 审批的测试误走 realtime Celery，并尝试连接不可用的本地 Redis，表现为长时间等待或套件超时。
- 根因：测试继承了运行环境开关，却没有显式声明被测分支的业务前提。生产默认关闭 Batch 是有意配置，不应为测试方便而全局打开。
- 解决：断言 Batch 审批/排队的测试显式设置 `bailian_batch_enabled=true`；断言 realtime 的测试显式设置 false 并隔离异步投递。
- 非解决：修改生产默认值、启动本地 Redis 伪造测试通过，或把 realtime 连接超时当作 Batch 功能失败。
- 验证：2026-08-05 `tests/test_model_cleaning_workflow.py` 及模型契约安全回归通过；全量 `pytest -q` 当前收集 328 项且全部通过。
- 预防：测试用例必须显式固定影响路由的配置；报告记录“生产默认值”和“测试分支前提”两者的区别。
- 证据：[本轮 issues.jsonl](../../var/reports/20260805-full-test-jx-fix/issues.jsonl)。
- 最后验证：2026-08-05。

## TEST-009：pytest 入口必须显式加入仓库根目录，历史测试数量需以当前收集为准

- 适用：Windows hermes pytest 入口、包含 `scripts` 包的项目测试和历史交接报告核对。
- 触发：直接执行 pytest 收集阶段出现 `scripts` 包导入错误，或交接文档的历史测试数量与当前 `--collect-only` 清单不一致。
- 根因：当前 pytest 可执行文件的启动路径不总是包含仓库根目录；历史“384 项”是旧快照，当前仓库收集清单实际为 328 项。
- 解决：从仓库根目录设置 `PYTHONPATH=(Get-Location).Path` 后执行 pytest；先用 `--collect-only -q` 统计当前清单，再报告通过数量。导入错误不算测试通过，也不通过修改生产 Batch 开关规避。
- 非解决：忽略收集阶段 ImportError、把历史 384 项直接写成当前结果，或为测试方便打开生产 Batch。
- 验证：2026-08-05 `PYTHONPATH=repo-root pytest -q` 全量 328 项通过；索引生成和文档契约检查通过。
- 预防：测试报告保存启动命令、收集数量和退出码；交接文档引用当前证据，不复制旧数字。
- 证据：[本轮 issues.jsonl](../../var/reports/20260805-full-test-jx-fix/issues.jsonl)、`pyproject.toml`、`tests/conftest.py`。
- 最后验证：2026-08-05。

## TEST-002：Dify 内建 Metadata 不等于服务泄露

- 适用：清洗验收和 Metadata 断言。
- 触发：验收脚本把 Dify 返回的内建字段与服务提交字段一起比较。
- 根因：平台内建字段与服务自定义 Metadata 属于不同责任边界。
- 解决：分别断言策略允许的自定义字段和平台内建字段；保留实际索引、分块和回读证据。
- 非解决：把 Dify 内建字段直接判定为服务提交或隐私泄露。
- 验证：自定义字段白名单通过，内建字段单独记录且不改变服务泄露结论。
- 预防：契约测试显式维护“服务字段”和“平台字段”两组断言。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:81)。
- 最后验证：2026-07-30。

## TEST-003：浏览器上传必须定位可见入口

- 适用：Streamlit 文件清洗页。
- 触发：自动化按无障碍树中的 `Choose File` 原生隐藏输入进行鼠标点击，得到被容器拦截或不可点击。
- 事实：`st.file_uploader` 使用非空可见标签是官方要求；当前页面的“选择文件”标签已满足该要求。
- 根因：测试点击了组件内部隐藏输入，而不是用户可操作的上传入口。
- 解决：点击可见上传触发器或监听 file chooser 后设置文件；用上传后的文件名和批次结果验证，不用 CSS/`aria-hidden` 隐藏原生输入。
- 非解决：强制点击隐藏原生输入，或为了自动化移除可见标签。
- 验证：文件选择器成功接收文件，页面显示文件名并能进入后续批次流程。
- 预防：浏览器验收优先使用可见控件和 file chooser 事件。
- 证据：[Streamlit 1.41 file uploader](https://docs.streamlit.io/1.41.0/develop/api-reference/widgets/st.file_uploader)。
- 最后验证：2026-07-30。

## TEST-010：独立脚本包缺少 pytest 时要保留未执行标记并运行无依赖断言

- 适用：独立清洗/分块脚本包的 Ubuntu/Python 验证。
- 触发：环境没有 `pytest`，但测试包内的 `test_*` 函数只依赖标准库和普通 `assert`。
- 根因：可选测试 runner 缺失，不等于被测脚本失败或通过；临时联网安装会扩大任务范围并改变环境基线。
- 解决：先记录 `pytest` 未执行；用标准库手动收集并调用包内 `test_*` 函数，再运行 `py_compile`、脚本自带 fuzz 和真实目录验证。手动断言通过只能作为补充证据，报告仍明确区分 pytest 未执行。
- 非解决：联网安装依赖、把未执行写成 pytest 通过，或只看脚本退出码而不保存测试数量和失败项。
- 验证：2026-08-09 钩子资料分块包手动回归 `36 passed`，500-case fuzz `failures=0`，真实目录验证 `files=5 failures=0`；详见[本轮报告](../../var/reports/20260809-lead-magnet-chunker/README.md)。
- 预防：测试前先检查 runner 可用性，报告同时记录 pytest 状态、替代断言数量、fuzz 结果和真实目录结果。
- 证据：`var/reports/20260809-lead-magnet-chunker/README.md`、`validate_chunker.py` 手动回归输出。
- 最后验证：2026-08-09。

## TEST-011：目录分块回归必须验证条目、父边界、删除和幂等四项守恒

- 适用：PDF 转 Markdown 后的钩子资料父子分块脚本。
- 触发：总父块/子块数量看似正常，但目录仍进入知识正文，或目录条目与正文标题之间存在漏映射。
- 根因：旧验收只检查 marker 合法、warning 和总体数量，没有验证目录条目是否逐项映射到父边界，也没有把“删除目录”纳入正文守恒规则。
- 解决：验证器先识别允许删除的目录区块，再比较剩余正文可见字符；同时断言目录条目数=匹配数=目录映射父边界数、目录删除字符大于 0，并二次运行比较 Markdown/metadata 哈希。
- 非解决：只比较父块总数、按文件名或历史数量判断通过、把 pytest 缺失写成通过，或用模糊标题匹配掩盖未定位条目。
- 验证：2026-08-09，包内标准库回归 `40/40`，500-case fuzz `failures=0`，本地及 jx 真实目录 `files=5 failures=0 warnings=0`；含目录文档为 `25/25` 条目、`25/25` 父边界，隔离输出二次运行哈希一致。
- 预防：目录相关改动必须保留脱敏的条目/父边界/删除/幂等证据；pytest 不可用时单独记录并运行标准库测试函数。
- 证据：`文件清洗样本/钩子资料清洗/validate_chunker.py`、`test_generic_chunking.py`；本轮脱敏报告见 `var/reports/20260809-lead-magnet-toc-parent/`。
- 最后验证：2026-08-09。

## TEST-004：表单输入必须在提交时批量读取

- 适用：测试页破坏性操作。
- 触发：按钮 disabled 状态依赖上一次 rerun 的输入值，用户必须先按 Enter 才能启用按钮。
- 根因：Streamlit rerun 时序使按钮状态读取了提交前的旧输入。
- 解决：使用 `st.form(..., enter_to_submit=False)`，提交后再次校验用户 ID 和确认短语；无效输入不得发请求。
- 非解决：只依赖按钮 disabled 状态作为服务端安全门禁。
- 验证：无需额外按 Enter 即可提交有效表单，无效确认短语不会产生请求。
- 预防：破坏性操作统一使用表单提交并在提交分支内重新校验。
- 证据：[Streamlit 1.41 form](https://docs.streamlit.io/1.41.0/develop/api-reference/execution-flow/st.form)。
- 最后验证：2026-07-30。

## TEST-012：受限 Windows 临时目录必须改用工作区隔离 basetemp

- 适用：受管 Windows runner 中运行需要 `tmp_path` 的 pytest 分组或全量测试。
- 触发：pytest 尚未执行测试断言，就在默认系统临时目录扫描阶段报 `WinError 5`/`PermissionError`。
- 根因：runner 身份不能枚举系统临时目录的历史子目录；这不是测试夹具或业务代码失败。
- 解决：从仓库根目录设置 `PYTHONPATH`，使用仓库内明确、仅供本轮使用的 `--basetemp`；不要删除系统临时目录或终止无关进程。
- 非解决：把临时目录创建失败计入业务失败、删除整个系统 Temp，或并行复用同一 SQLite 测试目录。
- 验证：2026-08-10，直接相关分组 `67 passed`；全量当前收集 `397` 项，除已有冻结接口摘要断言外其余通过。
- 预防：长套件记录 `--basetemp`、当前收集数量和退出码；若只修复测试 harness，必须单独运行并记录对应回归。
- 证据：[2026-08-10 续测记录](../../var/reports/20260810-plan-continuation/README.md)、`tests/test_cleaning_release_contract.py`。
- 最后验证：2026-08-10。

## TEST-013：冻结接口摘要失败时先区分基线漂移与业务回归

- 适用：带“接口文档冻结摘要”断言的清洗发布契约测试。
- 触发：测试只失败冻结文档 SHA-256，业务/清洗相关断言均通过；文档还可能带 Git `assume-unchanged` 标记。
- 根因：工作树文档内容与测试保存的历史冻结值不一致，属于测试基线漂移；它不能单独证明本次清洗、API 或发布逻辑改变了对外接口。
- 解决：先核对失败文件、当前摘要、Git 状态和本轮变更范围；若文件是无关或受保护文档，保留现场并将该项单独标为基线失败，不回滚、覆盖或强行刷新摘要。只有确认接口内容应变更时，才由接口变更任务更新文档和冻结值。
- 非解决：为了让套件全绿而重置工作树文档、取消冻结断言、把该项计入业务失败，或在没有接口变更评审的情况下改写冻结值。
- 验证：2026-08-10，全量收集 `397` 项，其中 `396` 项通过；唯一失败为该冻结摘要断言。排除该断言后，相关回归和发布契约通过。
- 预防：报告同时记录收集数、失败测试名、文件摘要和是否属于本轮改动；将“代码回归”与“测试基线维护”拆成两个变更。
- 证据：[2026-08-10 续测记录](../../var/reports/20260810-plan-continuation/README.md)、`tests/test_cleaning_release_contract.py`。
- 最后验证：2026-08-10。

## TEST-014：最近活动验收必须包含普通问题负向路由

- 适用：商业顾问 Chatflow 增加活动/会议检索分支后的 jx 或隔离 Dify 验收。
- 触发：正常、取消、延期活动查询都能返回结果，但“如何提高活动报名转化率”等分析问题仍带出活动列表或活动检索上下文。
- 根因：只测活动意图正例无法证明活动分支与普通商业分析边界；另外，测试页可能仍调用旧 App，不能把旧路由结果归因于新版 DSL。
- 解决：将活动正例（最近/取消/延期/延期未定）与负例（活动分析、活动复盘、普通订单或经营问题）成对执行；同时记录实际 App Key 映射和服务容器重建状态，检查负例未进入活动检索/列表回答。新版 Chatflow 未切换前，负例失败应记录为阻塞，不得用正例通过覆盖。
- 非解决：只检查 HTTP 200 或 `message_end=completed`、只测试“最近有哪些活动”、只看 Dify 控制台发布状态，或把旧 App 的回答当作新版验收。
- 验证：2026-08-16 先复现旧绑定 App 的普通活动分析误路由；完成 App Key 同步并修正当前 App 的案例优先判断后，测试页正常/取消/延期、普通业务负例和“会议真实案例”重叠负例均显示最终回答，诊断回读确认 `message_end.status=completed`。
- 预防：验收清单固定包含正/负向意图、App 映射、`/parameters`、SSE 结束状态和知识库检索上下文五项证据。
- 证据：`tests/test_dify_activity_contract.py`、`tests/test_business_advisor_unified_dsl.py`、当前状态交接文档 0.61。
- 最后验证：2026-08-16。

## TEST-015：模型 realtime/Batch parity 要分开验证本地中间态和远端知识库

- 适用：四个内容模型策略在 jx 的 Qwen realtime/Provider Batch 双模式验收。
- 触发：测试报告只有候选数、批次状态或单条 Dify 抽样，没有逐文档远端 metadata/索引回读；或把小报童、知识星球拆分数量差异报告成失败。
- 根因：模型允许生成不同数量的知识单元；本地完整 metadata 包含来源/审计字段，Dify 只接收策略白名单和少量通用字段，并附加平台内建字段。
- 解决：按任务模式分别统计候选数、文件/文档终态、Dify ID；对全部关联远端文档回读 `indexing_status` 和 `doc_metadata`，在策略内比较远端 key set 和必需字段；报告单独记录本地可选字段差异与模型拆分差异。
- 非解决：只检查 HTTP 200、只抽查一条远端文档、只比较文档数，或用固定 seed 宣称模型输出逐字一致。
- 验证：2026-08-20 全量远端回读四策略均无错误，所有关联文档 `indexing_status=completed`，同一策略 realtime/Batch 的远端 metadata key set 一致；质量门禁和 Qwen/Dify E2E 检查通过。
- 预防：验收脚本保留脱敏 JSON 证据；Batch 测试完成后恢复 `BAILIAN_BATCH_ENABLED=false`，不删除用户要求保留的测试数据/任务。
- 证据：[`full_remote_metadata_parity.safe.json`](../../var/reports/20260820-qwen-activity/full_remote_metadata_parity.safe.json)、`scripts/verify_qwen_activity_e2e.py`。
- 最后验证：2026-08-20。

## TEST-016：流式 HTTP 非成功响应必须先读取响应体再解析错误

- 适用：`httpx.Client.stream()` 驱动的 Streamlit 后台 SSE 请求。
- 触发：接口在建立 SSE 前返回 4xx/5xx，后台线程抛出 `httpx.ResponseNotRead`，页面既收不到稳定错误码，也无法执行既定的失败清屏逻辑。
- 根因：流式响应体默认未缓冲；直接 `raise_for_status()` 后再对 `exc.response` 调用 `json()`，会在错误处理路径再次抛异常。
- 解决：在 `with client.stream(...) as response` 内发现非 2xx 时先调用 `response.read()`，再 `raise_for_status()`；之后复用统一的 HTTP 错误解析和队列事件逻辑。
- 非解决：吞掉异常、把预检错误伪装成空 SSE，或仅在页面捕获 `ResponseNotRead` 而丢失服务端错误码。
- 验证：`tests/test_test_ui.py::test_background_stream_reads_http_error_before_decoding` 通过；三类顾问聚焦回归共 194 项通过。
- 预防：所有流式 HTTP 客户端的非成功分支都应在响应上下文关闭前读取响应体，并对真实 4xx/5xx 增加回归。
- 证据：`ai_management/test_ui.py`、`tests/test_test_ui.py`、`var/reports/advisor-message-actions/issues.jsonl`。
- 最后验证：2026-09-09。

## TEST-017：商业顾问分段流停止时持久正文必须与客户端已见片段一致

- 适用：商业顾问先输出 `progress_answer`、后输出最终 `answer_*` 的分段 SSE。
- 触发：客户端已收到即时提示 `answer_delta` 后停止，终态为 `stopped`，但回读助手消息正文为空。
- 根因：即时提示与最终答案分别累积在 `progress_answer_parts`、`answer_parts`；停止发生在最终段开始前时，旧持久化逻辑只读取空的 `answer_parts`。
- 解决：仅在终态为 `stopped`、最终答案片段为空且即时提示已实际发送时，持久化即时提示片段；一旦有最终答案片段仍只保存最终段，`failed` 始终保持空正文。
- 非解决：把即时提示永久拼进完整答案、对失败终态保存片段，或恢复停止前已经作废的旧答案。
- 验证：`tests/test_ai_management.py::test_business_stop_persists_progress_fragment_when_final_answer_has_not_started` 通过；三类顾问聚焦回归共 194 项通过。
- 预防：分段 SSE 的停止测试同时断言“客户端收到的片段”和“本地同一助手消息回读正文”，并覆盖 progress-only 与 final-started 两条路径。
- 证据：`ai_management/conversations.py`、`tests/test_ai_management.py`、`var/reports/advisor-message-actions/live-acceptance-initial.json`。
- 最后验证：2026-09-09。

## TEST-018：Streamlit fragment 终态必须触发整页刷新并持久渲染结果

- 适用：测试页使用 `st.fragment(run_every=...)` 轮询后台 SSE 的商业、朋友圈和写作入口。
- 触发：Dify 已生成成功且 session state 已保存 answer/message ID，但答案和反馈/重新生成控件在终态后不稳定出现，或约一个 fragment 周期后消失。
- 根因：fragment 只重绘自身；旧逻辑在 `__done__` 后删除 active entry，却不触发 app-scope rerun，fragment 外的持久结果区和消息操作控件不会重新执行。
- 解决：终态保存结果并删除 active entry 后调用 `st.rerun(scope="app")`；商业和工具的常规结果区显式渲染已保存的 progress/final answer，再渲染诊断及操作控件。
- 非解决：延长 fragment 周期、依赖用户手工点击触发整页刷新，或只把答案放进默认折叠的诊断 JSON。
- 验证：`tests/test_test_ui.py` 覆盖 app-scope rerun 和持久结果渲染；真实浏览器仍需在新 jx release 后跨多个 fragment 周期复验三入口。
- 预防：Streamlit fragment 测试必须区分“fragment 内临时输出”和“整页终态持久输出”，并断言操作控件无需额外交互即可出现。
- 证据：`ai_management/test_ui.py`、`tests/test_test_ui.py`、`var/reports/advisor-message-actions/issues.jsonl`。
- 最后验证：2026-09-09。

## TEST-019：SSE conversation ID 重复事件不能造成按 token 数据库提交

- 适用：工具 Dify 流的多个 token/message 事件重复携带同一 `conversation_id`。
- 触发：短回答也产生大量 PostgreSQL commit，SSE delta 在写事务后才发送，并发时连接池和数据库写入被无意义放大。
- 根因：旧逻辑只要事件包含 `conversation_id` 就更新活动时间并提交，没有区分首次绑定和重复回显。
- 解决：仅在本地远端 ID 为空时提前绑定并提交一次，确保首轮停止/断开仍可精确清理；相同 ID 的重复事件不提交，终态统一更新活动时间并提交。
- 非解决：完全延迟到终态而丢失首轮提前停止的清理标识，或取消 conversation switch 的 fail-closed 校验。
- 验证：100 个重复 conversation ID 增量的回归中，请求 session 只提交“首次绑定 + 终态”两次。
- 预防：流式持久化回归使用大量重复 token 事件并对同一请求 session 的 commit 次数设置常数上限。
- 证据：`ai_management/api/routers/tools.py`、`tests/test_tool_conversations_contract.py`。
- 最后验证：2026-09-09。
