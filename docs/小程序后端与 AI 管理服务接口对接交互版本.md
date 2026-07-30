# 小程序后端与 AI 管理服务接口对接

## 1\. 基本约定

### 1\.1 鉴权

除健康检查和课程跳转外，接口默认需要：

```HTTP
X-Internal-Token: <内部服务令牌>
```

用户侧接口还需要：

```HTTP
X-User-Id: <稳定用户ID>
```

可选请求头：

```HTTP
X-User-Name: <用户展示名>
X-Request-Id: <请求ID>
```

管理接口还需要：

```HTTP
X-Admin-Token: <管理员令牌>
```

约束：

- `X-User-Id` 必填，长度不超过 255。

- `X-User-Name` 选填，仅用于后台展示，不传给 Dify。

- 小程序前端不能持有 `X-Internal-Token`、`X-Admin-Token`、Dify Key、Dataset ID。

### 1\.2 统一错误结构

```JSON
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

### 1\.3 SSE 约定

流式接口响应类型：

```HTTP
Content-Type: text/event-stream
```

客户端必须按 SSE 事件边界解析，不能按网络分片解析。

所有 SSE 事件都包含：

```JSON
{
  "schema_version": "2.0",
  "request_id": "uuid",
  "sequence": 1
}
```

出现 `error` 事件时，之前收到的 `answer_delta` 只能视为部分内容。

## 2\. 小程序后端对接接口

### 2\.1 入口配置

```HTTP
GET /internal/v1/entry-config
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
```

返回示例：

```JSON
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
      "course_recommendation": true
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

### 2\.2 商业顾问聊天

```HTTP
POST /internal/v1/business/chat
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
X-User-Name: <用户展示名，可选>
```

请求体：

```JSON
{
  "query": "我想从零学习AI工具，应该怎么开始？",
  "conversation_id": null
}
```

说明：

- `conversation_id` 不传或传 `null` 表示新建会话。

- 续聊时只能传本服务返回的本地 `conversation_id。`

返回方式：SSE

#### 商业顾问 SSE 事件

可能出现的事件：

- `request_started`

- `status`

- `answer_delta`

- `retriever_resources`

- `learning_route`

- `course_cards`

- `course_recommendation_note`

- `message_end`

- `error`

`request_started` 示例：

```Plain Text
event: request_started
data: {"schema_version":"2.0","request_id":"req-001","sequence":1,"conversation_id":"conv_001","message_id":"msg_001"}
```

`status` 示例：

```Plain Text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":2,"code":"retrieving","message":"正在检索相关资料…"}
```

开始生成后的 `status` 示例：

```Plain Text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":3,"code":"task_started","message":"已开始生成","task_id":"task_001"}
```

`answer_delta` 示例：

```Plain Text
event: answer_delta
data: {"schema_version":"2.0","request_id":"req-001","sequence":4,"delta":"建议先从高频办公场景开始。"}
```

`course_cards` 示例：

```Plain Text
event: course_cards
data: {"schema_version":"2.0","request_id":"req-001","sequence":5,"items":[{"course_id":"course_ai_001","name":"AI工具入门","direction":"AI基础","description":"适合零基础用户","enabled":true,"review_status":"approved","redirect_url":"https://example.com/r/xxxxx"}]}
```

说明：

- `redirect_url` 为服务端生成的完整访问链接。

- 调用方只使用这个链接，不自行拼接路径，不自行生成参数，不解析其中内容。

`learning_route` 示例：

```Plain Text
event: learning_route
data: {"schema_version":"2.0","request_id":"req-001","sequence":6,"route_id":"route_ai_basic","name":"AI入门路线","direction":"AI基础","target":"完成AI基础使用","stages":[{"stage_id":"stage_1","name":"入门","order":1,"course_ids":["course_ai_001"]}],"current_stage_id":null,"next_stage_id":null,"state":"awaiting_route_confirmation","progress_status":"pending_route","confirmation_required":true}
```

课程/路线规则：

- `learning_route.state=awaiting_route_confirmation` 或 `awaiting_stage_confirmation` 时，`course_cards` 必须为空；调用方应引导用户明确确认路线和当前阶段。

- 只有 `active` 路线才返回当前阶段及下一阶段的课程卡片，最多 5 门且保持路线顺序。

- 没有官方匹配路线时，服务可返回独立课程；不得临时拼接成路线。

- `course_recommendation_note` 是可选的匹配理由，不是课程事实扩写。课程级 `expert_insight` 已停用，客户端不得等待或渲染它。

