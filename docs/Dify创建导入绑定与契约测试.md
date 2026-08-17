# Dify 创建、导入、绑定与契约测试

目标版本：Dify 1.13.3。以下步骤先用隔离、脱敏的 `general` 测试资料跑通；正式资料只按当前清洗策略和 Metadata 契约替换，不能把课程/路线目录当作商业顾问新聊天的数据来源。

## 当前商业顾问生效状态（2026-08-16，优先于全文历史记录）

- jx 最近活动代码已随 `20260816-activity-59d963` release 部署并通过健康、清洗入库和 Metadata 回读；jx 已同步并实际生效新版 App Key，Dify 控制台中的当前绑定 Chatflow 已发布更新。旧应用和发布前配置仍保留，未删除知识库、课程/路线数据或历史聊天。
- 新版 Chatflow 已增加活动意图、动态中国时区时间窗口、正常/取消/延期状态路由、活动 Metadata 检索、结果聚合排序去重和回答节点；普通商业、咨询案例和活动路径互斥。Dify 1.13.3 的动态变量、数值 Metadata 条件和 `doc_metadata` 读取已完成隔离/静态契约验证。
- jx 当前 App Key 已生效：`.env` 与运行中 API 容器的脱敏指纹一致，`health/ready=200`、Dify `/parameters=200`，并已完成测试页真实 SSE 验收。密钥明文不写入报告或文档。
- 线上复核曾发现当前 App 的意图代码落后于仓库 DSL，导致“会议真实案例”误入活动分支；已在当前 App 直接修正 `quick_intent` 并发布更新。发布后正常/取消/延期、普通业务负例和案例重叠负例均通过。
- 仓库的 `scripts/jx_deploy_release.sh` 不安装商业顾问 Dify DSL 或 Prompt；wheel 发布不会自动发布或切换 Dify 应用。每次 Dify 变更必须从 `dify/商业顾问（当前使用版本）.yml` 走受控导入、发布、Key 映射、`/parameters` 和真实 SSE 验收。

## 2026-08-03 执行状态（历史）

- 当前线上商业顾问行为基线仍为 `dify/商业顾问（当前使用版本）.yml`；本轮本地修复完成后，发布必须按“导出备份 → 导入/保存 → 发布 → `/parameters` 与真实 SSE 验收”顺序留存证据，不能只以本地 DSL 通过作为上线结论。
- 远端只读契约检查已验证四个应用的参数/调用路径；两个知识库检索结果为 0，不能据此宣称 RAG 召回通过。候选 DSL 导入/发布需要受控 Dify 管理员会话或 Enterprise API，并应通过 HTTPS/安全隧道完成；缺少这些条件时不得使用 HTTP 管理接口、猜测凭据或直接改数据库。
- 商业顾问轻量语义评测仍有未通过案例；个人商业咨询模型契约和课程目录结构复核也必须保持复核门禁，不能为了提高通过率放宽 schema 或自动批准。

## 当前商业顾问覆盖规则（2026-08-16，优先于下文历史说明）

- 新商业顾问聊天的当前源文件是 `dify/商业顾问（当前使用版本）.yml`：Start 只接收会话摘要、最近对话、用户画像和输出风格；只有一个 `combined_response_general` 主 LLM。
- 课程推荐、学习路线、课程知识库检索、课程混合分支、推荐决策 JSON、格式重试和课程卡片输出均已移除。`quick_route`、`case_route` 只是流程分支 ID，不表示学习路线。
- 保留“用户明确要求相关/类似/真实案例才检索 `consulting_case`”的非课程边界；新增“明确查询活动/会议时才检索 `record_type=event`”的活动边界。普通商业、案例和活动检索互斥，均绑定通用知识库，且不会访问课程库。
- 活动正常分支使用动态 `event_end_ts ≥ start_ts`、`event_start_ts ≤ end_ts`；取消/延期分支只按 `event_status`，延期未定不把旧日期作为新日期。普通分支排除 `consulting_case` 和 `event`。
- 2026-08-16 已完成新版 Dify 导入/发布更新、jx App Key 切换、`/parameters`、健康检查和测试页真实 SSE 验收；“控制台已发布”与“jx 已生效”仍必须分别记录，不能互相替代。

## 本轮 2026-08-10 发布与自动导入补充

