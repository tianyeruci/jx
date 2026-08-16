# 小程序后端与 AI 管理服务接口对接标准

> 当前生效（2026-08-14，ADR-036）：商业顾问不再执行课程推荐或学习路线处理。`POST /internal/v1/business/chat` 的入参和基础 SSE 协议不变，但 `entry-config` 的商业顾问能力为 `course_recommendation=false`，新流不会发送 `learning_route`、`course_cards`、`course_recommendation_note`。旧事件/字段段落仅适用于历史兼容读取。
## 1. 基本约定
### 1.1 鉴权
除健康检查和课程跳转外，接口默认需要：
```http
X-Internal-Token: <内部服务令牌>
```
用户侧接口还需要：
```http
X-User-Id: <稳定用户ID>
```
可选请求头：
```http
X-User-Name: <用户展示名>
X-Request-Id: <请求ID>
```
管理接口还需要：
```http
X-Admin-Token: <管理员令牌>
```
约束：
- `X-User-Id` 必填，长度不超过 255。
- `X-User-Name` 选填，仅用于后台展示，不传给 Dify。
- 小程序前端不能持有 `X-Internal-Token`、`X-Admin-Token`、Dify Key、Dataset ID。
### 1.2 统一错误结构
```json
{
  "code": "DIFY_UNAVAILABLE",
  "message": "AI服务暂时不可用，请稍后重试",
  "request_id": "uuid",
  "retryable": true
}
```
常见错误码：
- `UNAUTHORIZED`
- `VALIDATION_ERROR`
- `CONVERSATION_NOT_FOUND`
- `CONVERSATION_BUSY`
- `CONFLICT`
- `RECOMMENDATION_INVALID`
- `DIFY_UNAVAILABLE`
- `DIFY_TIMEOUT`
- `DIFY_RATE_LIMITED`
- `CLEANING_FAILED`
- `NOT_FOUND`
### 1.3 SSE 约定
流式接口响应类型：
```http
Content-Type: text/event-stream
```
客户端必须按 SSE 事件边界解析，不能按网络分片解析。
所有 SSE 事件都包含：
```json
{
  "schema_version": "2.0",
  "request_id": "uuid",
  "sequence": 1
}
```
出现 `error` 事件时，之前收到的 `answer_delta` 只能视为部分内容。
## 2. 健康检查
### 2.1 存活检查
```http
GET /health/live
```
请求头：无
返回示例：
```json
{
  "status": "ok"
}
```
### 2.2 就绪检查
```http
GET /health/ready
```
请求头：无
返回示例：
```json
{
  "status": "ready",
  "database": true,
  "redis": true,
  "dify_apps_configured": true,
  "dify_knowledge_configured": true,
  "dify_reachable": true
}
```
## 3. 小程序后端对接接口
### 3.1 入口配置
```http
GET /internal/v1/entry-config
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
```
返回示例：
```json
{
  "entries": [
    {
      "code": "business_advisor",
      "name": "商业顾问",
      "opening_statement": "你好，我来帮你分析问题。",
      "suggested_questions": [
        "我现在适合学什么？"
      ],
      "history": true,
      "feedback": true,
      "course_recommendation": false
    },
    {
      "code": "moments_advisor",
      "name": "朋友圈顾问",
      "opening_statement": "",
      "suggested_questions": [],
      "history": false,
      "feedback": false,
      "course_recommendation": false
    },
    {
      "code": "writing_advisor",
      "name": "日常写作",
      "opening_statement": "",
      "suggested_questions": [],
      "history": false,
      "feedback": false,
      "course_recommendation": false
    }
  ]
}
```