`message_end` 示例：

```Plain Text
event: message_end
data: {"schema_version":"2.0","request_id":"req-001","sequence":8,"conversation_id":"conv_001","message_id":"msg_001","dify_message_id":"dify_msg_001","task_id":"task_001","status":"completed","first_token_ms":620,"total_ms":4820,"token_usage":{"total_tokens":128}}
```

`error` 示例：

```Plain Text
event: error
data: {"schema_version":"2.0","request_id":"req-001","sequence":9,"code":"DIFY_UNAVAILABLE","message":"AI服务暂时不可用，请稍后重试","retryable":true}
```

补充：

- 同一会话正在生成时，接口返回 `409 CONVERSATION_BUSY`。

- `message_end.status` 当前可能为 `completed`、`failed`、`stopped`。

### 2\.3 停止商业顾问生成

```HTTP
POST /internal/v1/business/chat/{task_id}/stop
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

返回示例：

```JSON
{
  "result": "success"
}
```

说明：该接口成功返回时，字段以返回为准，不建议前端对返回体做强绑定。

### 2\.4 会话列表

```HTTP
GET /internal/v1/business/conversations?cursor=<cursor>&limit=20
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

返回示例：

```JSON
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

### 2\.5 消息列表

```HTTP
GET /internal/v1/business/conversations/{conversation_id}/messages?cursor=<cursor>&limit=20
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

返回示例：

```JSON
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

### 2\.6 重命名会话

```HTTP
PATCH /internal/v1/business/conversations/{conversation_id}
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

请求体：

```JSON
{
  "title": "新的会话标题"
}
```

返回示例：

```JSON
{
  "conversation_id": "conv_001",
  "title": "新的会话标题"
}
```

### 2\.7 删除会话

```HTTP
DELETE /internal/v1/business/conversations/{conversation_id}
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

返回示例：

```JSON
{
  "deleted": true
}
```

### 2\.8 消息反馈

```HTTP
POST /internal/v1/business/messages/{message_id}/feedback
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

请求体：

```JSON
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

```JSON
{
  "result": "success"
}
```

说明：该接口成功返回时，字段以返回为准，不建议前端对返回体做强绑定。

### 2\.9 朋友圈顾问

```HTTP
POST /internal/v1/tools/moments/run
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

请求体：

```JSON
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

### 2\.10 日常写作

```HTTP
POST /internal/v1/tools/writing/run
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

请求体：

```JSON
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

### 2\.11 停止工具生成

```HTTP
POST /internal/v1/tools/{task_id}/stop
```

请求头：

```HTTP
X-Internal-Token: <内部服务令牌>
X-User-Id: <稳定用户ID>
```

返回示例：

```JSON
{
  "result": "success"
}
```

说明：该接口成功返回时，字段以当前 Dify 返回为准，不建议前端对返回体做强绑定。

### 2\.12 课程点击统计接入

商业顾问返回课程卡片时，会带：

```JSON
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

```HTTP
GET /internal/v1/admin/course-clicks
```

## 4\. AI 管理页对接接口

以下接口都需要：

```HTTP
X-Internal-Token: <内部服务令牌>
X-Admin-Token: <管理员令牌>
```

### 4\.1 会话列表

```HTTP
GET /internal/v1/admin/conversations?user_id=<user_id>&user_name=<user_name>&limit=50
```

返回示例：

```JSON
{
  "items": [
    {
      "conversation_id": "conv_001",
      "user_hash": "hash_xxx",
      "user_name": "张三",
      "title": "我想从零学习AI工具",
      "summary": "用户希望从零开始学习 AI 工具。",
      "summary_gap": false,
      "last_active_at": "2026-07-28T09:30:00Z"
    }
  ]
}
```

### 4\.2 会话详情

```HTTP
GET /internal/v1/admin/conversations/{conversation_id}
```

返回示例：

```JSON
{
  "conversation_id": "conv_001",
  "user_hash": "hash_xxx",
  "user_name": "张三",
  "title": "我想从零学习AI工具",
  "summary": "用户希望从零开始学习 AI 工具。",
  "messages": [
    {
      "message_id": "msg_001",
      "role": "assistant",
      "content": "建议先从高频办公场景开始。",
      "status": "completed",
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

### 4\.3 课程点击统计

```HTTP
GET /internal/v1/admin/course-clicks?course_id=<course_id>&from_date=2026-07-01&to_date=2026-07-28&page=1&limit=50
```

返回示例：

```JSON
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