- Chrome 已登录 Dify 工作区；导入弹窗使用隐藏 `input[type=file]` 时，自动化不应要求用户手工上传或修改扩展权限。按 `docs/troubleshooting/dify.md` 的 DIFY-013，通过目标标签页级 CDP 临时显示控件，再使用标准 filechooser 自动上传。
- `dify/商业顾问（当前使用版本）.yml` 已导入为隔离应用 `eaa840e6-ec85-47e4-89a5-5cf0678f7ac3` 并发布；旧应用保留作回滚。发布后必须重新创建/核对 App Key，并按 `docs/troubleshooting/deployment.md` DEPLOY-013 备份 `.env`、强制重建读取配置的 jx 核心服务。
- 本次 `/parameters` 与 Dify Chat API 真实调用均返回 `200`；测试 Dataset 没有案例文档，`retriever_resources` 为 `0`，因此这只能证明分支、鉴权和 API 链路可用，不能把空召回宣称为案例 RAG 质量通过。待正式案例文档发布后，必须追加有召回证据的真实 SSE 验收。

## 当前功能修改索引（2026-08-16，优先于历史记录）

以下结论以当前使用的商业顾问 DSL `dify/商业顾问（当前使用版本）.yml`、两个 Workflow DSL 和 jx-now 适配代码为准。

| 功能 | 当前来源 | 修改位置与注意事项 |
| --- | --- | --- |
| 商业顾问打开页面的推荐问题 | Dify App 的 `suggested_questions` 参数，经 `entry.py` 的 `/parameters` 读取 | 推荐问题应聚焦经营咨询或咨询案例，不得引导课程推荐或学习路线。问题文案在 Dify App 参数或 `dify/商业顾问（当前使用版本）.yml` 修改；改本地 DSL 后仍须导入、保存、发布并重新核对 `/parameters`，不能只改本地文件后宣称线上已更新。 |
| 商业顾问回答后的推荐问题 | Dify 的 `suggested_questions_after_answer.enabled` 开关和 Dify 消息上下文 | 当前 DSL 只打开了该开关；jx-now 没有把 Dify 的回答后推荐问题接口转发给小程序/测试页。因此要改变生成逻辑，应在 Dify App 的回答后推荐问题能力中修改并单独验收，不能只改 `suggested_questions`；如要让本项目展示，还需要新增受控 API/SSE 字段，这不在本次计划内。 |
| 商业顾问检索与回答 | `general_knowledge`、`case_knowledge`、活动检索节点、结果聚合、`combined_response_general` | 普通、案例和活动检索都绑定同一 `general` Dataset，三条路径互斥；普通检索排除 `consulting_case`/`event`，案例只取 `consulting_case`，活动只取 `event` 并按动态状态/时间条件过滤；聚合、证据压缩后只调用一个主 LLM。不得恢复课程检索、推荐决策 JSON、课程卡片或课程/路线 SSE。 |
| 朋友圈顾问、日常写作界面引导语 | Dify App 的 `opening_statement` 参数，经 `entry.py` 的 `/parameters` 读取 | `dify/dsl/moments_advisor.yml` 和 `dify/dsl/writing_advisor.yml` 是 Workflow DSL，当前只定义 Start→LLM→End 图，没有可供 `entry-config` 读取的 `opening_statement`；用户看到的引导语应在对应 Dify App 的界面/参数设置中修改并发布。不要把引导语只写进 LLM Prompt，否则不会成为入口配置。 |
| 朋友圈顾问、日常写作输出 | 两个 Workflow DSL 的 LLM Prompt | 朋友圈修改 `dify/dsl/moments_advisor.yml` 的 `llm` 节点；日常写作修改 `dify/dsl/writing_advisor.yml` 的 `llm` 节点。当前 Start→LLM→End 结构已经足够，不需要为界面引导语或商业顾问的分段回答增加节点；修改 DSL 后需重新导入、保存、发布并分别验收。案例目录是 jx 运行时 `AdminSetting` 配置，完整 `request` 作为既有输入传入，不复制到 DSL；只增删改案例时不需要重新发布 Dify。 |

### 商业顾问首屏固定问题配置

当前仓库配置文件为 `dify/商业顾问（当前使用版本）.yml`，位置是 `workflow.features.suggested_questions`。固定问题必须聚焦商业经营诊断、行动建议或明确咨询案例，不能引导课程、课程集合或学习路线。

在 Dify 控制台打开商业顾问 Chatflow 的应用设置，修改开场引导/推荐问题中的 `suggested_questions`，保存并发布。发布后依次核对 Dify `/parameters` 和 jx `/internal/v1/entry-config`；jx 入口配置缓存最长约 300 秒。这个配置只影响首次进入页面的推荐问题，不改变回答后的推荐问题能力。