### 3.2 商业顾问聊天
```http
POST /internal/v1/business/chat
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
X-User-Name: <用户展示名，可选>
```
请求体：
```json
{
  "query": "我的获客转化下降，应该先如何诊断？",
  "conversation_id": null
}
```
说明：
- `conversation_id` 不传或传 `null` 表示新建会话。
- 续聊时只能传本服务返回的本地 `conversation_id`。
- 不能传 Dify 的 `conversation_id`。
返回方式：SSE
#### 商业顾问 SSE 事件
可能出现的事件：
- `request_started`
- `status`
- `answer_delta`
- `retriever_resources`
- `message_end`
- `error`
`request_started` 示例：
```text
event: request_started
data: {"schema_version":"2.0","request_id":"req-001","sequence":1,"conversation_id":"conv_001","message_id":"msg_001"}
```
`status` 示例：
```text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":2,"code":"retrieving","message":"正在检索相关资料…"}
```
开始生成后的 `status` 示例：
```text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":3,"code":"task_started","message":"已开始生成","task_id":"task_001"}
```
商业顾问分段回答时，`status.code` 还可能为：
```text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":4,"code":"progress_answer_started","message":"已开始返回即时提示"}

event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":6,"code":"final_answer_started","message":"已开始返回最终答案"}
```
这两个状态码只标记同一条 SSE 流中即时提示和最终答案的边界，不改变 `answer_delta` 事件名或 `data.delta` 字段。旧客户端可以继续顺序追加所有 `answer_delta`；需要分栏的客户端在边界码后切换展示区。
`answer_delta` 示例：
```text
event: answer_delta
data: {"schema_version":"2.0","request_id":"req-001","sequence":4,"delta":"建议先从高频办公场景开始。"}
```
#### 历史兼容字段（新聊天不产生）

以下字段只可能存在于 ADR-036 前的历史消息中；当前 SSE 不产生、客户端不得渲染或据此恢复课程/路线交互。

