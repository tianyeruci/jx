# Dify 创建、导入、绑定与契约测试

目标版本：Dify 1.13.3。以下步骤先用测试资料跑通，老板正式资料到位后只替换知识库内容和稳定课程数据。

## 当前执行状态（2026-08-03）

- 当前线上商业顾问行为基线仍为 `dify/商业顾问（当前使用版本）.yml`；候选“统一修复版”只完成本地生成与静态契约检查，未导入或发布到正式应用。
- 远端只读契约检查已验证四个应用的参数/调用路径；两个知识库检索结果为 0，不能据此宣称 RAG 召回通过。候选 DSL 导入/发布需要受控 Dify 管理员会话或 Enterprise API，并应通过 HTTPS/安全隧道完成；缺少这些条件时不得使用 HTTP 管理接口、猜测凭据或直接改数据库。
- 商业顾问轻量语义评测仍有未通过案例；个人商业咨询模型契约和课程目录结构复核也必须保持复核门禁，不能为了提高通过率放宽 schema 或自动批准。

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

### 3.1 独立 PDF→Markdown Code 节点测试 Workflow

用于验证 PDF 转 Markdown 的独立 DSL 为 `dify/dsl/pdf_to_markdown_code_test.yml`。它不是四个业务应用的一部分，不绑定知识库，也不要求修改 jx API/Worker。

导入和使用：

1. 在 Dify 控制台以管理员身份导入该 YAML，作为新的 `workflow` 应用；不要覆盖四个现有应用。
2. 上传一个纯文本或纯图片 PDF；`OCR DPI` 默认 200（允许 150–300），`最大页数`用于纯图片 PDF，默认 50（允许 1–200）。文本型 PDF 先走 PyMuPDF，不受该 OCR 页数限制。
3. 运行后查看 `status`、`backend` 和 `warnings`；文字 PDF 预期 `backend=pymupdf`，无文字层的扫描 PDF 预期 `backend=rapidocr`，Markdown 在 `markdown` 字段。
4. 先用合成/脱敏样本各跑一遍，再核对页数、输出长度和置信度；不要把样本正文复制到日志、报告或仓库。RapidOCR/ONNX Runtime 的 INFO/WARNING 可能写 stderr，测试 DSL 已在 OCR 调用期间处理该输出，避免被 Sandbox 误判为 RuntimeError。

如果此前已经导入旧版本，必须重新导入本地修复后的 `pdf_to_markdown_code_test.yml` 并保存/发布新 Workflow 版本；Dify 不会自动同步本地 YAML。旧版本会继续出现“文本 PDF 超过 max_pages”或 RapidOCR 日志导致节点异常。

本次服务器验证对 Dify Sandbox 做了临时覆盖：离线安装 PyMuPDF/RapidOCR/ONNX Runtime，扩大 syscall 白名单并将 Code 执行超时调到 180 秒；当前按用户要求保留该自测窗口，SSRF 仅放行 `112.74.37.111:80`。窗口结束后必须从 `/opt/ai-management/backups/20260804-pdf-code-sandbox-pre` 恢复并按哈希确认。默认 Dify Sandbox 的 seccomp 会阻断 ONNX Runtime，不能把“镜像能 import”误当成“Workflow 可运行”。

该 DSL 仍只是独立测试资产，不绑定正式知识库，也不替换生产清洗策略；导入后的 UI 运行结果以 Dify 追踪日志和 `status/backend/warnings` 为准。

### 3.2 PDF Workflow 已验证踩坑清单（2026-08-04）

| 现象 | 已验证根因 | 解决与预防 |
| --- | --- | --- |
| 文本型 PDF 报“超过 max_pages” | 旧逻辑在识别文字层前统一检查页数 | 先探测前两页文字层；文本型 PDF 走 PyMuPDF，不套 OCR 页数限制；`max_pages` 只限制纯图片 PDF。 |
| 扫描 PDF 报 `bad system call` | Sandbox 默认 seccomp 缺少 ONNX Runtime 所需 syscall | 测试窗口使用隔离依赖镜像和临时 syscall 配置；保留备份，不能直接作为生产配置。 |
| RapidOCR 日志导致 Code 节点 RuntimeError | RapidOCR/ONNX Runtime 将 INFO/WARNING 写入 stderr，Sandbox 把非空 stderr 当错误 | 关闭 RapidOCR Python logger、提高 ONNX 日志级别，并用内存管道 drain 原生 fd 2。 |
| `/dev/null` 或临时文件方案继续失败 | 受限 Sandbox 没有 `/dev/null`，也没有可用临时目录 | 不依赖文件系统 sink；固定使用 `os.pipe()`，并在 `finally` 恢复 fd 2、关闭管道。 |
| 本地 YAML 已修复但 Dify 仍报旧错误 | Dify 已导入的应用不会自动读取本地文件 | 重新导入 YAML，保存并发布新 Workflow 版本后再测。 |

最终扫描样本验收：`code=0`、`error` 为空、`status=succeeded`、`backend=rapidocr`、3 页、平均置信度 `0.9969`。报告中不保存样本正文。

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
4. 再跨自然日或临时修改测试日期，确认服务创建新的 Dify 会话段但本地仍可续聊。

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
- Metadata 创建和设置。
- 文档删除。
- 中文和超长名称。

## 9. 两个知识库是否足够

当前场景使用两个最合适：

- 通用库内容规模和更新节奏较杂，回答一般商业问题。
- 课程库是审核后的受控事实，承担课程 ID、路线和阶段检索。

分开可以为课程设置更高召回和更严格的最终校验，也避免普通文章中的课程措辞被误当成正式目录。首版无需再按公众号、案例或 SOP 拆更多库；用 Metadata 区分即可。