### 4\.4 课程与路线目录

```HTTP
GET /internal/v1/admin/catalog
```

返回示例：

```JSON
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

### 4\.5 删除指定用户 AI 数据

```HTTP
DELETE /internal/v1/admin/users/{raw_user_id}/ai-data
```

返回示例：

```JSON
{
  "deleted": true,
  "conversation_count": 3,
  "external_deletions_queued": 3
}
```

说明：

- 删除用户 AI 会话、消息、画像、路线进度等 AI 数据。

- 保留课程每日 PV/UV 汇总数据。

### 4\.6 历史保留配置

#### 查询

```HTTP
GET /internal/v1/admin/settings/retention
```

返回示例：

```JSON
{
  "history_retention_days": 7,
  "cleanup_hour": 2,
  "cleanup_minute": 0
}
```

#### 更新

```HTTP
PUT /internal/v1/admin/settings/retention
```

请求体：

```JSON
{
  "history_retention_days": 14
}
```

返回示例：

```JSON
{
  "history_retention_days": 14,
  "cleanup_hour": 2,
  "cleanup_minute": 0
}
```

### 4\.7 输出风格配置

当前配置项如下：

|style\_code|对应入口|实际作用|
|---|---|---|
|`business_answer`|商业顾问|控制商业顾问主回答正文风格|
|`moments`|朋友圈顾问|控制朋友圈顾问输出风格|
|`writing`|日常写作|控制日常写作输出风格|
|可以直接理解为：|||

- 商业顾问当前只使用主回答风格；补充观点项为历史兼容项

- 朋友圈顾问 1 个可调项

- 日常写作 1 个可调项

#### 查询

```HTTP
GET /internal/v1/admin/style-settings
```

返回示例：

```JSON
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

- `moments`

- `writing`

#### 更新

```HTTP
PUT /internal/v1/admin/style-settings/{style_code}
```

请求体：

```JSON
{
  "enabled": true,
  "style_prompt": "自然、克制、避免夸张表达。"
}
```

返回示例：

```JSON
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

### 4\.8 知识库概览

```HTTP
GET /internal/v1/admin/knowledge-bases
```

返回示例：

```JSON
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

### 4\.9 知识库文档列表

```HTTP
GET /internal/v1/admin/knowledge-bases/{kb_code}/documents
```

`kb_code` 只允许：

- `general`

- `course`

返回示例：

```JSON
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

### 4\.10 文档清洗管理能力

#### 4\.10\.1 获取可用清洗策略

```HTTP
GET /internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies
```

`kb_code` 只允许：

- `general`

- `course`

当前正式文档清洗策略都在 `general` 知识库下使用。

返回示例：

```JSON
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

#### 4\.10\.2 创建知识库上传批次

```HTTP
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches
Content-Type: multipart/form-data
```

表单字段：

- `files`: 多文件

- `upload_mode`: `cleaned` 或 `raw`

- `strategy_code`

- `common_metadata`

示例：

```Plain Text
kb_code=general
upload_mode=cleaned
strategy_code=lead_magnet
common_metadata={"document_type":"lead_magnet","review_status":"approved"}
files=<文件1>
files=<文件2>
```

返回示例：

```JSON
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

#### 4\.10\.3 查询上传批次状态

```HTTP
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}
```

返回示例：同上传批次返回结构。

#### 4\.10\.4 查询文件条目列表

```HTTP
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items?status=<status>&cursor=<cursor>&limit=50
```

返回示例：

```JSON
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

#### 4\.10\.5 查询条目详情

```HTTP
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}
```

返回示例：

```JSON
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

#### 4\.10\.6 重试失败文件

```HTTP
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry
```

返回示例：

```JSON
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

```JSON
{
  "code": "VALIDATION_ERROR",
  "message": "待复核条目不能直接重试，请先执行人工复核"
}
```

#### 4\.10\.7 重试单条目

```HTTP
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry
```

返回示例：

```JSON
{
  "item_id": "item_001",
  "status": "pending"
}
```

说明：

- 该接口只允许 `failed` 条目调用。

- `review_required` 条目调用时返回 `409 CONFLICT`，必须先执行复核。

冲突示例：

```JSON
{
  "code": "VALIDATION_ERROR",
  "message": "只有失败条目可以重试；待复核条目请先执行人工复核"
}
```

#### 4\.10\.8 条目复核

