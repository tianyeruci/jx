# Dify 创建、导入、绑定与契约测试

目标版本：Dify 1.13.3。以下步骤先用测试资料跑通，老板正式资料到位后只替换知识库内容和稳定课程数据。

## 当前商业顾问生效状态（2026-08-14，优先于全文历史记录）

- 当前线上商业顾问已切换到本次新建并发布的隔离 Chatflow；旧应用仍保留，可与发布前 jx 配置快照配合回滚。本次没有删除 Dify 数据集、课程/路线数据或旧应用。
- 当前应用的 Start 输入实测只有 `conversation_summary`、`recent_history_text`、`profile_json`、`business_output_style`；Dify 1.13.3 `/parameters` 和 jx `health/ready` 均通过。
- 工作流保留固定直答、即时提示、一次主 LLM 和“明确案例请求才检索 `consulting_case`”的通用知识库边界；没有课程库、学习路线、课程卡片或推荐决策 JSON 分支。`quick_route`、`case_route` 只是流程节点 ID。
- 发布前控制台发现 `variable-aggregator` 后继节点引用了不存在的 `result` 输出；Dify 1.13.3 的实际输出名为 `output`。已修正两个受控 DSL、增加回归断言并重新导入发布；不得以跳过控制台校验的方式绕过这类错误。
- jx 真实商业顾问 SSE 已以 `message_end.status=completed` 结束，且未出现课程推荐、课程卡片或学习路线事件；测试会话已精确删除。通用 `retriever_resources` 仍保留，故通用知识库不得混入课程资料。
- 仓库的 `scripts/jx_deploy_release.sh` 不再安装任何商业顾问 Dify DSL 或 Prompt；wheel 发布不会发布 Dify 应用。该脚本保护将在下一次完整 release 同步并复核；后续变更必须从 `dify/商业顾问（当前使用版本）.yml` 走受控 Dify 导入、发布、`/parameters` 和真实 SSE 验收，不能把服务器上的历史 DSL 或 Prompt 副本当作当前源。

## 2026-08-03 执行状态（历史）

- 当前线上商业顾问行为基线仍为 `dify/商业顾问（当前使用版本）.yml`；本轮本地修复完成后，发布必须按“导出备份 → 导入/保存 → 发布 → `/parameters` 与真实 SSE 验收”顺序留存证据，不能只以本地 DSL 通过作为上线结论。
- 远端只读契约检查已验证四个应用的参数/调用路径；两个知识库检索结果为 0，不能据此宣称 RAG 召回通过。候选 DSL 导入/发布需要受控 Dify 管理员会话或 Enterprise API，并应通过 HTTPS/安全隧道完成；缺少这些条件时不得使用 HTTP 管理接口、猜测凭据或直接改数据库。
- 商业顾问轻量语义评测仍有未通过案例；个人商业咨询模型契约和课程目录结构复核也必须保持复核门禁，不能为了提高通过率放宽 schema 或自动批准。

## 当前商业顾问覆盖规则（2026-08-14，优先于下文历史说明）

- 新商业顾问聊天的当前源文件是 `dify/商业顾问（当前使用版本）.yml`：Start 只接收会话摘要、最近对话、用户画像和输出风格；只有一个 `combined_response_general` 主 LLM。
- 课程推荐、学习路线、课程知识库检索、课程混合分支、推荐决策 JSON、格式重试和课程卡片输出均已移除。`quick_route`、`case_route` 只是流程分支 ID，不表示学习路线。
- 保留“用户明确要求相关/类似/真实案例才检索 `consulting_case`”的非课程边界；普通商业检索与案例检索互斥，均绑定通用知识库，且不会访问课程库。
- 2026-08-14 已完成 Dify 新应用导入和发布、jx App Key 切换、`/parameters` 与真实 SSE 验收；本节其余“待发布”描述均为历史过程，不再代表当前线上行为。

## 本轮 2026-08-10 发布与自动导入补充

- Chrome 已登录 Dify 工作区；导入弹窗使用隐藏 `input[type=file]` 时，自动化不应要求用户手工上传或修改扩展权限。按 `docs/troubleshooting/dify.md` 的 DIFY-013，通过目标标签页级 CDP 临时显示控件，再使用标准 filechooser 自动上传。
- `dify/商业顾问（当前使用版本）.yml` 已导入为隔离应用 `eaa840e6-ec85-47e4-89a5-5cf0678f7ac3` 并发布；旧应用保留作回滚。发布后必须重新创建/核对 App Key，并按 `docs/troubleshooting/deployment.md` DEPLOY-013 备份 `.env`、强制重建读取配置的 jx 核心服务。
- 本次 `/parameters` 与 Dify Chat API 真实调用均返回 `200`；测试 Dataset 没有案例文档，`retriever_resources` 为 `0`，因此这只能证明分支、鉴权和 API 链路可用，不能把空召回宣称为案例 RAG 质量通过。待正式案例文档发布后，必须追加有召回证据的真实 SSE 验收。