`course_cards` 历史示例：
```text
event: course_cards
data: {"schema_version":"2.0","request_id":"req-001","sequence":5,"items":[{"course_id":"course_ai_001","name":"AI工具入门","direction":"AI基础","description":"适合零基础用户","enabled":true,"review_status":"approved","redirect_url":"https://example.com/r/xxxxx"}]}
```
说明：
- `redirect_url` 为服务端生成的完整访问链接。
- 调用方只使用这个链接，不自行拼接路径，不自行生成参数，不解析其中内容。
`learning_route` 示例：
```text
event: learning_route
data: {"schema_version":"2.0","request_id":"req-001","sequence":6,"route_id":"route_ai_basic","name":"AI入门路线","direction":"AI基础","target":"完成AI基础使用","stages":[{"stage_id":"stage_1","name":"入门","order":1,"course_ids":["course_ai_001"],"course_names":["AI工具入门"],"course_collection_names":["AI写作课"]}],"current_stage_id":null,"next_stage_id":null,"state":"awaiting_route_confirmation","progress_status":"pending_route","confirmation_required":true}
```
课程/路线规则：
- `learning_route.state=awaiting_route_confirmation` 或 `awaiting_stage_confirmation` 时，不得发送 `course_cards`；调用方应引导用户明确确认路线和当前阶段。此时可在路线说明中展示审核路线的阶段和课程名称供用户判断，但不得把它们表述为已推荐课程。
- `learning_route.stages[].course_collection_names` 是按阶段内原始目录顺序去重后的课程集名称数组，不是课程名称；`course_collections` 进一步给出每个合集及其当前阶段课程名称，格式为 `[{"name":"课程合集","course_names":["课程名称"]}]`。合集按源表连续块、路线/阶段作用域隔离；没有课程集时两个字段均为空数组。
- `learning_route.stages[].course_names` 与 `course_ids` 一一对应、保持同序，用于用户展示；`course_ids` 仍是机器字段。
- 只有 `active` 路线才返回已确认当前阶段的全部有效课程卡片，按阶段内正式顺序；不得返回下一阶段或其它阶段。明确课程、课程集或最新课程路径仍最多 5 门。
- 仅明确查询课程、课程集或最新课程时可返回独立课程；路线型诉求有多个可信候选时应说明未匹配或澄清，不得回退独立课程；确实没有官方路线证据时应先说明未匹配，再转为独立课程路径，不得临时拼接路线。
- 多条官方路线均可能匹配时，调用方只接收澄清与路线信息，不接收课程卡。明确选择新路线不取消旧路线进度；拒绝、换一条或含糊答复不写持久拒绝状态。
- 用户自身状态、意图、选择和学习进度按“当前轮明确表达 → 最近未摘要历史中的明确确认 → 会话摘要中的已确认内容 → 路线进度 JSON → 用户画像 JSON”取值；低优先级信息不得覆盖高优先级事实。
- `course_recommendation_note` 是可选的单条专家见解，仅在服务端已校验课程卡、全部课程卡片发送完成且内部课程 ID 全量一致时出现；`content` 直接是 1–2 句、最多 80 字的组合学习动作，不含推荐/匹配理由或前缀。事件名和 `{"content":"..."}` 结构不变。
`message_end` 示例：
```text
event: message_end
data: {"schema_version":"2.0","request_id":"req-001","sequence":8,"conversation_id":"conv_001","message_id":"msg_001","dify_message_id":"dify_msg_001","task_id":"task_001","status":"completed","first_token_ms":620,"total_ms":4820,"token_usage":{"total_tokens":128}}
```
`error` 示例：
```text
event: error
data: {"schema_version":"2.0","request_id":"req-001","sequence":9,"code":"DIFY_UNAVAILABLE","message":"AI服务暂时不可用，请稍后重试","retryable":true}
```
补充：
- 同一会话正在生成时，接口返回 `409 CONVERSATION_BUSY`。
- `message_end.status` 当前可能为 `completed`、`failed`、`stopped`。
### 3.3 停止商业顾问生成
```http
POST /internal/v1/business/chat/{task_id}/stop
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
返回示例：
```json
{
  "result": "success"
}
```
说明：该接口成功返回时，字段以当前 Dify 返回为准，不建议前端对返回体做强绑定。
### 3.4 会话列表
```http
GET /internal/v1/business/conversations?cursor=<cursor>&limit=20
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
返回示例：
```json
{
  "items": [
    {
      "conversation_id": "conv_001",
      "title": "我想从零学习AI工具",
      "last_active_at": "2026-07-28T09:30:00Z",
      "created_at": "2026-07-28T09:00:00Z"
    }
  ],
  "next_cursor": null
}
```
### 3.5 消息列表
```http
GET /internal/v1/business/conversations/{conversation_id}/messages?cursor=<cursor>&limit=20
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
返回示例：
```json
{
  "items": [
    {
      "message_id": "msg_user_001",
      "role": "user",
      "content": "我想从零学习AI工具",
      "status": "completed",
      "recommendation": null,
      "retriever_resources": null,
      "created_at": "2026-07-28T09:00:00Z"
    },
    {
      "message_id": "msg_assistant_001",
      "role": "assistant",
      "content": "建议先从高频办公场景开始。",
      "status": "completed",
      "recommendation": {
        "intent": "route_recommendation"
      },
      "retriever_resources": [],
      "created_at": "2026-07-28T09:00:05Z"
    }
  ],
  "next_cursor": null
}
```
### 3.6 重命名会话
```http
PATCH /internal/v1/business/conversations/{conversation_id}
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
请求体：
```json
{
  "title": "新的会话标题"
}
```
返回示例：
```json
{
  "conversation_id": "conv_001",
  "title": "新的会话标题"
}
```
### 3.7 删除会话
```http
DELETE /internal/v1/business/conversations/{conversation_id}
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
返回示例：
```json
{
  "deleted": true
}
```
### 3.8 消息反馈
```http
POST /internal/v1/business/messages/{message_id}/feedback
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
请求体：
```json
{
  "rating": "like",
  "content": "回答比较清楚"
}
```
`rating` 允许值：
- `like`
- `dislike`
- `null`
返回示例：
```json
{
  "result": "success"
}
```
说明：该接口成功返回时，字段以当前 Dify 返回为准，不建议前端对返回体做强绑定。
### 3.9 朋友圈顾问
```http
POST /internal/v1/tools/moments/run
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
请求体：
```json
{
  "inputs": {
    "request": "写一条朋友圈",
    "source_material": "今天学习了AI工作流",
    "audience": "创业者",
    "tone": "自然",
    "constraints": "不要夸张"
  }
}
```
返回方式：SSE
事件类型通常为：
- `request_started`
- `status`
- `answer_delta`
- `message_end`
- `error`
### 3.10 日常写作
```http
POST /internal/v1/tools/writing/run
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
请求体：
```json
{
  "inputs": {
    "request": "帮我改写成更正式的版本",
    "source_text": "原始内容",
    "audience": "客户",
    "tone": "正式",
    "format": "邮件",
    "constraints": "保持简洁"
  }
}
```
返回方式：SSE
事件类型通常为：
- `request_started`
- `status`
- `answer_delta`
- `message_end`
- `error`
### 3.11 停止工具生成
```http
POST /internal/v1/tools/{task_id}/stop
```
请求头：
```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```
返回示例：
```json
{
  "result": "success"
}
```
说明：该接口成功返回时，字段以当前 Dify 返回为准，不建议前端对返回体做强绑定。
### 3.12 独立课程跳转与点击统计
该能力只处理已存在的受控课程跳转链接，不由商业顾问新聊天产生课程卡片：
```json
{
  "course_id": "course_ai_001",
  "name": "AI工具入门",
  "redirect_url": "https://example.com/r/xxxxx"
}
```
小程序后端或前端只需要这样接：
1. 读取 `course_cards[].redirect_url`
2. 用户点击课程卡片时，直接打开这个 `redirect_url`
不需要传额外参数。
不需要额外请求体。
不需要再调用单独的点击上报接口。
服务端会在打开这个链接时自动完成两件事：
1. 记录一次课程有效点击
2. 跳转到课程真实地址
当前“有效点击”的定义：
- 使用服务返回的 `redirect_url`
- 链接合法且未过期
- 课程当前仍然启用且已审核
当前统计口径：
- PV：每次有效点击都计数
- UV：同一用户同一课程同一天只计一次
当前能力边界：
- 可以统计课程被有效点击的次数
- 不提供每一次点击的逐条明细查询
管理页查询统计使用：
```http
GET /internal/v1/admin/course-clicks
```
## 4. AI 管理页对接接口
以下接口都需要：
```http
X-Internal-Token: <内部服务令牌>
X-Admin-Token: <管理员令牌>
```
### 4.1 会话列表
```http
GET /internal/v1/admin/conversations?user_id=<user_id>&user_name=<user_name>&limit=50
```
返回示例：
```json
{
  "items": [
    {
      "conversation_id": "conv_001",
      "user_id": "user_001",
      "user_name": "张三",
      "title": "我想从零学习AI工具",
      "summary": "用户希望从零开始学习 AI 工具。",
      "summary_gap": false,
      "last_active_at": "2026-07-28T09:30:00Z",
      "recommended_courses": [{"course_id": "course_ai_001", "name": "AI工具入门"}],
      "professional_insight": "",
      "recommendation_note": "适合从基础工具开始。",
      "token_usage": 128,
      "first_token_ms": 620,
      "total_ms": 4820
    }
  ],
  "next_cursor": null
}
```