### 商业顾问“即时提示 + 最终答案”的流式边界

当前商业顾问 DSL 已有 `progress_answer`（固定即时提示）和多个最终 `answer_*` 节点。此前 `ai_management/conversations.py` 只处理 `message` 的 `answer` 字段，把两个节点产生的文本连续写进同一段服务端状态，因此测试页只能在整个流结束后一起看到。

本次只调整 jx-now 适配层和测试页，不修改或重新发布商业顾问 DSL：

1. Dify 进入 `progress_answer` 时发送 `status.code=progress_answer_started`；Dify 1.13.3 的固定 Answer 节点正文实际位于 `node_finished.data.outputs.answer`，适配层将其转成原有 `answer_delta`。
2. Dify 进入最终 `answer_*` 节点时发送 `status.code=final_answer_started`，最终答案继续使用原有 `answer_delta` 流式输出。
3. `answer_delta` 事件名、`data.delta` 字段和 `schema_version` 均不变；旧客户端继续按顺序追加所有 `answer_delta` 即可。需要分栏展示的客户端按两个可选 `status.code` 切换显示区。
4. 即时提示不写入助手历史，只有最终答案写入本地助手消息；测试页已分为“即时提示”和“最终回答”两个占位区。

如果 Dify 版本或导出 DSL 改名了节点，适配层识别的节点规则位于 `ai_management/conversations.py` 顶部的 `PROGRESS_ANSWER_NODE_ID`、`QUICK_ANSWER_NODE_ID` 和 `ANSWER_NODE_PREFIX`。修改节点 ID 后必须补充 SSE 契约测试，并重新执行真实 Chatflow 验收；2026-08-05 已在 jx 发布并用真实合成请求、浏览器测试页验证“即时提示 → 最终答案”顺序。

### 商业顾问 Metadata 与引用边界

- Dify 只接收策略声明的业务 Metadata，外层附加 `cleaning_strategy`、`cleaning_spec_revision`、`content_format`；原文件名、文件/批次 ID、SHA、构建版本、人名映射和审计信息只留本地。
- 普通/案例 DSL 的核心 Metadata 字段是 `record_type`。咨询案例必须回读为 `consulting_case`；活动文档必须回读为 `record_type=event`，并额外具备 `event_status`、`event_start_ts`、`event_end_ts` 等活动字段。
- 活动分支在运行时动态传入 `event_status` 和中国时区时间戳；正常活动使用时间区间相交条件，取消/延期只按状态，结果 Code 从 `metadata.doc_metadata` 读取字段、按开始时间升序去重并限制 3 条。`topic`、`industry`、`business_stage` 等不会自动形成动态过滤。
- `retriever_resources` 仍可能发送通用引用。因此发布前必须确认 `general` 中没有课程/路线资料，且公开标题和正文已通过脱敏边界。

## 1. 建立商业顾问通用知识库

在 Dify 控制台创建：

1. `business-general-test`

两者使用同一种可用中文 Embedding 模型。首版选择高质量索引和普通向量检索；没有已经部署的 Rerank 模型时先不启用。

在知识库 Service API 页面生成一个 API Key。该 Key 可能访问当前账号可见的多个知识库，因此只放在 AI 管理服务环境变量，不下发小程序。

记录商业顾问使用的 Dataset ID：

```text
DIFY_KNOWLEDGE_API_KEY=
DIFY_GENERAL_DATASET_ID=
```

## 2. 先导入测试资料

通用库导入：

```text
test_data/knowledge/通用知识库测试资料.md
```

测试资料通过 AI 管理服务选择 `general` 策略上传。Dify `create-by-text`、索引、父子块和 Metadata 回读均成功后才算入库；不要把课程/路线资料上传到该 Dataset。

## 3. 导入四个应用

逐个从 DSL 文件导入：

| 应用 | 文件 | 类型 |
|---|---|---|
| 商业顾问 | `dify/商业顾问（当前使用版本）.yml` | Chatflow / advanced-chat |
| 朋友圈顾问 | `dify/dsl/moments_advisor.yml` | Workflow |
| 日常写作 | `dify/dsl/writing_advisor.yml` | Workflow |
| 摘要画像 | `dify/dsl/summary_profile.yml` | Workflow |

DSL 记录 Tongyi 插件和 `qwen-plus`。如果目标环境插件版本不同：

