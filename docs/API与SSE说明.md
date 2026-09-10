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
POST   /internal/v1/business/messages/{message_id}/regenerate
```

聊天请求：

```json
{
  "query": "我想从零学习AI工具，应该怎么开始？",
  "conversation_id": null
}
```

不传 `conversation_id` 创建新会话；续聊时只能传本服务返回的本地 ID。

### 商业顾问消息操作

- `POST /internal/v1/business/messages/{message_id}/feedback` 请求体为
  `{"rating":"like|dislike|null","content":"可选，最多1000字"}`；`message_id` 是本地
  `BusinessMessage.id`，服务端校验当前用户、入口和有效会话归属。
- `POST /internal/v1/business/messages/{message_id}/regenerate` 无请求体，返回
  `text/event-stream`。只接受当前会话最新、未进入摘要游标且状态允许的助手消息；否则返回
  `409 MESSAGE_NOT_REGENERATABLE`。服务清空旧答案后严格排除原用户/助手二元组重发，并覆盖同一助手消息 ID。
- 成功或停止沿用商业 SSE 的 `message_end`；`message_id` 仍为原本地 ID。失败发送带该本地 ID、`status=failed` 的 `error`，客户端丢弃本轮增量。

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

商业重生成受理后旧答案立即失效：`completed` 替换同一消息，`stopped` 保留新片段，`failed` 不保留旧答案或新片段。消息列表的 `can_regenerate` 仅对当前最新且未摘要的可重生成助手消息为 `true`。

同一会话已有生成时返回：

```http
409 CONVERSATION_BUSY
```

消息或会话不存在返回 `404 MESSAGE_NOT_FOUND`；消息不是最新可操作回答返回
`409 MESSAGE_NOT_REGENERATABLE`；Dify 不可达/超时返回 `502 DIFY_UNAVAILABLE`，响应非法返回
`502 DIFY_RESPONSE_INVALID`。

预检失败发生在 `StreamingResponse` 建立前，HTTP 状态为对应的 4xx/5xx，响应为 JSON 错误，不会建立 SSE。预检通过后为 HTTP 200；如果 Dify 在流中失败，则通过 `event: error` 返回 `status=failed`、`retryable` 和会话/消息/任务 ID，客户端按失败规则处理累计增量。

流中失败示例（HTTP 已为 200）：

```text
event: error
data: {"code":"DIFY_UNAVAILABLE","status":"failed","retryable":true,"conversation_id":"local-c1","message_id":"opaque-m1","task_id":"task-1"}
```

## 朋友圈顾问与日常写作工具

```http
GET /internal/v1/tools/{kind}/examples
GET /internal/v1/tools/{kind}/conversations/current
POST /internal/v1/tools/{kind}/conversations
POST /internal/v1/tools/{kind}/conversations/{conversation_id}/reset
POST /internal/v1/tools/moments/run
POST /internal/v1/tools/writing/run
POST /internal/v1/tools/{task_id}/stop
POST /internal/v1/tools/{kind}/messages/{message_id}/feedback
POST /internal/v1/tools/{kind}/messages/{message_id}/regenerate
```

请求：

> 本节旧版示例曾允许在 `inputs` 中携带类型/格式；该旧契约已废止。请以本文新增的顶层 `conversation_id` 规则和交付版接口文档为准。

```json
{
  "conversation_id": "<本服务创建接口返回的本地会话ID>",
  "inputs": {
    "request": "写一段本周学习复盘",
    "tone": "自然"
  }
}
```

创建会话请求为 `{\"type\":\"sales|seeding|polish|expand\",\"output_format\":\"article|wechat_public_account\"}`（按工具限制取值）。服务端将会话中的类型和成稿格式映射到 Dify Chatflow。两个工具不创建商业历史、画像、课程事件或商业顾问数据。

`GET /internal/v1/tools/{kind}/examples` 现在返回空目录（旧案例已按需求清除）；管理员仍可在 `/internal/v1/admin/tools/{kind}/examples` 下使用双 Token CRUD 新增案例，不再提供 restore-defaults。调用 `/run` 前必须先创建会话并把本地 `conversation_id` 放在请求体顶层；`inputs` 不得传 `conversation_id`、`type`、`format` 或 `output_format`，顶层未知字段返回 `422 extra_forbidden`。输入精确“新对话”“开启新对话”“开始新对话”“新建对话”“重新开始对话”“重新开启对话”“重开对话”（允许首尾空白和句末标点）时，SSE 直接返回“已开启新会话”，不调用 Dify；其它相似表达按普通内容处理。

### 工具消息操作与流终态

`kind` 仅允许 `moments` 或 `writing`。工具 `message_id` 是不透明的 Dify 消息 ID，客户端不得解析或拼接；本服务只接受当前用户当天有效会话、当前 Dify `conversation_id` 内的消息。

- `POST /internal/v1/tools/{kind}/messages/{message_id}/feedback` 请求体为
  `{"rating":"like|dislike|null","content":"可选，最多1000字"}`，直接转发 Dify feedback。
- `POST /internal/v1/tools/{kind}/messages/{message_id}/regenerate` 无请求体，回读目标消息的
  `query/inputs`，按当前工具白名单重新校验并注入当前类型、写作格式和管理员风格，在同一
  `conversation_id` 重发并返回新的不透明 `message_id`。非当前会话最新消息返回 `409 MESSAGE_NOT_REGENERATABLE`。
- 普通生成和重生成取得任务 ID 后先发送 `status(code=task_started,task_id)`；成功或停止发送含本地
  `conversation_id`、Dify `message_id`、`task_id` 和 `status=completed|stopped` 的 `message_end`。
  Dify 尚未创建消息时极早停止允许 stopped 的 `message_id` 为 `null`；completed 仍必须有 ID，
  客户端不得对 null ID 发起反馈或重生成。
  Dify 错误、非法流或会话不一致发送 `error(status=failed)`；客户端失败时清空本轮全部
  `answer_delta`，停止时保留已收到片段。

商业顾问使用本服务显式组装上下文且 Dify Memory 关闭；朋友圈顾问和日常写作复用同一 Dify 会话，目标 Memory 窗口为 6，重生成后的旧问答可能仍留在远端短期 Memory。

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

### 三类顾问消息操作增量

- 新增 `POST /internal/v1/business/messages/{message_id}/regenerate`；商业仅允许最新、未摘要回答，严格替换同一本地 assistant 消息，失败清空、停止保留片段。
- 新增 `POST /internal/v1/tools/{kind}/messages/{message_id}/feedback` 与 `/regenerate`（`kind=moments|writing`）；工具 message ID 为不透明 Dify ID，重生成回读 query/inputs 后在同一 conversation 重发。
- 既有 business/tools stop 路由保持；工具 SSE 先发 `task_started`，终态带 conversation/message/task/status；失败清空页面累计 delta，停止保留。
- rating 支持 `like|dislike|null`，content 最长 1000；工具 Memory 目标窗口 6，商业主 LLM Memory 关闭。jx/真实小程序后端 E2E 与本地 DSL/线上发布证据分开记录。