```HTTP
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

```JSON
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

### 4\.11 启用或停用知识库文档

```HTTP
PATCH /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status
```

请求体：

```JSON
{
  "enabled": false
}
```

返回示例：

```JSON
{
  "document_id": "doc_001",
  "enabled": false
}
```

### 4\.12 删除知识库文档

```HTTP
DELETE /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}
```

请求体：

```JSON
{
  "confirm_name": "测试文档.md"
}
```

返回示例：

```JSON
{
  "deleted": true,
  "document_id": "doc_001"
}
```

## 5\. 2026-07-30 隐私与清洗状态补充

本轮没有修改第 3、4 章既有路径、请求字段、SSE 事件名或公开响应字段。新增约束属于服务端输出安全和清洗事务语义：

- `answer_delta`、`retriever_resources`、消息历史和会话标题会统一隐藏清洗审计中登记的真实姓名、昵称、笔名、账号称呼、原文件名及 Dify 管理名称。
- 跨多个 `answer_delta` 分片出现的姓名也必须被完整替换；调用方不能假设一个姓名一定落在同一 SSE 分片。
- 检索引用中的非受控 URL 会被移除；服务端生成的课程卡 `redirect_url` 不受影响。
- 清洗条目仍可逐条查询和复核，但发布与失败重试以源文件为事务边界。任一待复核或失败条目会阻止同源其他条目完成发布。
- 新生成的 Dify 管理名称为 `原文件名主体__001.原扩展名`、`__002`……。完整原文件名仅用于受保护的管理与审计，不得作为公开引用返回。
- `personal_business_consultation` 当前同时接受 DOCX 和 PDF。隐私或版面门禁未通过时返回条目级 `review_required`，不会把文件伪装成清洗失败或强制发布。

## 6\. 变更表

| 修改原因 | 修改前 | 修改后 | 验证证据 |
|---|---|---|---|
| 阻止清洗资料身份信息进入小程序 | 只过滤模型生成 URL；检索引用和跨分片姓名没有统一过滤 | 回答、SSE 引用、持久化历史统一按受保护别名映射过滤，支持跨分片 | 本地契约测试 11/11；jx 公开引用检查 3/3 |
| 防止原文件名经引用公开 | Dify 管理名和 `retriever_resources` 可携带原文件名 | 管理端仍保留原名，公开回答和引用统一替换为受保护来源 | jx 清洗验收 15/15，已知文件名与管理名 0 泄露 |
| 统一拆分文档命名 | `主体__SHA前8位__序号.md` | `主体__三位序号.原扩展名`，超长名称确定性截断 | 127 个成功子文档名称回读全部通过 |
| 消除同源部分发布 | 子条目并行发布，可能出现 `mixed` 和远端残留 | 同源子项串行验证后一次提交；失败整文件补偿，课程/路线目录同事务提交 | 本地受控 metadata 故障测试通过；84 子文档整文件提交通过 |
| 补齐咨询格式与门禁 | 仅 PDF；门禁异常表现为文件失败 | DOCX/PDF 双格式；可安全发布或进入 `review_required` | 真实 DOCX 成功、真实 PDF 复核，安全合成 DOCX/PDF 成功 |
| 修正大 GB18030 文件误判 | 64 KiB 探测若截断双字节字符会报签名无效 | 使用增量解码器允许样本末尾保留不完整字符 | 29+84 篇公众号样本确定性通过 |
| 让已发布 Dify 工作流立即执行隐私约束 | 只修改本地 DSL/提示词不会自动更新线上已发布工作流 | 服务端在现有 `business_output_style` 输入中追加公开引用隐私指令，输出端仍保留强制过滤 | 本地运行时输入测试通过；r3 远端接口 37/37、公开引用 3/3 |

本轮远端证据汇总见 [接口契约与清洗全链路验收记录-2026-07-30.md](接口契约与清洗全链路验收记录-2026-07-30.md)。

## 7\. 远端发布结果

2026-07-30 最终实测版本：

```text
核心：jx-now-ai-management:0.2.1-contract-cleaning-90a23f26-r3
测试页：jx-now-ai-management-ui:0.2.1-contract-cleaning-90a23f26-r3
Alembic：e11c7f4a2d90 (head)
```

接口契约 `37/37`、真实样本清洗 `15/15`、公开聊天与引用 `3/3`、RAG 检索门槛均通过。测试数据已精确清理，正式 9 门课程、2 条路线及配置保留。
