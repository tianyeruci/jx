# API 与 SSE 说明

> 当前生效（2026-08-14，ADR-036）：商业顾问的新聊天不再推荐课程或学习路线。请求路径和字段不变，`entry-config` 中 `business_advisor.course_recommendation=false`；新 SSE 仅可能发送 `request_started`、`status`、`answer_delta`、`retriever_resources`、`message_end`、`error`。`learning_route`、`course_cards`、`course_recommendation_note` 不再从新聊天发送；下文旧课程事件细节仅用于历史消息和兼容接口说明。

除课程签名跳转和健康检查外，内部接口均要求：

```http
X-Internal-Token: <INTERNAL_API_TOKEN>
```

用户入口还要求：

```http
X-User-Id: <小程序后端的稳定用户ID>
```

不要把小程序登录 Token 或 Dify `conversation_id` 传给本服务。

## 商业顾问

```http
POST   /internal/v1/business/chat
POST   /internal/v1/business/chat/{task_id}/stop
GET    /internal/v1/business/conversations
GET    /internal/v1/business/conversations/{conversation_id}/messages
PATCH  /internal/v1/business/conversations/{conversation_id}
DELETE /internal/v1/business/conversations/{conversation_id}
POST   /internal/v1/business/messages/{message_id}/feedback
```

聊天请求：

```json
{
  "query": "我想从零学习AI工具，应该怎么开始？",
  "conversation_id": null
}
```

不传 `conversation_id` 创建新会话；续聊时只能传本服务返回的本地 ID。

## SSE

响应类型为 `text/event-stream`。客户端必须按事件边界解析，不能假设一次网络分片就是一个完整 JSON。

```text
request_started
status
answer_delta
retriever_resources
message_end
error
```

`answer_delta`：

```json
{"delta":"本次新增文本"}
```

商业顾问的 `answer_delta` 可以在同一条 SSE 流中出现多次。当前 DSL 的 `progress_answer` 即时提示和最终 `answer_*` 节点都复用该事件，不新增事件名或字段。为支持需要分栏展示的页面，服务端在两个阶段之间复用现有 `status` 事件发送可选边界码：

```json
{"code":"progress_answer_started","message":"已开始返回即时提示"}
{"code":"final_answer_started","message":"已开始返回最终答案"}
```

收到 `progress_answer_started` 后的 `answer_delta` 属于即时提示；收到 `final_answer_started` 后的 `answer_delta` 属于最终答案。旧客户端可以继续按顺序追加所有 `answer_delta`，无需修改接口字段。当前测试页使用两个展示区，并且只把最终答案写入助手历史；即时提示不是持久化回答。

客户端不得等待或渲染课程卡片、学习路线或课程推荐说明事件；这些事件名仅保留在历史文档和旧数据结构中，不属于新聊天的输出集合。

在 Dify 1.13.3 中，固定 `progress_answer` 节点可能只在 `node_finished.data.outputs.answer` 返回正文，不发送 `message.answer`；AI 管理服务会在适配层把该输出规范化为一个或多个 `answer_delta`，调用方无需感知 Dify 原始事件差异。

服务端会在用户可见文本出口过滤受保护来源与内部标识；新聊天没有课程/路线决策字段、课程卡片或路线进度结构。历史正文中的旧课程字段仅容错读取，不能用于恢复聊天行为。

`message_end` 包含本地会话/消息 ID、Dify message/task ID、状态、首 Token、总耗时和 Token 用量。若流中收到 `error`，不能把此前部分文本标记为完整回答。

同一会话已有生成时返回：

```http
409 CONVERSATION_BUSY
```

## 无状态工具

```http
GET /internal/v1/tools/{kind}/examples
POST /internal/v1/tools/moments/run
POST /internal/v1/tools/writing/run
POST /internal/v1/tools/{task_id}/stop
```

请求：

```json
{
  "inputs": {
    "request": "写一段本周学习复盘",
    "tone": "自然"
  }
}
```

字段必须与对应 Workflow Start 节点一致。两个入口不创建本地商业历史、画像、课程事件或本地反馈。

`GET /internal/v1/tools/{kind}/examples` 仅返回 `moments` 或 `writing` 当前启用的案例；案例的完整 `request` 直接复用对应 `/run` 的既有输入，不改变 Workflow DSL。管理员案例 CRUD/恢复接口位于 `/internal/v1/admin/tools/{kind}/examples`，沿用双 Token 管理鉴权。

## 入口配置

```http
GET /internal/v1/entry-config
```

服务从 Dify 参数接口聚合开场白和推荐问题，并声明入口是否支持历史、反馈和课程。

## 历史课程与学习路线事件（新聊天不适用）