列表中的推荐课程、专业见解来自最近一条助手消息；没有值时分别返回空数组或空字符串。兼容 `recommendation_note` 返回空字符串。`token_usage` 为当前消息总 token，响应耗时单位为毫秒。管理接口不再返回内部 `user_hash`；迁移前没有保存原始用户 ID 的历史记录返回空 `user_id`，用户再次聊天后才会补齐。
### 4.2 会话详情
```http
GET /internal/v1/admin/conversations/{conversation_id}
```
返回示例：
```json
{
  "conversation_id": "conv_001",
  "user_id": "user_001",
  "user_name": "张三",
  "title": "我想从零学习AI工具",
  "summary": "用户希望从零开始学习 AI 工具。",
  "recommended_courses": [{"course_id": "course_ai_001", "name": "AI工具入门"}],
  "professional_insight": "",
  "recommendation_note": "适合从基础工具开始。",
  "token_usage": 128,
  "first_token_ms": 620,
  "total_ms": 4820,
  "messages": [
    {
      "message_id": "msg_001",
      "role": "assistant",
      "content": "建议先从高频办公场景开始。",
      "status": "completed",
      "recommended_courses": [{"course_id": "course_ai_001", "name": "AI工具入门"}],
      "professional_insight": "",
      "recommendation_note": "适合从基础工具开始。",
      "token_usage": 128,
      "first_token_ms": 620,
      "total_ms": 4820,
      "recommendation": {},
      "retriever_resources": [],
      "feedback": null,
      "dify_message_id": "dify_msg_001",
      "dify_task_id": "task_001",
      "created_at": "2026-07-28T09:00:05Z"
    }
  ]
}
```
### 4.3 课程点击统计
```http
GET /internal/v1/admin/course-clicks?course_id=<course_id>&from_date=2026-07-01&to_date=2026-07-28&page=1&limit=50
```
返回示例：
```json
{
  "items": [
    {
      "course_id": "course_ai_001",
      "course_name": "AI工具入门",
      "date": "2026-07-28",
      "clicks": 12,
      "unique_users": 9,
      "live": true
    }
  ],
  "page": 1,
  "limit": 50,
  "total": 1,
  "total_clicks": 12,
  "total_unique_users": 9,
  "course_totals": [
    {
      "course_id": "course_ai_001",
      "course_name": "AI工具入门",
      "clicks": 12,
      "unique_users": 9
    }
  ]
}
```
### 4.4 课程与路线目录
```http
GET /internal/v1/admin/catalog
```
返回示例：
```json
{
  "courses": [
    {
      "course_id": "course_ai_001",
      "name": "AI工具入门",
      "direction": "AI基础",
      "stage": "入门",
      "source_platform": "小鹅通",
      "series_name": null,
      "published_at": null,
      "first_imported_at": "2026-07-29T08:00:00+00:00",
      "prerequisite_course_ids": [],
      "enabled": true,
      "review_status": "approved",
      "optional_data": {}
    }
  ],
  "routes": [
    {
      "route_id": "route_ai_basic",
      "name": "AI入门路线",
      "direction": "AI基础",
      "enabled": true,
      "review_status": "approved",
      "stages": [
        {
          "stage_id": "stage_1",
          "name": "入门",
          "order": 1,
          "course_ids": [
            "course_ai_001"
          ]
        }
      ]
    }
  ]
}
```
### 4.5 删除指定用户 AI 数据
```http
DELETE /internal/v1/admin/users/{raw_user_id}/ai-data
```
返回示例：
```json
{
  "deleted": true,
  "conversation_count": 3,
  "external_deletions_queued": 3
}
```
说明：
- 删除用户 AI 会话、消息、画像、路线进度等 AI 数据。
- 保留课程每日 PV/UV 汇总数据。
### 4.6 历史保留配置
#### 查询
```http
GET /internal/v1/admin/settings/retention
```
返回示例：
```json
{
  "history_retention_days": 7,
  "cleanup_hour": 2,
  "cleanup_minute": 0
}
```
#### 更新
```http
PUT /internal/v1/admin/settings/retention
```
请求体：
```json
{
  "history_retention_days": 14
}
```
返回示例：
```json
{
  "history_retention_days": 14,
  "cleanup_hour": 2,
  "cleanup_minute": 0
}
```
### 4.7 输出风格配置
当前配置项如下：
| style_code | 对应入口 | 实际作用 |
| --- | --- | --- |
| `business_answer` | 商业顾问 | 控制商业顾问主回答正文风格 |
| `business_expert_insight` | 历史兼容项 | 当前课程/路线链路不生成 `expert_insight`，不得用它配置或推导课程级专业见解 |
| `moments` | 朋友圈顾问 | 控制朋友圈顾问输出风格 |
| `writing` | 日常写作 | 控制日常写作输出风格 |
可以直接理解为：
- 商业顾问当前只使用主回答风格；补充观点项为历史兼容项
- 朋友圈顾问 1 个可调项
- 日常写作 1 个可调项
`business_expert_insight` 仍可能出现在旧管理数据中，但当前不应新增或依赖该配置。
#### 查询
```http
GET /internal/v1/admin/style-settings
```
返回示例：
```json
{
  "items": [
    {
      "code": "business_answer",
      "label": "商业顾问主回答风格",
      "enabled": true,
      "style_prompt": "回答直接、清楚，不空泛。",
      "version": 3,
      "updated_at": "2026-07-28T10:00:00Z",
      "updated_by": "system"
    },
    {
      "code": "business_expert_insight",
      "label": "商业顾问补充观点风格",
      "enabled": true,
      "style_prompt": "只补充关键判断，不重复正文。",
      "version": 2,
      "updated_at": "2026-07-28T10:00:00Z",
      "updated_by": "system"
    },
    {
      "code": "moments",
      "label": "朋友圈输出风格",
      "enabled": true,
      "style_prompt": "自然、克制、避免夸张表达。",
      "version": 2,
      "updated_at": "2026-07-28T10:00:00Z",
      "updated_by": "system"
    }
  ]
}
```
当前支持的 `style_code`：
- `business_answer`
- `business_expert_insight`
- `moments`
- `writing`
#### 更新
```http
PUT /internal/v1/admin/style-settings/{style_code}
```
请求体：
```json
{
  "enabled": true,
  "style_prompt": "自然、克制、避免夸张表达。"
}
```
返回示例：
```json
{
  "code": "moments",
  "label": "朋友圈输出风格",
  "enabled": true,
  "style_prompt": "自然、克制、避免夸张表达。",
  "version": 2,
  "updated_at": "2026-07-28T10:00:00Z",
  "updated_by": "system"
}
```
### 4.8 知识库概览
```http
GET /internal/v1/admin/knowledge-bases
```
返回示例：
```json
{
  "items": [
    {
      "code": "general",
      "name": "通用知识库",
      "document_count": 10,
      "enabled_document_count": 8
    },
    {
      "code": "course",
      "name": "课程知识库",
      "document_count": 20,
      "enabled_document_count": 20
    }
  ]
}
```
### 4.9 知识库文档列表
```http
GET /internal/v1/admin/knowledge-bases/{kb_code}/documents
```
`kb_code` 只允许：
- `general`
- `course`
返回示例：
```json
{
  "items": [
    {
      "document_id": "doc_001",
      "name": "测试文档.md",
      "enabled": true,
      "indexing_status": "completed",
      "word_count": 1024,
      "segment_count": 8,
      "created_at": 1722150000,
      "updated_at": 1722150300,
      "source_sha256": "abcdef123456",
      "source_filename": "测试文档.md"
    }
  ]
}
```
### 4.10 文档清洗管理能力
#### 4.10.1 获取可用清洗策略
```http
GET /internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies
```
`kb_code` 只允许：
- `general`
- `course`
当前正式文档清洗策略都在 `general` 知识库下使用。
返回示例：
```json
{
  "items": [
    {
      "code": "lead_magnet",
      "display_name": "资料钩子",
      "supported_extensions": [".docx", ".markdown", ".md", ".pdf", ".txt"],
      "knowledge_base_code": "general"
    },
    {
      "code": "book",
      "display_name": "书籍",
      "supported_extensions": [".docx", ".markdown", ".md", ".pdf", ".txt"],
      "knowledge_base_code": "general"
    }
  ]
}
```
#### 4.10.2 创建知识库上传批次
```http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches
Content-Type: multipart/form-data
```
表单字段：
- `files`: 多文件
- `upload_mode`: `cleaned` 或 `raw`
- `strategy_code`
- `common_metadata`
示例：
```text
kb_code=general
upload_mode=cleaned
strategy_code=lead_magnet
common_metadata={"document_type":"lead_magnet","review_status":"approved"}
files=<文件1>
files=<文件2>
```
返回示例：
```json
{
  "batch_id": "batch_001",
  "strategy_code": "lead_magnet",
  "knowledge_base_code": "general",
  "upload_mode": "cleaned",
  "status": "processing",
  "progress": 0,
  "next_poll_after_seconds": 2,
  "all_succeeded": false,
  "all_processed_without_error": false,
  "total": 2,
  "succeeded": 0,
  "failed": 1,
  "file_counts": {
    "succeeded": 0,
    "failed": 1,
    "excluded": 0,
    "review_required": 0,
    "mixed": 0
  },
  "item_counts": {
    "pending": 0,
    "processing": 0,
    "succeeded": 0,
    "excluded": 0,
    "review_required": 0,
    "failed": 0
  },
  "files": [
    {
      "file_id": "file_001",
      "original_filename": "测试文档.md",
      "status": "pending",
      "stage": "upload",
      "outcome": "pending",
      "error_code": null,
      "error_message": null,
      "source_deleted_at": null,
      "source_expires_at": "2026-08-04T10:00:00Z",
      "item_counts": {
        "pending": 0,
        "processing": 0,
        "succeeded": 0,
        "excluded": 0,
        "review_required": 0,
        "failed": 0
      },
      "updated_at": "2026-07-28T10:00:00Z"
    },
    {
      "file_id": "file_002",
      "original_filename": "不支持.exe",
      "status": "failed",
      "stage": "upload",
      "outcome": "failed",
      "error_code": "UNSUPPORTED_FILE_TYPE",
      "error_message": "策略 lead_magnet 不支持 .exe",
      "source_deleted_at": null,
      "source_expires_at": "2026-08-04T10:00:00Z",
      "item_counts": {
        "pending": 0,
        "processing": 0,
        "succeeded": 0,
        "excluded": 0,
        "review_required": 0,
        "failed": 0
      },
      "updated_at": "2026-07-28T10:00:00Z"
    }
  ]
}
```
补充约束：
- `common_metadata` 必须是 JSON 对象。
- 不允许写入未受控的公共 Metadata 字段。
- `.txt` 以文本读取；其余支持格式进入 Markdown 清洗链路。
- 当前正式清洗策略都在 `general` 知识库下使用。
- `book` 策略在 Markdown 成品超过 `10MB` 时会拆分后再入库。
#### 4.10.3 查询上传批次状态
```http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}
```
返回示例：同上传批次返回结构。
#### 4.10.4 查询文件条目列表
```http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items?status=<status>&cursor=<cursor>&limit=50
```
返回示例：
```json
{
  "file_id": "file_001",
  "items": [
    {
      "item_id": "item_001",
      "sequence": 1,
      "title": "测试文档",
      "document_key": "file_001:1:abcdef123456",
      "status": "succeeded",
      "stage": "completed",
      "error_code": null,
      "error_message": null,
      "retryable": false,
      "review_revision": 0,
      "warnings": [],
      "metadata": {
        "record_type": "article",
        "source_title": "测试文档"
      },
      "chunking": {
        "parent_mode": "paragraph",
        "parent_separator": "<<<DIFY_PARENT>>>",
        "child_separator": "<<<DIFY_CHILD>>>"
      },
      "dify_document_id": "doc_001",
      "remote_parent_count": 1,
      "remote_child_count": 1,
      "updated_at": "2026-07-28T10:00:05Z"
    }
  ],
  "next_cursor": null,
  "total": 1
}
```
#### 4.10.5 查询条目详情
```http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}
```
返回示例：
```json
{
  "item_id": "item_001",
  "sequence": 1,
  "title": "测试文档",
  "document_key": "file_001:1:abcdef123456",
  "status": "succeeded",
  "stage": "completed",
  "error_code": null,
  "error_message": null,
  "retryable": false,
  "review_revision": 0,
  "warnings": [],
  "metadata": {
    "record_type": "article",
    "source_title": "测试文档"
  },
  "chunking": {
    "parent_mode": "paragraph",
    "parent_separator": "<<<DIFY_PARENT>>>",
    "child_separator": "<<<DIFY_CHILD>>>"
  },
  "dify_document_id": "doc_001",
  "remote_parent_count": 1,
  "remote_child_count": 1,
  "cleaned_markdown": "# 测试文档\n\n正文",
  "audit": {},
  "updated_at": "2026-07-28T10:00:05Z"
}
```
#### 4.10.6 重试失败文件
```http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry
```
返回示例：
```json
{
  "file_id": "file_002",
  "status": "pending"
}
```
说明：
- 该接口只用于：
  - 文件级失败；
  - 文件下存在失败条目。
