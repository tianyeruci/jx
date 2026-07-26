# Dify 创建、导入、绑定与契约测试

目标版本：Dify 1.13.3。以下步骤先用测试资料跑通，老板正式资料到位后只替换知识库内容和稳定课程数据。

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

## 4. 绑定商业顾问知识库

打开商业顾问画布：

- “通用知识检索”绑定 `business-general-test`。
- “课程路线检索”绑定 `business-course-test`。
- 确认两个 LLM 节点没有开启 Memory。
- 开启引用与归属，让 API 的 `retriever_resources` 有内容。
- 保存并发布。

重要：`.env` 中 Dataset ID 只供 Service API 使用，不会自动改变 Chatflow 原生知识检索节点。更换知识库时必须同时改环境变量、重新绑定画布并发布。

## 5. 创建四个 App Key

每个应用在 API Access 页面创建独立 Key：

```text
DIFY_BUSINESS_APP_API_KEY=
DIFY_MOMENTS_APP_API_KEY=
DIFY_WRITING_APP_API_KEY=
DIFY_SUMMARY_PROFILE_APP_API_KEY=
```

不要复用旧 `.env` 中通用的 `DIFY_API_KEY`。已暴露或曾用于临时调试的 Key 应在 Dify 中撤销后重建。

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

确认能看到 `request_started`、`answer_delta`、引用、课程卡片或 `message_end`。

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