## 当前功能修改索引（2026-08-05）

以下结论以当前使用的商业顾问 DSL `dify/商业顾问（当前使用版本）.yml`、两个 Workflow DSL 和 jx-now 适配代码为准。

| 功能 | 当前来源 | 修改位置与注意事项 |
| --- | --- | --- |
| 商业顾问打开页面的推荐问题 | Dify App 的 `suggested_questions` 参数，经 `entry.py` 的 `/parameters` 读取 | 当前固定为 3 条 AI 课程相关问题；入口接口按 Dify 当前已发布参数返回推荐问题，并缓存 300 秒。问题文案应在 Dify App 参数或 `dify/商业顾问（当前使用版本）.yml` 中修改，修改本地 DSL 后仍需保存、发布并重新核对 `/parameters`，不能只改本地文件后宣称线上已更新。 |
| 商业顾问回答后的推荐问题 | Dify 的 `suggested_questions_after_answer.enabled` 开关和 Dify 消息上下文 | 当前 DSL 只打开了该开关；jx-now 没有把 Dify 的回答后推荐问题接口转发给小程序/测试页。因此要改变生成逻辑，应在 Dify App 的回答后推荐问题能力中修改并单独验收，不能只改 `suggested_questions`；如要让本项目展示，还需要新增受控 API/SSE 字段，这不在本次计划内。 |
| 商业顾问专家见解 | DSL 的 `combined_response_general/course/mixed` 与格式修复分支输出 `expert_insight`、`expert_course_ids`，并由 `recommendation_decision_*` 解析；服务端 `ai_management/courses.py` 的 `validate_recommendation()` 与 `ai_management/conversations.py` | DSL 不生成用户可见推荐理由。服务端只在已校验课程卡片存在且全部卡片完成后发送一条 `course_recommendation_note` 专家见解；模型见解须通过 `expert_course_ids` 全量一致、链接和内部 ID 门禁，不合规时由服务端生成同长度约束的安全默认见解。无卡、待路线/阶段确认或解析失败不发送。事件名及 `{"content":"..."}` 结构不变；路线完整阶段、`course_names` 与 `course_collection_names` 由服务端补充；修改 DSL 后必须导入、保存、发布并执行真实 SSE 验收。 |
| 朋友圈顾问、日常写作界面引导语 | Dify App 的 `opening_statement` 参数，经 `entry.py` 的 `/parameters` 读取 | `dify/dsl/moments_advisor.yml` 和 `dify/dsl/writing_advisor.yml` 是 Workflow DSL，当前只定义 Start→LLM→End 图，没有可供 `entry-config` 读取的 `opening_statement`；用户看到的引导语应在对应 Dify App 的界面/参数设置中修改并发布。不要把引导语只写进 LLM Prompt，否则不会成为入口配置。 |
| 朋友圈顾问、日常写作输出 | 两个 Workflow DSL 的 LLM Prompt | 朋友圈修改 `dify/dsl/moments_advisor.yml` 的 `llm` 节点；日常写作修改 `dify/dsl/writing_advisor.yml` 的 `llm` 节点。当前 Start→LLM→End 结构已经足够，不需要为界面引导语或商业顾问的分段回答增加节点；修改后需重新导入、保存、发布并分别验收。 |

### 商业顾问首屏固定问题配置

当前仓库配置文件为 `dify/商业顾问（当前使用版本）.yml`，位置是 `workflow.features.suggested_questions`。固定问题聚焦 `AI高手提效课`，当前为：

1. `我想系统学习AI工具，应该从哪里开始？`
2. `有没有适合公众号运营入门的课程？`
3. `我已经有一些基础，如何选择AI写作进阶课程？`

在 Dify 控制台打开商业顾问 Chatflow 的应用设置，修改开场引导/推荐问题中的 `suggested_questions`，保存并发布。发布后依次核对 Dify `/parameters` 和 jx `/internal/v1/entry-config`；jx 入口配置缓存最长约 300 秒。这个配置只影响首次进入页面的推荐问题，不改变回答后的推荐问题能力。

