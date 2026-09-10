# Dify 联调排障

## DIFY-001：只修改本地 DSL/Prompt 不会更新已发布 Workflow

- 适用：Dify 应用行为变更。
- 触发：本地文件已改，线上 Workflow 仍使用旧分支或旧提示词。
- 根因：仓库中的 DSL/Prompt 文件不会自动替换 Dify 已导入并发布的应用版本。
- 解决：导出线上旧版本，导入/发布目标隔离应用，发布后核对应用参数和三个入口真实调用；不输出 Key。
- 非解决：只修改或提交本地文件后宣称线上应用已更新。
- 验证：已发布应用参数、版本和三个入口真实调用均对应目标文件。
- 预防：DSL/Prompt 变更完成条件必须包含导入、发布和契约回归证据。
- 证据：[Dify 联调问题与修复记录](../V2-Dify联调问题与修复记录-2026-07-28.md:20)、[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:255)。
- 最后验证：2026-07-30。

## DIFY-002：Code 节点 401 先核对 Sandbox 密钥哈希

- 适用：含 Dify Code 节点的 Workflow。
- 触发：Code 节点调用 Sandbox 返回 401。
- 根因：Dify API 与 Sandbox 使用的密钥配置不一致。
- 解决：仅比较 API 与 Sandbox 密钥哈希是否一致；不输出密钥，不操作 Dify 业务数据库或持久卷。
- 非解决：打印明文密钥、直接改业务数据库或删除持久卷。
- 验证：哈希一致后 Code 节点调用通过，日志和报告中无明文密钥。
- 预防：配置核验只保存脱敏哈希与服务身份。
- 证据：[Dify 联调问题与修复记录](../V2-Dify联调问题与修复记录-2026-07-28.md:45)。
- 最后验证：2026-07-28。

## DIFY-003：创建文档不等于索引成功

- 适用：知识库清洗和 RAG。
- 触发：创建接口返回成功和文档 ID，但索引、分块或回读未完成。
- 根因：文档创建与异步索引是不同阶段，后续处理仍可能失败。
- 解决：同时核对创建响应、索引终态、父子块数量、允许的自定义 Metadata 和回读结果；失败项阻止源文件完成。
- 非解决：只断言 HTTP 200 或文档 ID 存在。
- 验证：索引达到成功终态，父子块、Metadata 和回读结果均满足契约。
- 预防：发布和验收脚本统一等待明确索引终态。
- 证据：[r3 验收记录](../接口契约与清洗全链路验收记录-2026-07-30.md:74)。
- 最后验证：2026-07-30。

## DIFY-004：证据总预算判断不能让首条超长 Chunk 清空全部证据

- 适用：知识检索后通过 Code 节点压缩证据，再送入 LLM。
- 触发：第一条检索结果长度超过总预算，压缩器在加入任何结果前直接 `break`，最终把“有召回”错误变成“无证据”。
- 根因：只做总长度判断，没有先限制单条内容和 Metadata；第一条结果本身就超过预算。
- 解决：先按字段裁剪单条内容和 Metadata，再把裁剪后的块放入总预算；只保留当前工作流确实需要的、安全的检索语义或来源字段，不能为已退役的课程/路线字段预留预算。
- 非解决：直接提高总预算、把原始检索数组全部送给 LLM，或在 Prompt 中让模型自行忽略噪声。
- 验证：使用首条内容和无关 Metadata 均远超预算的合成结果，输出仍为 `has_evidence=true`，总长度不超过预算，并且不包含无关或已退役的课程/路线 Metadata。
- 预防：证据压缩测试同时覆盖“首条超长”“多条累计超长”“当前检索语义字段保留”和“无关 Metadata 剔除”。
- 证据：[商业顾问 DSL 优化与隔离验收记录](../商业顾问DSL优化与隔离验收记录-2026-07-31.md:61)、`tests/test_business_advisor_optimized_dsl.py`。
- 最后验证：2026-07-31（本地代码节点回归）；隔离 Dify 运行态验证待完成。

## DIFY-005：共享 Docker 网络中的 `api` 别名会让 Dify 请求误路由