- 如果文件下全部条目都处于 `review_required`，返回 `409 CONFLICT`，必须先对条目执行复核。
冲突示例：
```json
{
  "code": "VALIDATION_ERROR",
  "message": "待复核条目不能直接重试，请先执行人工复核"
}
```
#### 4.10.7 重试单条目
```http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry
```
返回示例：
```json
{
  "item_id": "item_001",
  "status": "pending"
}
```
说明：
- 该接口只允许 `failed` 条目调用。
- `review_required` 条目调用时返回 `409 CONFLICT`，必须先执行复核。
冲突示例：
```json
{
  "code": "VALIDATION_ERROR",
  "message": "只有失败条目可以重试；待复核条目请先执行人工复核"
}
```
#### 4.10.8 条目复核
```http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions
Content-Type: multipart/form-data
```
表单字段：
- `action`
  - `approve`
  - `replace_and_approve`
  - `exclude`
- `expected_revision`
- `reason`
- `metadata_patch`
- `markdown_file`（仅 `replace_and_approve` 时必传）
返回示例：
```json
{
  "item_id": "item_001",
  "status": "pending",
  "review_revision": 1
}
```
说明：
- `expected_revision` 是并发保护字段，版本不一致返回 `409 CONFLICT`。
- `status=review_required` 且 `retryable=false` 表示必须先走复核接口，不能直接调用重试接口。
- `exclude` 不再入库；其余动作会重新提交条目级任务。
- `exclude` 完成后，文件和批次聚合状态会立即刷新。
### 4.11 启用或停用知识库文档
```http
PATCH /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status
```
请求体：
```json
{
  "enabled": false
}
```
返回示例：
```json
{
  "document_id": "doc_001",
  "enabled": false
}
```
### 4.12 删除知识库文档
```http
DELETE /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}
```
请求体：
```json
{
  "confirm_name": "测试文档.md"
}
```
返回示例：
```json
{
  "deleted": true,
  "document_id": "doc_001"
}
```
### 4.13 日志列表
```http
GET /internal/v1/admin/logs?page=1&limit=50
```
可选查询参数：
- `from_time`
- `to_time`
- `level`
- `component`
- `event_code`
- `status`
- `request_id`
- `resource_id`
- `page`
- `limit`
返回示例：
```json
{
  "items": [
    {
      "log_id": "log_001",
      "occurred_at": "2026-07-28T10:00:00Z",
      "level": "INFO",
      "category": "operation",
      "component": "admin_management_api",
      "event_code": "STYLE_SETTING_UPDATED",
      "status": "succeeded",
      "request_id": "req_001",
      "actor_hash": "admin",
      "resource_type": "style_setting",
      "resource_id": "moments",
      "message": "输出风格配置已更新",
      "duration_ms": null,
      "build_version": "0.2.1"
    }
  ],
  "page": 1,
  "limit": 50,
  "total": 1
}
```
### 4.14 日志详情
```http
GET /internal/v1/admin/logs/{log_id}
```
返回示例：
```json
{
  "log_id": "log_001",
  "occurred_at": "2026-07-28T10:00:00Z",
  "level": "INFO",
  "category": "operation",
  "component": "admin_management_api",
  "event_code": "STYLE_SETTING_UPDATED",
  "status": "succeeded",
  "request_id": "req_001",
  "actor_hash": "admin",
  "resource_type": "style_setting",
  "resource_id": "moments",
  "message": "输出风格配置已更新",
  "duration_ms": null,
  "build_version": "0.2.1",
  "details": {
    "enabled": true,
    "version": 2
  }
}
```
## 5. 当前不纳入本标准的能力
以下能力当前没有做成 AI 管理服务接口，不按既有接口对接：
- 一键切换整个应用模型
- 新增或删除整个知识库
- 商业顾问聊天附件上传
- 单独的课程点击上报接口
- 通用幂等键接口
当前处理方式：
- 模型切换：走 Dify 原生控制台
- 知识库新增或删除：走 Dify 原生控制台
- 课程点击统计：通过 `redirect_url` 自动完成