### 商业顾问“即时提示 + 最终答案”的流式边界

当前商业顾问 DSL 已有 `progress_answer`（固定即时提示）和多个最终 `answer_*` 节点。此前 `ai_management/conversations.py` 只处理 `message` 的 `answer` 字段，把两个节点产生的文本连续写进同一段服务端状态，因此测试页只能在整个流结束后一起看到。

本次只调整 jx-now 适配层和测试页，不修改或重新发布商业顾问 DSL：

1. Dify 进入 `progress_answer` 时发送 `status.code=progress_answer_started`；Dify 1.13.3 的固定 Answer 节点正文实际位于 `node_finished.data.outputs.answer`，适配层将其转成原有 `answer_delta`。
2. Dify 进入最终 `answer_*` 节点时发送 `status.code=final_answer_started`，最终答案继续使用原有 `answer_delta` 流式输出。
3. `answer_delta` 事件名、`data.delta` 字段和 `schema_version` 均不变；旧客户端继续按顺序追加所有 `answer_delta` 即可。需要分栏展示的客户端按两个可选 `status.code` 切换显示区。
4. 即时提示不写入助手历史，只有最终答案写入本地助手消息；测试页已分为“即时提示”和“最终回答”两个占位区。

如果 Dify 版本或导出 DSL 改名了节点，适配层识别的节点规则位于 `ai_management/conversations.py` 顶部的 `PROGRESS_ANSWER_NODE_ID`、`QUICK_ANSWER_NODE_ID` 和 `ANSWER_NODE_PREFIX`。修改节点 ID 后必须补充 SSE 契约测试，并重新执行真实 Chatflow 验收；2026-08-05 已在 jx 发布并用真实合成请求、浏览器测试页验证“即时提示 → 最终答案”顺序。

### 商业顾问课程/路线名称与内部标识边界

Chatflow 的决策 JSON 可以继续使用 `course_id`、`route_id`、`stage_id` 做候选、校验和进度动作；这些字段不能为了改善文案而从决策结果删除。`ai-management` 在用户可见文本出口读取已审核课程目录快照，把课程、路线、阶段 ID 转成对应正式名称，并清理英文标签、裸标识和旧的“内部标识已隐藏”占位词。

- 用户看到的是目录正式名称；错误名称与有效 ID 同时出现时，以目录名称为准。
- 路线快照按“新手必看 → 入门阶段 → 高手进阶”排序，保留现有阶段 ID，避免影响已确认进度。
- 路线列表可展示正式课程名称供用户判断，但路线或阶段待确认时不得将其表述为已推荐课程；普通推荐的课程卡片仍由 `courses.py` 按已校验 ID 生成签名 `redirect_url`，结构化 `course_cards`、`learning_route`、停止和反馈字段保持兼容。
- ID 无效或课程已下架时不生成课程卡片，用户文案使用不可用提示；不能根据 ID 猜测课程名称。
- 处理范围覆盖流式回答、历史正文、标题、推荐说明、专家建议和引用资源中的可见字符串；引用资源的 `course_id`/`route_id`/`stage_id` 元数据字段保持结构但不作为用户文案展示。
- 路线确认沿用现有机器字段和接口，不新增字段；路线初问和“我确认学习路线：路线名称”后的阶段确认状态均不发送 `course_cards`，用户明确确认阶段后才生成该路线已确认当前阶段的全部有效课程卡片，不得带入下一阶段。明确课程、课程集或最新课程查询可走独立课程路径且最多 5 门；多个可信路线均可能匹配时只澄清，不回退独立课程；确实没有官方路线证据时先说明未匹配，再转为独立课程路径。
- 用户自身状态、意图、选择和学习进度按“当前轮明确表达 → 最近未摘要历史中的明确确认 → 会话摘要中的已确认内容 → 路线进度 JSON → 用户画像 JSON”取值。新路线选择不取消旧路线进度；拒绝、换一条或含糊答复不写持久拒绝状态。

这部分保持现有接口字段，但改变了公开课程卡片的出现条件；修改后必须同步接口契约、Prompt、目录/DSL 契约测试，并在隔离 Dify 应用完成“路线初问 → 路线确认 → 阶段确认 → 课程卡片”的真实 SSE 验收。

## 1. 建两个知识库

在 Dify 控制台创建：

1. `business-general-test`
2. `business-course-test`

两者使用同一种可用中文 Embedding 模型。首版选择高质量索引和普通向量检索；没有已经部署的 Rerank 模型时先不启用。