- 适用：jx 与 Dify 共用外部 Docker 网络的测试部署。
- 触发：Dify 控制台请求出现 502、重试或长期 pending；Nginx 日志显示连接到错误项目的 `api:5001`，或 `getent hosts api` 同时返回 jx 与 Dify 服务。
- 根因：Docker Compose 会把两个项目的服务名都暴露为 `api`；Dify Nginx 和内部配置访问 `http://api:5001` 时，DNS 结果混入 jx API（端口不匹配）或其他同网容器。
- 解决：给 Dify API 配置独占的 `dify-api` 网络别名，并把 Nginx、`PLUGIN_DIFY_INNER_API_URL`、`INTERNAL_FILES_URL` 统一切换到该别名；jx 测试页使用 `ai-management-api:8000`。同时给 API healthcheck 的 curl 增加客户端 `--max-time`，防止异常 Worker 时检查进程堆积。
- 非解决：只重启 Nginx、只刷新浏览器、只重启 jx API，或用 `--remove-orphans` 删除历史容器；这些不能消除同名 DNS 冲突且可能破坏回滚证据。
- 验证：`getent hosts dify-api` 只返回 Dify API 副本；两个 API/Worker healthy；Dify `/apps`、`/console/api/system-features`、`/v1/parameters` 连续 smoke 无超时；jx 测试页返回 200；登录态 Chatflow 预览页的 `advanced-chat/workflows/draft/run` 返回 HTTP 200 `text/event-stream` 并完成流式响应，浏览器无 Console 错误。
- 预防：共享网络变更后检查所有服务别名和 `proxy_pass`/内部 URL；禁止跨项目依赖通用服务名；健康检查必须同时设置服务端和客户端超时。
- 证据：`var/reports/20260803-dify-pending/issues.jsonl`、远端备份 `/opt/ai-management/backups/20260803-193603-dify-pending-root-cause-fix`。
- 最后验证：2026-08-03（jx 测试环境基础链路与登录态 Chatflow SSE 已验证）。

## DIFY-006：RapidOCR/ONNX Runtime 在 Dify Sandbox 默认 seccomp 下被阻断