1. 在控制台安装目标环境可用的官方 Tongyi 插件。
2. 打开每个 LLM 节点重新选择 `qwen-plus` 和模型凭据。
3. 保存并重新导出目标环境 DSL。

DSL 不包含模型 API Key。

### 3.1 PDF→Markdown Dify Workflow（已退役）

独立的 `pdf_to_markdown_code_test.yml` Workflow 和 `sandbox-pdf-probe` 构建材料已按 2026-08-05 决定全部退役，不再作为当前导入、发布或验收入口。PDF 转 Markdown 统一由 jx-now 的 `knowledge_cleaner.pdf_markdown_refine` 共享核心和现有 `ai_ocr` 上传清洗队列负责；不会再通过 Dify Code 节点复制一套转换逻辑。

远端已从 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre` 恢复 Sandbox、SSRF、超时和环境基线，并删除临时 override；退役前快照位于 `/opt/ai-management/backups/20260805-pdf-workflow-retire-pre`。历史验证和踩坑证据保留在日期化报告，不代表当前生产配置。

### 3.2 PDF Workflow 已验证踩坑清单（2026-08-04）

| 现象 | 已验证根因 | 解决与预防 |
| --- | --- | --- |
| 文本型 PDF 报“超过 max_pages” | 旧逻辑在识别文字层前统一检查页数 | 先探测前两页文字层；文本型 PDF 走 PyMuPDF，不套 OCR 页数限制；`max_pages` 只限制纯图片 PDF。 |
| 扫描 PDF 报 `bad system call` | Sandbox 默认 seccomp 缺少 ONNX Runtime 所需 syscall | 测试窗口使用隔离依赖镜像和临时 syscall 配置；保留备份，不能直接作为生产配置。 |
| RapidOCR 日志导致 Code 节点 RuntimeError | RapidOCR/ONNX Runtime 将 INFO/WARNING 写入 stderr，Sandbox 把非空 stderr 当错误 | 关闭 RapidOCR Python logger、提高 ONNX 日志级别，并用内存管道 drain 原生 fd 2。 |
| `/dev/null` 或临时文件方案继续失败 | 受限 Sandbox 没有 `/dev/null`，也没有可用临时目录 | 不依赖文件系统 sink；固定使用 `os.pipe()`，并在 `finally` 恢复 fd 2、关闭管道。 |
| 本地 YAML 已修复但 Dify 仍报旧错误 | Dify 已导入的应用不会自动读取本地文件 | 重新导入 YAML，保存并发布新 Workflow 版本后再测。 |

上述 Dify Workflow 验收结果仅作历史记录；当前 PDF 验收请使用本地全量样本、jx 代表样本、上传清洗状态和 PDF 问题中心接口。

## 4. 绑定商业顾问知识库

打开商业顾问画布：

- `general_knowledge` 与 `case_knowledge` 都绑定 `business-general-test`。
- 前者使用 `record_type is not consulting_case`；后者使用 `record_type is consulting_case`。两个分支互斥。
- 确认唯一主 LLM `combined_response_general` 没有开启 Memory。
- 开启引用与归属，让 API 的 `retriever_resources` 有内容。
- 保存并发布。

重要：`.env` 中 Dataset ID 只供 Service API 使用，不会自动改变 Chatflow 原生知识检索节点。更换知识库时必须同时改环境变量、重新绑定画布并发布。不得绑定课程 Dataset、添加课程 Metadata 过滤或恢复课程/混合检索节点。

## 5. 创建四个 App Key

每个应用在 API Access 页面创建独立 Key：

```text
DIFY_BUSINESS_APP_API_KEY=
DIFY_MOMENTS_APP_API_KEY=
DIFY_WRITING_APP_API_KEY=
DIFY_SUMMARY_PROFILE_APP_API_KEY=
```

不要复用旧 `.env` 中通用的 `DIFY_API_KEY`。已暴露或曾用于临时调试的 Key 应在 Dify 中撤销后重建。

`app-...` 是应用访问 Key，不是应用 UUID，也不能写入交接文档、聊天记录或小程序代码。每次重新创建应用、导入 DSL、重新发布或轮换 Key 后，必须按受控流程核对 AI 管理服务的对应环境变量，并逐一验证三个用户入口的 `/parameters`；只有映射变化时才同步配置并重建 API、Worker、Scheduler。Dify 控制台的发布动作不会替 AI 管理服务完成该项核对。

### 5.1 含 Code 节点的环境预检

商业顾问含 Dify Code 节点。由 Dify 运维人员在不显示原值的前提下，比较 Dify API 的 `CODE_EXECUTION_API_KEY` 与 Sandbox 的 `SANDBOX_API_KEY` 的哈希；二者必须一致。若 Code 节点返回 401：先备份 Dify `.env`，使两个值一致后只重建 Sandbox，再用最小输入复测 Code 节点。不得通过删除 Dify 数据、知识库、卷或重启整套 Dify 来掩盖问题。

## 6. 最小真实调用

先启动 AI 管理服务，再用内部接口调用，避免小程序直接接触 Dify Key：

```powershell
$headers = @{
  "X-Internal-Token" = "<内部Token>"
  "X-User-Id" = "contract-user-001"
}
$body = @{
  query = "我的获客转化下降，先应如何诊断？"
} | ConvertTo-Json
Invoke-WebRequest `
  -Method Post `
  -Uri "http://127.0.0.1:8000/internal/v1/business/chat" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