`learning_route` 表示服务端已校验过的官方路线。它可能处于 `awaiting_route_confirmation`、`awaiting_stage_confirmation`、`active` 或 `completed`；只有用户明确确认路线和当前阶段后，才允许出现该路线的 `course_cards`。待路线或阶段确认时，`learning_route` 和正文可以展示阶段与课程名称供用户判断，但不得把这些名称表述为已推荐课程。

`learning_route` 每次返回完整路线的全部阶段，阶段按目录顺序排列。每个 `stages[]` 同时保留机器字段 `course_ids`，并提供与其一一对应、同序的用户展示字段 `course_names`；另外提供 `course_collection_names`，以及按原始合集顺序组织的 `course_collections: [{"name": string, "course_names": string[]}]`。这两个合集字段只取当前阶段的课程 ID，跨路线/跨阶段的其它课程不会带入；没有可用课程集时返回空数组。合集按源表连续块、路线/阶段作用域隔离。上述字段由现有课程目录快照派生，不新增 Redis 键或数据库字段。

`course_cards.items` 只包含启用且审核通过的课程。卡片中的 `redirect_url` 是唯一允许客户端打开的课程地址；不要从回答正文、Dify 文档或 `official_url` 推导链接。明确课程、课程集或最新课程查询可返回独立课程卡且最多 5 门；激活路线的卡片严格只返回已确认当前阶段的全部有效课程，按阶段内顺序，不含下一阶段或其它阶段。多个官方路线候选均可匹配时，服务只返回澄清，不回退独立课程卡；确实没有官方路线证据时，可明确说明未匹配后转为独立课程路径。

`course_recommendation_note` 是可选的单条“专家见解”：服务端在本轮确实返回有效课程卡时、且在全部卡片之后发送，内容为 1–2 句、最多 80 字的组合学习动作，不带推荐/匹配理由或前缀。模型见解会经过最终课程 ID 全量一致、链接和内部 ID 门禁；缺失或不合规时使用同样受限的服务端默认见解。无课程卡、路线/阶段待确认或解析失败时不发送。该事件名和 `{"content":"..."}` payload 不变。

用户自身状态、意图、选择和学习进度按“当前轮明确表达 → 最近未摘要历史中的明确确认 → 会话摘要中的已确认内容 → 路线进度 JSON → 用户画像 JSON”取值；低优先级信息不得覆盖高优先级事实。明确选择新路线不会取消既有路线进度；拒绝、换一条或含糊答复不产生持久拒绝状态。

## AI 管理端文件清洗

```http
GET  /internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches
GET  /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/model-tasks/{task_id}/items/{item_id}/retry
```

以上接口还要求 `X-Admin-Token`；`kb_code` 只能为 `general` 或 `course`。当前测试页只展示 `general` 策略；`course_information`、`learning_route`、`course_route_catalog` 仅保留历史代码和读取兼容，所有课程/学习路线写入入口均在创建批次前返回 `409 STRATEGY_DISABLED`，不创建本地批次、文件、上传目录，也不会触发课程解析或模型发布。完整条目查询、复核、启停和删除接口以 [小程序后端与 AI 管理服务接口对接标准](小程序后端与AI管理服务接口对接标准.md) 为准。

模型契约精确重试接口只接受已完成、唯一候选、`realtime` 模式且错误码为 `MODEL_CONTRACT_INVALID` 的条目；它只重新排队该条目，不开启 Bailian Batch、不绕过严格契约。其他状态返回 `409`，客户端应按错误码处理，不解析模型原始错误文字。

上传为 `multipart/form-data`：

课程目录接口已关闭，不再提供当前上传示例。旧接口调用若仍使用 `course_information`、`learning_route` 或 `course_route_catalog`，统一返回 `STRATEGY_DISABLED`；用户选择的其它策略只按该 `strategy_code` 分派，不会隐式回退到课程/学习路线策略。

## 统一错误

```json
{
  "code": "DIFY_UNAVAILABLE",
  "message": "AI服务暂时不可用，请稍后重试",
  "request_id": "uuid",
  "retryable": true
}
```

生产客户端应依据 `code` 和 `retryable` 处理，不解析 Dify 原始错误文字。

## OpenAPI

服务启动后可查看：

- `/docs`
- `/openapi.json`

只读管理接口还包括：

```http
GET /internal/v1/admin/conversations
GET /internal/v1/admin/conversations/{conversation_id}
GET /internal/v1/admin/course-clicks
GET /internal/v1/admin/catalog
```

会话审计接口的 `user_id` 是管理人员可见的原始用户 ID，不返回内部 `user_hash`。新聊天列表使用最近一条助手消息的回答、通用引用、token 消耗和首 token/总响应耗时；历史推荐字段仅容错读取。`recommendation_note` 始终返回空字符串，旧库原值不回填、不改写。