- 适用：Dify 1.13.3 的 Code 节点在 Sandbox 0.2.14 中加载 RapidOCR/ONNX Runtime。
- 触发：PyMuPDF 文字分支成功，但扫描 PDF 分支返回 `operation not permitted`；Sandbox 日志出现 `signal: bad system call`。
- 根因：Sandbox 子进程 seccomp syscall allowlist 没有覆盖 ONNX Runtime 所需调用；仅在容器内成功 `import` 不能证明 Code 节点可执行。
- 解决：仅在测试窗口使用隔离的离线 Sandbox 镜像安装 PyMuPDF、RapidOCR、ONNX Runtime 和 headless OpenCV；临时扩大子进程 syscall 白名单、延长 Code/API 读取超时，并把 SSRF 规则限制到测试文件主机；完成后按原始备份恢复。
- 非解决：只安装 Python wheel、只重启 Sandbox、只验证容器 import，或把临时 syscall 放宽直接带入生产。
- 验证：Dify Sandbox `/v1/sandbox/run` 文字 PDF 为 `succeeded/pymupdf`；扫描 PDF 为 `succeeded/rapidocr`、3 页、平均置信度 `0.9969`；Dify API 与 Sandbox health 均正常。
- 预防：将依赖镜像、syscall、超时和下载白名单作为同一份可回滚测试配置管理；生产启用前完成最小权限、资源、镜像和许可证评审。
- 证据：`var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`、远端备份 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre`。
- 最后验证：2026-08-04（jx 测试环境；不代表生产批准）。

## DIFY-007：RapidOCR 成功结果被 Sandbox 的 stderr 误判为 Code 节点失败

- 适用：Dify 1.13.3 的 Code 节点调用 RapidOCR/ONNX Runtime。
- 触发：追踪日志只有 RapidOCR/ONNX Runtime INFO/WARNING，Code 节点显示运行异常；Sandbox 响应同时包含有效 `stdout` 结果和非空 `error`。
- 根因：RapidOCR 与 ONNX Runtime 将初始化日志写入原生 stderr；当前 Sandbox 将非空 stderr 包装为 `RuntimeError`，即使进程退出码为 0 且 stdout 已生成成功结果。
- 解决：在 Code 节点仅包住 OCR 初始化和识别调用，使用内存管道和 drain 线程吸收原生 fd 2，恢复后继续返回结构化结果；同时关闭 RapidOCR Python logger 并提高 ONNX 日志级别，仍保留异常捕获和结果状态。
- 非解决：把 INFO/WARNING 当作 OCR 识别失败、仅提高执行超时、依赖 `/dev/null`/临时目录作为日志 sink，或只检查容器内依赖 import。
- 验证：原失败响应的 `stdout` 为 `status=succeeded/backend=rapidocr`，`error` 仅为 RapidOCR/ONNX Runtime 日志；修复后 Code 代码静态编译、DSL 契约和现有 Dify DSL 测试通过。
- 预防：Dify Code 节点验收必须同时检查退出码、stdout 结构化结果和 stderr；带原生库的 Code 节点应显式控制日志输出。
- 证据：`var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`、远端 Sandbox 响应 `response-image.json`（仅脱敏运行证据）。
- 最后验证：2026-08-04。

## DIFY-008：受限 Sandbox 同时缺少 `/dev/null` 和可用临时目录

- 适用：Dify 1.13.3 测试 Sandbox 的受限 Code 子进程。
- 触发：使用 `/dev/null` 静默 stderr 报 `No such file or directory`；改用 `NamedTemporaryFile` 又报 `No usable temporary directory found`。
- 根因：该受限运行时不提供标准 `/dev/null`，并将 `/tmp`、`/var/tmp` 等目录标记为不可用；基于文件的 stderr 重定向不成立。
- 解决：使用 `os.pipe()` 创建内存管道，后台线程持续 drain 写端，OCR 调用期间将 fd 2 重定向到管道，完成后恢复并关闭管道。
- 非解决：继续尝试 `/dev/null`、临时文件、扩大文件系统权限或修改 Sandbox 容器文件系统。
- 验证：指定扫描 PDF 完整 3 页执行返回 `code=0`、`error` 为空、`status=succeeded`、`backend=rapidocr`、平均置信度 `0.9969`。
- 预防：Dify 受限 Code 节点的原生 stderr 静默优先使用内存管道；不要假设 Linux 标准设备节点或临时目录存在。
- 证据：`var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`、本次脱敏 Sandbox 运行响应。
- 最后验证：2026-08-04。

## DIFY-009：PDF 页数门禁必须在文本/图片分类之后（历史，Workflow 已退役）

- 适用：历史 Dify PDF→Markdown 测试 Workflow；文本层 PDF 与纯图片 PDF 分流。该 Workflow 已于 2026-08-05 退役。
- 触发：多页文本型 PDF 报“超过 max_pages”，即使它不需要 OCR。
- 根因：旧逻辑在读取文字层前统一执行页数门禁，把 OCR 资源限制错误应用到 PyMuPDF 文本分支。
- 解决：先探测前两页文字层；有文字直接走 PyMuPDF 且不受 OCR 页数限制；无文字才检查 `max_pages` 并进入 RapidOCR。
- 非解决：只把默认页数从 5 调大、删除所有页数限制，或按文件扩展名判断 PDF 类型。
- 验证：DSL 静态回归确认文字层探测位于页数门禁之前；已有文字分支 Sandbox smoke 返回 `succeeded/pymupdf`。
- 预防：页数、DPI、资源限制只作用于实际需要 OCR 的分支；分类顺序纳入 DSL 契约检查。
- 证据：`var/reports/20260804-pdf-rapidocr-docker/issues.jsonl`、退役前资产快照；当前实现见 `knowledge_cleaner/pdf_convert.py` 与 `knowledge_cleaner/pdf_markdown_refine.py`。
- 最后验证：2026-08-04。

## DIFY-010：Chatflow 多个 Answer 节点被适配层合并时，必须保留阶段边界

- 适用：商业顾问 Chatflow 同时包含固定即时提示 `progress_answer` 和最终 `answer_*` 节点的流式调用。
- 触发：Dify Chatflow 预览页能先显示即时提示，jx 测试页却把即时提示和最终答案放在同一展示区，并且只有最终生成结束后才看起来像一次完整回答。
- 根因：Dify 1.13.3 对固定 `progress_answer` 节点不发送 `message.answer`，而是在 `node_finished.data.outputs.answer` 返回正文；最终 `answer_*` 节点才通过 `message.answer` 流式返回。旧适配层只处理 `message`，因此能发边界却没有即时提示正文。
- 解决：`ai_management/conversations.py` 在 `progress_answer` 开始时发送现有 `status` 事件的 `progress_answer_started`，在最终 `answer_*` 开始时发送 `final_answer_started`；同时把 `node_finished.data.outputs.answer` 转为原有 `answer_delta`。`ai_management/test_ui.py` 按状态码使用两个展示区，并只把最终段写入助手历史。
- 兼容：没有分栏需求的旧客户端仍可按顺序追加全部 `answer_delta`；没有即时提示的 `quick_answer` 路径保持单段最终回答。
- 验证：本地 `tests/test_ai_management.py::test_business_chat_stream_separates_progress_answer_from_final_history` 通过；`consume_sse` 分栏等价检查通过；目标文件 `ruff check` 和 Python 编译检查通过。
- 验证：本地单测、jx 发布 smoke、真实合成商业顾问请求和公网浏览器测试页均通过；SSE 收到 1 个即时提示 delta、2 个最终答案 delta，顺序和两个展示区符合预期。测试用户会话已按精确 ID 删除。
- 非解决：复制即时提示到最终答案、修改对外 SSE 字段或只在 Dify 预览页验证而不验证 jx 适配层。
- 预防：修改商业顾问 Answer 节点 ID 时同步更新适配层节点常量和 SSE 契约测试；不要为了修复展示问题复制或改造对外 `answer_delta` 字段。
- 证据：`ai_management/conversations.py`、`tests/test_ai_management.py`、`docs/Dify创建导入绑定与契约测试.md`。
- 最后验证：2026-08-05（本地代码、jx 发布、SSE 和浏览器测试页）。

## DIFY-011：目录 ID 不能在服务出口一律删除，必须恢复正式名称（ADR-036 前课程链路，当前新聊天已退役）

- 适用：ADR-036 前商业顾问 Chatflow 返回课程推荐、学习路线或阶段进度的历史场景；当前新聊天不生成这些结果。
- 触发：小程序正文出现“课程ID/阶段ID/路线ID”、`course_`/`stage_`/`route_`，或课程列表全部变成“内部标识已隐藏”；模型还可能因缺少名称而臆造课程名。
- 根因：Chatflow 决策 JSON 必须保留内部 ID 供服务端校验和生成签名卡片，但旧公开文本过滤器只做删除，没有使用已审核课程目录把 ID 映射回名称；路线阶段还依赖来源 `order`，无法保证产品规定顺序。
- 解决：在 `ai-management` 公开文本出口读取目录快照，将有效 ID、标签和列表占位转换为正式名称；错误名称以目录为准，未知 ID 使用不可用提示；结构化 `course_cards`/`learning_route` 及停止、反馈字段保留机器 ID。路线统一按“新手必看 → 入门阶段 → 高手进阶”排序，并通过快照版本使旧缓存自动失效。
- 非解决：从 Chatflow 决策 JSON 删除候选 ID、只在小程序端隐藏字符串、根据 ID 猜测课程名称或把所有无效项静默删除成空列表。
- 验证：`tests/test_stream_filter.py` 覆盖完整/短 ID、标签、括号、跨分片、占位列表、错误名称和未知 ID；`tests/test_ai_management.py` 覆盖流式回答、历史正文和引用资源结构；`tests/test_course_route_catalog.py` 覆盖阶段排序、课程名称、路线资料文本和“确认学习路线”真实操作文案；最终 jx release 真实 SSE 覆盖三条首屏问题、路线确认、阶段确认、课程卡片链接和精确会话清理。
- 当前处理：新聊天不得恢复本条所述课程读取、目录快照或 SSE；仅在明确恢复课程功能的变更中，才按本条历史经验重新评估。
- 预防：新聊天回归同时断言课程/路线 Redis、数据库和 SSE 事件均未访问；恢复课程功能前必须重新建立目录快照与公开出口的专门验收。
- 证据：`ai_management/stream_filter.py`、`ai_management/courses.py`、`ai_management/conversations.py`、上述测试文件。
- 最后验证：2026-08-05（本地定向回归、jx release `quick-20260805-142049-ddc348e62453`、入口配置和真实 SSE）。

## DIFY-012：多文件清洗要做本地批量编排，不猜测 Dify 多文档创建接口

- 适用：用户一次上传多个 PDF/DOCX/Markdown，完成父子块、Metadata 和索引后写入知识库。
- 触发：把多个源文件拼成一个 Dify 文档、并发调用创建接口导致限流，或只看到创建响应就把批次标成成功。
- 根因：Dify `create-by-text` 是单文档创建；响应中的 `batch` 是该文档的异步索引批次，不等同于可提交任意数量源文件的批量创建接口。不同部署版本还可能对请求体、分片条数和并发有独立限制。
- 解决：服务端保留用户外部批次，在本地按文件/文章/知识单元编排；每个逻辑条目逐个调用 `create-by-text`，所有 Dify 写入统一进入 `ai_publication` 单并发 Worker。一个源文件中包含多篇公众号文章或小报童帖子时，先在清洗阶段拆成多个本地条目，再分别创建远端文档；`DIFY_SHARD_MAX_BYTES` 仍限制单条正文，`DIFY_SHARD_MAX_ITEMS` 仅保留为兼容性和防御性校验，不再把多个逻辑条目合并成一个远端文档。每个文档都等待索引、校验父子块、写 Metadata 并回读。多文件不会合并正文，也不会调用仓库未验证的多文档 endpoint。
- 非解决：把 Celery 批次 ID 当作 Dify 多文档 batch、按文件数盲目并发、只核对 HTTP 200、直接改 Dify 业务数据库或为了吞吐取消 Metadata/结构回读。
- 验证：本地发布契约确认 `ai_publication --concurrency=1`、分片上限和 `create-by-text` 单文档调用；远端需按精确 batch/file/shard ID 核对 Dify 索引终态、父子块、Metadata 回读和本地聚合。
- 预防：Dify API 版本升级时先用隔离 Dataset 验证真实上限，再调整配置；发布失败必须保留脱敏异常阶段和精确资源 ID，并刷新文件/批次状态。
- 证据：`knowledge_cleaner/dify.py`、`ai_management/workers/tasks/cleaning.py`、`ai_management/workers/tasks/model_cleaning.py`、`docker-compose.ai.yml`、`tests/test_batch_cleaner.py`、`tests/test_cleaning_release_contract.py`。
- 最后验证：2026-08-06，本地 Dify 客户端、分片和发布队列回归通过；jx release `20260806-165915-full-23162b2` readiness、同源镜像和容器烟测通过。

## DIFY-013：Chrome 中 Dify 隐藏文件控件不能触发 chooser 时，自动接管页面控件

- 适用：已登录的 Chrome Dify 工作区，通过导入 DSL 弹窗自动上传本地 `.yml/.yaml`。
- 触发：`/apps` 可以正常访问、工作区和导入弹窗均可见，但对按钮或隐藏文件控件调用高层 `filechooser` 等待一直超时；这不是登录失效，也不是“必须人工上传”。
- 根因：Dify 导入弹窗使用 `input[type=file][style="display:none"]`；当前 Chrome 自动化封装会拒绝原始 `DOM.setFileInputFiles`，而隐藏控件不会产生可观察的 chooser 事件。
- 解决：仅在目标标签页取得 CDP 能力，用 `DOM.getDocument`/`DOM.querySelector` 确认文件控件后，通过页面级 `Runtime.evaluate` 临时设置 `display:block`、固定位置和较高层级；随后仍使用标准 `playwright.waitForEvent('filechooser')`、`click()`、`chooser.setFiles(<本地 DSL>)` 完成上传。导入完成后不保留页面改动，不读取或输出任何凭据。
- 非解决：反复刷新或猜测未登录、要求用户手工上传、修改 Chrome 扩展权限、直接调用被封装层阻断的 `DOM.setFileInputFiles`，或把密钥写入日志。
- 验证：已自动导入 `商业顾问（当前使用版本）.yml`，创建隔离应用 `eaa840e6-ec85-47e4-89a5-5cf0678f7ac3`；编辑器显示“最新发布/已发布”，案例检索节点存在且绑定 `record_type=consulting_case`。
- 预防：浏览器自动化运行手册优先检查页面是否已登录和隐藏控件结构；遇到同类问题按本条使用标签页级 CDP 临时显示 + 标准 chooser，不把页面权限问题误报为账号权限问题。
- 证据：Chrome Dify 编辑器与 `/develop` 页面；本地 `dify/商业顾问（当前使用版本）.yml`；jx 真实 Chat API/SSE 验收记录见当前状态交接文档 0.54。
- 最后验证：2026-08-10（Chrome 登录态、Dify 导入发布、jx 测试环境）。

## DIFY-014：Dify 缺失结构化决策时仍须先走服务端路线状态（ADR-036 前课程链路，当前新聊天已退役）

- 适用：ADR-036 前商业顾问已确认学习路线后的简短续问；当前新聊天不维护路线状态或推荐决策。
- 触发：SSE `message_end=completed`，却没有 `learning_route` 或 `course_cards`，尽管用户只有一条已持久化的激活路线。
- 根因：持久化层过去只在 Dify 决策或目录推断成功时调用推荐校验，漏掉了服务端已有的路线状态。
- 解决：在目录推断前以中性决策调用服务端路线状态校验；该校验只对路线相关措辞和唯一的 `pending_stage`/`active`/`completed` 进度生效，再由目录快照生成合规结果。
- 非解决：让 Dify 自行猜测历史路线、对任何普通问答强制出卡，或在客户端补造课程卡。
- 验证：jx release `20260811-0912-route-resume-fix` 的合成 SSE 返回精确当前阶段课程卡、其后唯一 `course_recommendation_note`，并以 `message_end.status=completed` 结束；测试会话和进度均精确删除。
- 当前处理：新聊天不得使用本条的路线状态兜底；仅在明确恢复课程功能时，才恢复对应回归和真实 SSE 核验。
- 预防：DSL 或服务端变更后，当前新聊天回归必须断言不读取路线状态、不产生课程卡片和路线 SSE。
- 证据：`ai_management/conversations.py`、`docs/课程推荐与学习路线推荐全链路分析.md`、`/opt/ai-management/var/reports/v2/20260811-0912-route-resume-fix-deploy`。
- 最后验证：2026-08-11。

## DIFY-015：Dify 1.13.3 的变量聚合器输出固定为 `output`

- 适用：Dify 1.13.3 Chatflow 的 `variable-aggregator` 后接 Code、LLM 或 Answer 节点。
- 触发：DSL YAML 可解析、节点连线也存在，但控制台发布检查清单提示“无效的变量。请选择一个已有的变量”。
- 根因：该节点运行时只以 `output` 暴露首个可用变量；把输入检索节点的 `result` 误当作聚合器自身输出会产生无效引用。
- 解决：后继选择器写为 `["<aggregator_node_id>", "output"]`，同时保留聚合输入项中的 `["<retrieval_node_id>", "result"]`。重新导入后先确认检查清单为零，再发布。
- 非解决：跳过/强制发布、把聚合节点删掉导致两条互斥检索不能收敛，或仅修改测试而不修改受控 DSL。
- 验证：2026-08-14 商业顾问 DSL 修复后，Dify 编辑器无发布阻断，发布接口返回成功；`/parameters`、jx `health/ready` 和真实 SSE `message_end.status=completed` 均通过。
- 预防：DSL 测试必须同时断言聚合输入的 `result` 和聚合输出的 `output`；导入新 Dify 版本时仍需以控制台校验作为最终兼容门禁。
- 证据：`dify/商业顾问（当前使用版本）.yml`、`tests/test_business_advisor_optimized_dsl.py`、[商业顾问改造记录](../商业顾问移除课程与学习路线改造记录.md)。
- 最后验证：2026-08-14。

## DIFY-016：Dify Chatflow 发布不等于 jx 服务已切换

- 适用：Dify 1.13.3 中导入并发布隔离 Chatflow，且 jx 通过 `DIFY_BUSINESS_APP_API_KEY` 调用商业顾问。
- 触发：Dify 编辑器显示“已发布”、新版节点可见，但 jx 测试页仍表现为旧路由；控制台发布本身没有改变 jx 的 App Key 映射。
- 根因：Dify 应用发布与 AI 管理服务配置是两个独立状态；服务容器创建时读取 `.env`，旧容器不会因控制台发布自动改用新 App。
- 解决：先保留旧应用和 jx 配置回滚点，完成新版 `/parameters`/节点检查；获得凭据操作授权后在受控配置中更新 `DIFY_BUSINESS_APP_API_KEY`，按 DEPLOY-013 强制重建 `api/worker/scheduler`，再用真实 SSE 验证正向活动状态和普通问题负向路由。导入时若使用临时 HTTP DSL 地址，发布后必须撤回并用精确 URL 检查不再暴露文件。
- 非解决：只看编辑器“已发布”、只重启容器、打印或复制 Key 到报告、删除旧 App、跳过普通问题负向测试，或直接修改 Dify 数据库。
- 验证：2026-08-16 新版活动 Chatflow 已完成受控导入发布，活动节点和动态条件可见，临时公开 DSL 路由已删除；随后 jx Key 已同步并在容器中生效，`/parameters`、健康检查和测试页正负向 SSE 均通过。旧 App 的普通活动分析误路由已作为历史问题复现并关闭。
- 预防：状态交接分别记录“DSL 已发布”“Key 已映射”“容器已重建”“真实 SSE 已通过”四个门；未全部通过不得标记线上生效。
- 证据：`dify/商业顾问（当前使用版本）.yml`、`tests/test_dify_activity_contract.py`、`tests/test_business_advisor_unified_dsl.py`、当前状态交接文档 0.61。
- 最后验证：2026-08-16。

## DIFY-017：当前 App 已发布但代码节点可能仍是旧 DSL 内容

- 适用：Dify 1.13.3 已存在当前商业顾问 App，仓库 DSL 在首次导入/发布后又发生局部代码节点修正。
- 触发：画布能看到新增节点、App 显示“已发布”，但真实查询仍复现旧路由；仅核对节点标题无法证明 Code 节点正文已更新。
- 根因：Dify App 的已发布版本保留的是该 App 最近一次保存的节点正文；仓库文件修正不会自动同步已存在 App，且控制台的节点结构可与 Code 节点源码不同步。
- 解决：在当前 jx 绑定 App 中打开实际 Code 节点，核对关键判定代码和输出变量；保存后等待自动保存，执行“发布更新”，再通过 jx 测试页重跑正例、普通负例和语义重叠负例。若改为新建 App，必须重新做 Key 映射和核心服务重建。
- 非解决：只看应用名称、节点标题、旧会话结果或控制台“已发布”标签；只重启 jx 容器；用本地 DSL 通过替代线上 App 节点正文验收。
- 验证：2026-08-16 实际复现“会议真实案例”误入活动分支；核对并修正 `quick_intent` 的 `is_case_request` 优先条件后发布更新，测试页返回咨询案例，正常/取消/延期活动及普通业务负例保持通过，诊断回读含 `message_end.status=completed`。
- 预防：每次 Dify DSL 变更都要同时保存“源文件契约、当前 App 节点正文、发布时间、App Key 映射、`/parameters`、正负向 SSE”六项证据；不得以静态节点名称代替 Code/Prompt 正文核验。
- 证据：`dify/商业顾问（当前使用版本）.yml`、`tests/test_dify_activity_contract.py`、当前状态交接文档 0.61。
- 最后验证：2026-08-16。

## DIFY-020：页面上传后的 Dify Metadata 与 Chatflow 验收必须分开记录

- 适用：咨询会议 v3 通过测试页写入 general 知识库，并在商业顾问 Chatflow 中验证方法、案例和组合问题。
- 触发：Dify 文档详情显示 Metadata 后，直接推断商业顾问每次查询都执行了预期节点；或 Dify 应用已保存但仍调用旧发布版本。
- 根因：Dify 文档索引、Chatflow 发布版本、日志展示和最终回答是不同层级的状态；当前日志详情页可见成功状态和回答，但不提供完整节点执行轨迹。
- 解决：先在文档详情回读 `record_type` 等白名单 Metadata，再确认 Chatflow 已发布，最后分开记录查询是否完成、回答是否出现方法/案例结构以及是否有可见节点级证据。没有节点级证据时只报告答案级验证，不伪造命中信息。
- 非解决：只看画布节点名称、只看“已发布”、只看最终回答中的关键词，或为排查登录问题启动另一个未登录浏览器并要求重新输入凭据。
- 验证：2026-08-19 使用用户已登录 Chrome 完成 18 份样本的 6 批页面验收；31 个成功清洗文档均有 Dify ID，文档详情回读到候选方法 Metadata；Chatflow 发布后 5 个查询均为 `Success`，显式方法+案例问题返回结构化方法与案例。日志详情未暴露节点轨迹，故报告保留该边界。
- 预防：Dify 操作固定复用已登录 Chrome 标签页；发布后重新从项目测试页发起新会话；分别留存 Metadata 回读、发布状态、日志 Success 和答案级结果。不得写入或输出 Cookie、Token、App Key 或配置正文。
- 证据：`var/reports/consulting-methodology-v3/e2e-20260819-redacted.md`、`dify/商业顾问（当前使用版本）.yml`、Dify 文档详情/日志页脱敏记录。
- 最后验证：2026-08-19。

## DIFY-021：realtime/Batch 的最终 Metadata parity 必须逐文档回读，不能只看本地记录

- 适用：Qwen realtime/Provider Batch 清洗结果写入同一 Dify `general` Dataset 后的入库对照验收。
- 触发：本地两种任务的 `CleaningDocument.metadata_json` key set 不同，或小报童/知识星球文档数不同，就直接判定 Dify 入库不一致；反过来只抽查一个远端文档就宣称全量一致。
- 根因：模型拆分数量可以不同；本地来源/审计字段不会全部投影到 Dify；Dify `doc_metadata` 还会附加 `document_name`、`source`、上传时间和上传者等平台字段。因此本地中间态、远端自定义 Metadata 和平台内建字段是三种不同口径。
- 解决：按本地 Dify 文档 ID 对四个策略的 realtime/Batch 结果逐文档调用 `get_document`，核对 `indexing_status=completed`、必需字段存在，并在同一策略内比较远端 metadata key set；将本地可选字段缺失和模型拆分差异单独记录，不把它们混成 Dify metadata 失败。
- 非解决：只看 Dify 控制台“已发布”、只比较候选数/文档数、只抽查一条文档、把平台内建字段当成服务字段，或修改结果以强行得到逐文档数量一致。
- 验证：2026-08-20 四策略全量回读通过：咨询 `100/100`、微信公众号 `101/101`、小报童 Batch/realtime `202/186`、知识星球 `202/192`；所有已关联远端文档索引完成，同一策略远端 metadata key set 一致。完整脱敏证据见 `var/reports/20260820-qwen-activity/full_remote_metadata_parity.safe.json`。
- 预防：Dify 验收报告固定拆分“发布状态”“jx App Key 映射”“本地终态”“远端索引/Metadata 回读”四项；没有全量回读证据时只能写“抽样通过”，不能写“全量一致”。
- 证据：`var/reports/20260820-qwen-activity/full_remote_metadata_parity.safe.json`、`knowledge_cleaner/dify.py::DifyKnowledgeClient.get_document`。
- 最后验证：2026-08-20。

## DIFY-022：Dify 1.13.3 消息回读的用户请求来自顶层 `query`

- 适用：朋友圈顾问、日常写作通过 `GET /messages` 回读上一轮输入并在同一 Dify conversation 中重新生成。
- 触发：普通生成和反馈正常，但两个工具的重新生成都在建立 SSE 前返回 `DIFY_RESPONSE_INVALID`；回读记录的 `inputs` 只有控制字段，没有必填 `request`。
- 根因：Dify 1.13.3 将用户请求保存在消息顶层 `query`，不会保证在 `inputs.request` 中回显；直接用 `inputs` 构造 `MomentsInputs`/`WritingInputs` 会因缺少 `request` 失败。
- 解决：先验证顶层 `query` 为非空字符串，只从远端 `inputs` 提取允许的用户字段，再用顶层 `query` 强制重建 `request`；`type`、写作格式和管理员风格继续由当前本地会话与配置覆盖。
- 非解决：信任远端未知字段或历史控制字段、放宽 Pydantic 必填约束、把消息正文写入本地，或新建 Dify 会话规避回读。
- 验证：真实 Dify 结构诊断确认 `query` 与 `inputs` 分离；`tests/test_tool_conversations_contract.py::test_tool_regenerate_feedback_and_terminal_contract_are_executable` 及三类顾问聚焦回归共 194 项通过。
- 预防：Dify 版本升级或 App 图变化后，先用只输出字段名和类型的脱敏探针核对 `/messages` 结构；契约测试固定覆盖“顶层 query + inputs 不含 request”。
- 证据：`ai_management/api/routers/tools.py`、`tests/test_tool_conversations_contract.py`、`var/reports/advisor-message-actions/diagnose_tool_message_shape.py`。
- 最后验证：2026-09-09。