## 6. 2026-07-30 已验证补充约束

本轮未改变既有接口路径、请求字段、SSE 事件名和公开响应结构。以下约束从本版本开始作为接口标准的一部分：

- 商业顾问公开输出中的真实姓名、昵称、笔名、个人账号称呼、原文件名和 Dify 管理名称必须被服务端过滤；范围包括 `answer_delta`、`retriever_resources`、会话标题和历史消息。客户端不得自行拼接或还原受保护来源。
- 姓名过滤必须覆盖跨 SSE 分片的情况。小程序后端仍按标准 SSE 事件解析，不自行拼接或还原受保护来源。
- 新聊天没有课程/路线决策 JSON、课程卡片或路线进度字段；历史消息中的这些字段仅容错读取。检索引用不得携带非受控 URL。
- 原文件名允许出现在管理员鉴权保护下的清洗详情中；不得进入公开回答、公开引用、测试安全摘要或普通运行日志。
- 清洗状态 API 仍返回条目明细，但同一源文件的发布和失败重试是一个原子单元。任一应发布条目失败时，同源本轮 Dify 写入会被删除或停用，课程/路线目录不得部分提交。
- 待复核条目会阻止同源其他条目发布；管理员完成复核后，重新执行的是整文件发布检查。
- Dify 子文档管理名固定为 `原文件名主体__NNN.原扩展名`，其中 `NNN` 从 `001` 开始；公开接口不得返回这个管理名。
- `personal_business_consultation` 支持 `.docx` 与 `.pdf`。无法可靠脱敏或版面不可确认时，条目进入 `review_required`。

2026-07-30 jx 实测版本为：

```text
核心：jx-now-ai-management:0.2.1-contract-cleaning-90a23f26-r3
测试页：jx-now-ai-management-ui:0.2.1-contract-cleaning-90a23f26-r3
Alembic：e11c7f4a2d90 (head)
```

接口 runner 为 `37/37`，清洗与公开引用为 `15/15 + 3/3`，完整证据见 [接口契约与清洗全链路验收记录-2026-07-30.md](接口契约与清洗全链路验收记录-2026-07-30.md)。