确认能看到 `request_started`、`status`、`answer_delta`、可选引用和 `message_end`，且不会出现课程/路线/课程卡片事件。至少分别验证一个普通商业问题和一个明确索要咨询案例的问题；每次均须有最终 `message_end`，Dify 运行记录不得出现未定义变量。两个互斥分支中，后续节点只能引用实际执行分支的检索输出。

## 7. 动态 inputs 契约

这是开发门禁，不凭源码推断直接通过：

1. 新建本地商业会话，首轮提供一个可辨识约束，例如“我的预算上限是A”。
2. 使用同一逻辑会话再次调用，让服务在 `conversation_summary` 或 `recent_history_text` 中提供新的可辨识值B。
3. 在 Dify 运行日志检查 Start 节点实际 inputs 是B，不是首轮A。
4. 构造相互冲突的当前轮、最近未摘要历史、会话摘要和用户画像值，逐级移除高优先级值，验证服务实际传入的 inputs 与输出严格遵循“当前轮明确表达 → 最近未摘要历史明确确认 → 会话摘要确认 → 用户画像”。
5. 在隔离应用至少完成普通商业问题、明确咨询案例问题和无课程事件断言三轮真实 SSE：普通分支不得取 `consulting_case`，案例分支不得取非案例资料；所有流都不得出现 `learning_route`、`course_cards` 或 `course_recommendation_note`。
6. 再跨自然日或临时修改测试日期，确认服务创建新的 Dify 会话段但本地仍可续聊。

若普通 inputs 在目标部署确实没有更新，再实现会话变量查询/更新备用路径；不要为未复现问题增加双套逻辑。

## 8. 必测 API

商业 Chatflow：

- 流式回答。
- 任务停止。
- 点赞、点踩和原因。
- 删除 Dify 会话段。
- node_finished 中可以取得推荐决策 JSON。

Workflow：

- 朋友圈输出 `text`。
- 写作输出 `text`。
- 摘要画像输出 `result` 且是严格 JSON。
- Workflow 停止。

知识库：

- create-by-text。
- 父子分段。
- 索引状态 completed/error。
- 零分块判定。

知识库文档清洗发布的额外契约：上传策略先按支持矩阵完成 PDF/DOCX→Markdown 或 Markdown/TXT 规范化，再执行表格清洗；模型、人工复核和文章拆分在 `create-by-text` 前都必须执行最终无 Markdown/HTML 表格门禁。单个源文件中多篇公众号文章/小报童帖子必须先拆成多个逻辑条目，每个逻辑条目独立调用一次 `create-by-text`、等待索引并回读父子块和 Metadata；不能把多个条目合并成一个远端文档。Dify 管理名保留逻辑条目标题语义并统一为 `.md`，同源多文档使用 `__001.md`、`__002.md` 等序号；多个上传文件通过本地发布队列逐条调用，不超过单条请求的数量和字节上限。内部来源文件名、源哈希、批次 ID 不得写入 Dify 自定义 Metadata。
- Metadata 创建和设置。
- 文档删除。
- 中文和超长名称。

## 9. 商业顾问知识库边界

当前商业顾问只绑定一个 `general` Dataset。普通商业知识与咨询案例通过静态 `record_type` 过滤隔离；它们不是两个知识库。

课程/路线数据即使仍因管理兼容而存在，也不属于本应用的 Dataset、检索节点、SSE 或验收目标。不能为了“提高课程召回”把它们放进 `general`，因为普通分支可能召回 `record_type` 不为 `consulting_case` 的污染资料。