在知识库 Service API 页面生成一个 API Key。该 Key 可能访问当前账号可见的多个知识库，因此只放在 AI 管理服务环境变量，不下发小程序。

记录两个 Dataset ID：

```text
DIFY_KNOWLEDGE_API_KEY=
DIFY_GENERAL_DATASET_ID=
DIFY_COURSE_DATASET_ID=
```

## 2. 先导入测试资料

通用库导入：

```text
test_data/knowledge/通用知识库测试资料.md
```

课程库不要直接手工上传原 Markdown作为最终流程。先配置 API Key 和 Dataset ID，再运行：

```powershell
ai-course-import test_data/courses/测试课程与路线.md --dry-run
ai-course-import test_data/courses/测试课程与路线.md --publish
```

CLI 使用 create-by-text，等待索引完成和非零分块，设置 Metadata，然后写本地课程事实和 Redis 快照。

老板模板本身没有稳定 ID。首次测试或首次正式整理时可以省略 `course_id`、`route_id` 和 `stage_id`，导入器会根据方向与名称生成确定性 ID，路线中的 `course_ids` 也可以暂时填写唯一课程名称。dry-run 输出生成结果后，应把这些 ID 回填到后续维护的正式 Markdown；课程改名时仍沿用原 ID，不能依赖再次生成。

## 3. 导入四个应用

逐个从 DSL 文件导入：

| 应用 | 文件 | 类型 |
|---|---|---|
| 商业顾问 | `dify/dsl/business_advisor.yml` | Chatflow / advanced-chat |
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

- “通用知识检索”绑定 `business-general-test`。
- “混合问题通用知识检索”也绑定 `business-general-test`。
- “课程路线检索”绑定 `business-course-test`。
- 确认两个 LLM 节点没有开启 Memory。
- 开启引用与归属，让 API 的 `retriever_resources` 有内容。
- 保存并发布。

重要：`.env` 中 Dataset ID 只供 Service API 使用，不会自动改变 Chatflow 原生知识检索节点。更换知识库时必须同时改环境变量、重新绑定画布并发布。商业顾问含“通用 / 课程 / 混合”三条路径，三个检索节点都必须按上表完成绑定。

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
  query = "AI工具入门课程主要解决什么问题？"
} | ConvertTo-Json
Invoke-WebRequest `
  -Method Post `
  -Uri "http://127.0.0.1:8000/internal/v1/business/chat" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

确认能看到 `request_started`、`answer_delta`、引用、课程卡片或 `message_end`。商业顾问必须分别用通用问题、课程问题和混合问题各测一次；每次都要有最终 `message_end`，且 Dify 运行记录中不能出现未定义变量。分类器未执行的分支不会生成空变量，因此决策和回答节点只能引用当前分支已经执行的检索节点输出。

## 7. 动态 inputs 契约

这是开发门禁，不凭源码推断直接通过：

1. 新建本地商业会话，首轮提供一个可辨识约束，例如“我的预算上限是A”。
2. 使用同一逻辑会话再次调用，让服务在 `conversation_summary` 或 `recent_history_text` 中提供新的可辨识值B。
3. 在 Dify 运行日志检查 Start 节点实际 inputs 是B，不是首轮A。
4. 构造相互冲突的当前轮、最近未摘要历史、会话摘要、路线进度和用户画像值，逐级移除高优先级值，验证服务实际传入的 inputs 与输出决策严格遵循“当前轮明确表达 → 最近未摘要历史明确确认 → 会话摘要确认 → 路线进度 JSON → 用户画像”。
5. 在隔离应用做至少三轮真实 SSE：路线初问的路线结构返回 `learning_route`（可展示阶段、课程名称）但不发送 `course_cards`；确认路线后仍不发送；明确确认阶段后才收到当前阶段全部有效课程（构造超过 5 门），不得出现下一阶段卡片，随后只收到一条 `course_recommendation_note` 专家见解。另测多路线候选只澄清、不回退独立课程，以及新路线选择不取消旧路线、拒绝/换一条/含糊答复不写持久拒绝状态。
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

## 9. 两个知识库是否足够

当前场景使用两个最合适：

- 通用库内容规模和更新节奏较杂，回答一般商业问题。
- 课程库是审核后的受控事实，承担课程 ID、路线和阶段检索。

分开可以为课程设置更高召回和更严格的最终校验，也避免普通文章中的课程措辞被误当成正式目录。首版无需再按公众号、案例或 SOP 拆更多库；用 Metadata 区分即可。
