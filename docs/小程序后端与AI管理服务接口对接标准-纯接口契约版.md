# 小程序后端与 AI 管理服务接口对接标准（纯接口契约版）
版本：2026-07-29
适用调用方：
- 小程序后端
- AI 管理端
本文只规定接口路径、鉴权、入参、出参、示例和调用注意事项，不描述服务内部实现、部署方式、测试流程或数据处理流程。
原有文档继续保留：
- 小程序后端与AI管理服务接口对接标准.md
- 小程序后端与AI管理服务接口对接标准-可直接调用测试版.md
## 1. 通用约定
### 1.1 基础地址
所有路径均相对于：
~~~text
<BASE_URL>
~~~
示例中的 Token、用户 ID、会话 ID、任务 ID、文档 ID 均为占位值。
### 1.2 请求头
小程序后端调用用户接口时：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| X-Internal-Token | string | 是 | 否 | 内部服务令牌 |
| X-User-Id | string | 视接口 | 否 | 稳定用户 ID，最长 255 |
| X-User-Name | string | 否 | 是 | 用户展示名 |
| X-Request-Id | string | 否 | 是 | 调用方请求 ID |
AI 管理端调用管理接口时：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| X-Internal-Token | string | 是 | 否 | 内部服务令牌 |
| X-Admin-Token | string | 是 | 否 | 管理员令牌 |
| X-Request-Id | string | 否 | 是 | 调用方请求 ID |
注意：
- 小程序前端不能持有 Internal Token 或 Admin Token。
- 健康检查和课程跳转不需要上述 Token。
- JSON 接口请求头使用 Content-Type: application/json。
- 文件上传和人工复核使用 Content-Type: multipart/form-data。
### 1.3 统一错误结构
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| code | string | 是 | 否 | 机器可读错误码 |
| message | string | 是 | 否 | 可展示错误信息 |
| request_id | string | 是 | 否 | 排查请求 ID |
| retryable | boolean | 是 | 否 | 是否适合重试 |
出参案例：
~~~json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数不合法",
  "request_id": "req-001",
  "retryable": false
}
~~~
常见状态码：
| HTTP 状态 | 说明 |
| --- | --- |
| 200 | 成功 |
| 302 | 课程跳转 |
| 400/422 | 入参不合法 |
| 401 | 鉴权失败 |
| 404 | 资源不存在 |
| 409 | 状态冲突 |
| 410 | 资源已过期 |
| 429 | 请求过多 |
| 502/503 | 下游服务或服务依赖暂不可用 |
### 1.4 SSE 通用字段
SSE 响应类型：
~~~http
Content-Type: text/event-stream
~~~
每个事件 data 都包含：
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| schema_version | string | 是 | 否 | 当前为 2.0 |
| request_id | string | 是 | 否 | 当前请求 ID |
| sequence | integer | 是 | 否 | 事件序号，从 1 递增 |
SSE 数据格式：
~~~text
event: <event_name>
data: <JSON对象>
~~~
客户端必须按 SSE 事件边界解析，不能按网络分片解析。
## 2. 小程序后端接口
### 2.1 获取入口配置
调用方：小程序后端  
对接级别：必须
~~~http
GET /internal/v1/entry-config
~~~
请求头：
| 字段 | 必填 |
| --- | --- |
| X-Internal-Token | 是 |
无 Path、Query 和 Body 参数。
出参字段：
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| entries | array<object> | 是 | 否 | AI 入口列表 |
| entries[].code | string | 是 | 否 | 入口编码 |
| entries[].name | string | 是 | 否 | 入口名称 |
| entries[].opening_statement | string | 是 | 否 | 开场白，可为空字符串 |
| entries[].suggested_questions | array<string> | 是 | 否 | 建议问题 |
| entries[].history | boolean | 是 | 否 | 是否支持历史会话 |
| entries[].feedback | boolean | 是 | 否 | 是否支持消息反馈 |
| entries[].course_recommendation | boolean | 是 | 否 | 是否支持课程推荐 |
入参案例：
~~~http
GET /internal/v1/entry-config
X-Internal-Token: <INTERNAL_TOKEN>
~~~
出参案例：
~~~json
{
  "entries": [
    {
      "code": "business_advisor",
      "name": "商业顾问",
      "opening_statement": "你好，我来帮你分析问题。",
      "suggested_questions": ["我现在适合学什么？"],
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
    }
  ]
}
~~~
注意：调用方应根据返回的能力字段展示功能，不应自行假定所有入口都支持历史、反馈或课程推荐。
### 2.2 商业顾问聊天
调用方：小程序后端  
对接级别：必须  
响应类型：SSE
~~~http
POST /internal/v1/business/chat
~~~
请求头：
| 字段 | 必填 |
| --- | --- |
| X-Internal-Token | 是 |
| X-User-Id | 是 |
| X-User-Name | 否 |
| Content-Type: application/json | 是 |
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束/说明 |
| --- | --- | --- | --- | --- |
| query | string | 是 | 否 | 非空问题 |
| conversation_id | string | 否 | 是 | 新会话为 null；续聊传本服务返回的本地会话 ID |
入参案例：
~~~http
POST /internal/v1/business/chat
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
X-User-Name: 张三
Content-Type: application/json
{
  "query": "我想从零学习 AI 工具，应该怎么开始？",
  "conversation_id": null
}
~~~
可能返回的事件：
| 事件名 | 是否一定出现 | 说明 |
| --- | --- | --- |
| request_started | 是 | 请求和本地会话已创建 |
| status | 是 | 当前处理状态 |
| answer_delta | 视结果 | 回答增量 |
| retriever_resources | 否 | 检索来源 |
| learning_route | 否 | 学习路线 |
| course_cards | 否 | 课程卡片 |
| course_recommendation_note | 否 | 推荐说明 |
| message_end | 成功或正常停止时 | 请求结束 |
| error | 失败时 | 流式错误；出现后不再返回 message_end |
request_started 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| schema_version | string | 是 | 否 |
| request_id | string | 是 | 否 |
| sequence | integer | 是 | 否 |
| conversation_id | string | 是 | 否 |
| message_id | string | 是 | 否 |
~~~text
event: request_started
data: {"schema_version":"2.0","request_id":"req-001","sequence":1,"conversation_id":"conv-001","message_id":"msg-001"}
~~~
status 出参：
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| code | string | 是 | 否 | retrieving 或 task_started 等 |
| message | string | 是 | 否 | 状态文案 |
| task_id | string | 否 | 是 | code=task_started 时返回 |
~~~text
event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":2,"code":"task_started","message":"已开始生成","task_id":"task-001"}
~~~
answer_delta 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| delta | string | 是 | 否 |
~~~text
event: answer_delta
data: {"schema_version":"2.0","request_id":"req-001","sequence":3,"delta":"建议先从高频办公场景开始。"}
~~~
retriever_resources 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| resources | array<object> | 是 | 否 |
~~~text
event: retriever_resources
data: {"schema_version":"2.0","request_id":"req-001","sequence":4,"resources":[]}
~~~
learning_route 出参：
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| route_id | string | 是 | 否 | 路线 ID |
| name | string | 是 | 否 | 路线名称 |
| direction | string | 是 | 否 | 方向 |
| target | string | 是 | 否 | 适用目标 |
| stages | array<object> | 是 | 否 | 有序阶段 |
| stages[].stage_id | string | 是 | 否 | 阶段 ID |
| stages[].name | string | 是 | 否 | 阶段名称 |
| stages[].order | integer | 是 | 否 | 阶段顺序 |
| stages[].course_ids | array<string> | 是 | 否 | 课程 ID |
| current_stage_id | string | 是 | 是 | 当前阶段 ID |
| next_stage_id | string | 是 | 是 | 下一阶段 ID |
| state | string | 是 | 否 | 路线确认状态 |
| progress_status | string | 是 | 否 | 路线进度状态 |
| confirmation_required | boolean | 是 | 否 | 是否需要用户确认 |
~~~text
event: learning_route
data: {"schema_version":"2.0","request_id":"req-001","sequence":5,"route_id":"route-ai-basic","name":"AI入门路线","direction":"AI基础","target":"完成AI基础使用","stages":[{"stage_id":"stage-1","name":"入门","order":1,"course_ids":["course-ai-001"]}],"current_stage_id":null,"next_stage_id":null,"state":"awaiting_route_confirmation","progress_status":"pending_route","confirmation_required":true}
~~~
course_cards 出参：
| 字段 | 类型 | 一定返回 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| items | array<object> | 是 | 否 | 课程卡片，最多 5 个 |
| items[].course_id | string | 是 | 否 | 课程 ID |
| items[].name | string | 是 | 否 | 课程名称 |
| items[].direction | string | 是 | 否 | 方向 |
| items[].description | string | 是 | 否 | 课程真实介绍 |
| items[].stage | string | 否 | 是 | 课程全局阶段 |
| items[].source_platform | string | 否 | 是 | 来源平台 |
| items[].series_name | string | 否 | 是 | 系列名称 |
| items[].published_at | datetime | 否 | 是 | 首次发布时间 |
| items[].first_imported_at | datetime | 否 | 是 | 首次入库时间 |
| items[].prerequisite_course_ids | array<string> | 否 | 否 | 前置课程 ID |
| items[].audience | string/array | 否 | 是 | 适合人群；仅在课程资料明确提供时返回 |
| items[].highlights | string/array | 否 | 是 | 课程亮点；仅在课程资料明确提供时返回 |
| items[].learning_outcomes | string/array | 否 | 是 | 学习收获；仅在课程资料明确提供时返回 |
| items[].special_prerequisites | string/array | 否 | 是 | 特殊前置要求 |
| items[].stage_tags | array<string> | 否 | 否 | 阶段标签 |
| items[].enabled | boolean | 是 | 否 | 是否启用 |
| items[].review_status | string | 是 | 否 | 审核状态 |
| items[].redirect_url | string | 是 | 否 | 服务端签名跳转地址 |
~~~text
event: course_cards
data: {"schema_version":"2.0","request_id":"req-001","sequence":6,"items":[{"course_id":"course-ai-001","name":"AI工具入门","direction":"AI基础","description":"适合零基础用户","stage":"入门","source_platform":"小鹅通","series_name":null,"enabled":true,"review_status":"approved","redirect_url":"https://example.com/r/xxxxx"}]}
~~~
course_recommendation_note 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| content | string | 是 | 否 |
~~~text
event: course_recommendation_note
data: {"schema_version":"2.0","request_id":"req-001","sequence":7,"content":"该课程与已确认的学习目标匹配。"}
~~~
message_end 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| conversation_id | string | 是 | 否 |
| message_id | string | 是 | 否 |
| dify_message_id | string | 是 | 是 |
| task_id | string | 是 | 是 |
| status | string | 是 | 否 |
| first_token_ms | integer | 是 | 是 |
| total_ms | integer | 是 | 否 |
| token_usage | object | 是 | 否 |
~~~text
event: message_end
data: {"schema_version":"2.0","request_id":"req-001","sequence":8,"conversation_id":"conv-001","message_id":"msg-001","dify_message_id":"external-msg-001","task_id":"task-001","status":"completed","first_token_ms":620,"total_ms":4820,"token_usage":{"total_tokens":128}}
~~~
error 出参：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| code | string | 是 | 否 |
| message | string | 是 | 否 |
| retryable | boolean | 是 | 否 |
~~~text
event: error
data: {"schema_version":"2.0","request_id":"req-001","sequence":8,"code":"DIFY_UNAVAILABLE","message":"AI服务暂时不可用，请稍后重试","retryable":true}
~~~
注意：
- conversation_id 不能使用外部平台会话 ID。
- state 为 awaiting_route_confirmation 或 awaiting_stage_confirmation 时，不应出现课程卡片。
- course_cards 最多 5 门。
- 当前不返回课程级 expert_insight 事件。
- 同一会话有未完成生成时，接口可能返回 409 CONVERSATION_BUSY。
### 2.3 停止商业顾问生成
调用方：小程序后端  
对接级别：建议
~~~http
POST /internal/v1/business/chat/{task_id}/stop
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| task_id | string | 是 | 否 | status 事件返回的任务 ID |
无 Query 和 Body 参数。
入参案例：
~~~http
POST /internal/v1/business/chat/task-001/stop
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
~~~
出参案例：
~~~json
{"result":"success"}
~~~
注意：调用方只需要判断 HTTP 是否成功，不应依赖额外返回字段。
### 2.4 获取会话列表
调用方：小程序后端  
对接级别：必须
~~~http
GET /internal/v1/business/conversations
~~~
Query 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 约束 |
| --- | --- | --- | --- | --- | --- |
| cursor | string | 否 | 是 | null | 上一页 next_cursor |
| limit | integer | 否 | 否 | 20 | 1～100 |
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| items | array<object> | 是 | 否 |
| items[].conversation_id | string | 是 | 否 |
| items[].title | string | 是 | 否 |
| items[].last_active_at | datetime | 是 | 否 |
| items[].created_at | datetime | 是 | 否 |
| next_cursor | string | 是 | 是 |
入参案例：
~~~http
GET /internal/v1/business/conversations?limit=20
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
~~~
出参案例：
~~~json
{"items":[{"conversation_id":"conv-001","title":"AI 工具学习","last_active_at":"2026-07-28T09:30:00Z","created_at":"2026-07-28T09:00:00Z"}],"next_cursor":null}
~~~
### 2.5 获取会话消息
调用方：小程序后端  
对接级别：必须
~~~http
GET /internal/v1/business/conversations/{conversation_id}/messages
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 |
| --- | --- | --- | --- |
| conversation_id | string | 是 | 否 |
Query 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 约束 |
| --- | --- | --- | --- | --- | --- |
| cursor | string | 否 | 是 | null | 上一页 next_cursor |
| limit | integer | 否 | 否 | 20 | 1～100 |
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| items | array<object> | 是 | 否 |
| items[].message_id | string | 是 | 否 |
| items[].role | string | 是 | 否 |
| items[].content | string | 是 | 否 |
| items[].status | string | 是 | 否 |
| items[].recommendation | object | 是 | 否 |
| items[].retriever_resources | array<object> | 是 | 是 |
| items[].created_at | datetime | 是 | 否 |
| next_cursor | string | 是 | 是 |
入参案例：
~~~http
GET /internal/v1/business/conversations/conv-001/messages?limit=20
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
~~~
出参案例：
~~~json
{"items":[{"message_id":"msg-001","role":"assistant","content":"建议先从入门课程开始。","status":"completed","recommendation":{},"retriever_resources":[],"created_at":"2026-07-28T09:00:05Z"}],"next_cursor":null}
~~~
### 2.6 重命名会话
调用方：小程序后端  
对接级别：按需
~~~http
PATCH /internal/v1/business/conversations/{conversation_id}
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| title | string | 是 | 否 | 去除首尾空白后 1～120 字符 |
入参案例：
~~~http
PATCH /internal/v1/business/conversations/conv-001
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
Content-Type: application/json
{"title":"AI 工具入门计划"}
~~~
出参案例：
~~~json
{"conversation_id":"conv-001","title":"AI 工具入门计划"}
~~~
### 2.7 删除会话
调用方：小程序后端  
对接级别：必须
~~~http
DELETE /internal/v1/business/conversations/{conversation_id}
~~~
无 Query 和 Body 参数。
入参案例：
~~~http
DELETE /internal/v1/business/conversations/conv-001
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
~~~
出参案例：
~~~json
{"deleted":true}
~~~
### 2.8 消息反馈
调用方：小程序后端  
对接级别：按需
~~~http
POST /internal/v1/business/messages/{message_id}/feedback
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| rating | string | 是 | 是 | like、dislike 或 null |
| content | string | 否 | 否 | 默认空字符串，最长 1000 |
入参案例：
~~~http
POST /internal/v1/business/messages/msg-001/feedback
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
Content-Type: application/json
{"rating":"like","content":"回答比较清楚"}
~~~
出参案例：
~~~json
{"result":"success"}
~~~
### 2.9 朋友圈顾问
调用方：小程序后端  
对接级别：必须  
响应类型：SSE
~~~http
POST /internal/v1/tools/moments/run
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 最大长度 |
| --- | --- | --- | --- | --- |
| inputs | object | 是 | 否 | - |
| inputs.request | string | 是 | 否 | 12000 |
| inputs.source_material | string | 否 | 否 | 24000 |
| inputs.audience | string | 否 | 否 | 500 |
| inputs.tone | string | 否 | 否 | 500 |
| inputs.constraints | string | 否 | 否 | 3000 |
入参案例：
~~~http
POST /internal/v1/tools/moments/run
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
Content-Type: application/json
{"inputs":{"request":"写一条朋友圈","source_material":"今天学习了 AI 工作流","audience":"创业者","tone":"自然","constraints":"不要夸张"}}
~~~
返回事件：request_started、status、answer_delta、message_end、error。
工具 message_end 字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| task_id | string | 是 | 是 |
| first_token_ms | integer | 是 | 是 |
| total_ms | integer | 是 | 否 |
| elapsed_time | number | 否 | 是 |
| total_tokens | integer | 否 | 是 |
| total_steps | integer | 否 | 是 |
出参案例：
~~~text
event: message_end
data: {"schema_version":"2.0","request_id":"req-001","sequence":5,"task_id":"task-001","first_token_ms":500,"total_ms":2500,"elapsed_time":2.3,"total_tokens":120,"total_steps":4}
~~~
### 2.10 日常写作
调用方：小程序后端  
对接级别：必须  
响应类型：SSE
~~~http
POST /internal/v1/tools/writing/run
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 最大长度 |
| --- | --- | --- | --- | --- |
| inputs | object | 是 | 否 | - |
| inputs.request | string | 是 | 否 | 12000 |
| inputs.source_text | string | 否 | 否 | 30000 |
| inputs.audience | string | 否 | 否 | 500 |
| inputs.tone | string | 否 | 否 | 500 |
| inputs.format | string | 否 | 否 | 500 |
| inputs.constraints | string | 否 | 否 | 3000 |
入参案例：
~~~http
POST /internal/v1/tools/writing/run
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
Content-Type: application/json
{"inputs":{"request":"改写成正式邮件","source_text":"原始内容","audience":"客户","tone":"正式","format":"邮件","constraints":"保持简洁"}}
~~~
返回事件和字段与朋友圈顾问一致。
### 2.11 停止工具生成
调用方：小程序后端  
对接级别：建议
~~~http
POST /internal/v1/tools/{task_id}/stop
~~~
入参案例：
~~~http
POST /internal/v1/tools/task-001/stop
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: user-001
~~~
出参案例：
~~~json
{"result":"success"}
~~~
### 2.12 课程跳转
调用方：小程序或小程序后端  
对接级别：通过 course_cards.redirect_url 使用
~~~http
GET /r/{token}
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| token | string | 是 | 否 | 服务端签名 Token |
成功响应：
| HTTP 状态 | Header | 说明 |
| --- | --- | --- |
| 302 | Location | 课程正式地址 |
入参案例：
~~~http
GET /r/<SIGNED_TOKEN>
~~~
注意：
- 调用方不自行生成或解析 token。
- 应直接打开 course_cards.items[].redirect_url。
- 不存在单独的课程点击上报接口。
## 3. AI 管理端接口
本章接口都需要 X-Internal-Token 和 X-Admin-Token。
### 3.1 管理会话列表
~~~http
GET /internal/v1/admin/conversations
~~~
Query 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 约束 |
| --- | --- | --- | --- | --- | --- |
| user_id | string | 否 | 是 | null | 最长 255 |
| user_name | string | 否 | 是 | null | 最长 120 |
| limit | integer | 否 | 否 | 50 | 1～200 |
注意：同时提供 user_id 和 user_name 时，以 user_id 为准。
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| items | array<object> | 是 | 否 |
| items[].conversation_id | string | 是 | 否 |
| items[].user_id | string | 是 | 是 |
| items[].user_name | string | 是 | 否 |
| items[].title | string | 是 | 否 |
| items[].summary | string | 是 | 否 |
| items[].summary_gap | boolean | 是 | 否 |
| items[].last_active_at | datetime | 是 | 否 |
| items[].recommended_courses | array<object> | 是 | 否 |
| items[].professional_insight | string | 是 | 否 |
| items[].recommendation_note | string | 是 | 否 |
| items[].token_usage | integer | 是 | 否 |
| items[].first_token_ms | integer | 是 | 是 |
| items[].total_ms | integer | 是 | 是 |
| next_cursor | null | 是 | 是 |
`recommended_courses`、`professional_insight`、`recommendation_note`、token 和耗时取最近一条助手消息；无课程返回 `[]`，无见解或推荐理由返回空字符串。管理接口不返回内部 `user_hash`。迁移前未保存原始用户 ID 的历史记录返回空 `user_id`，用户再次聊天后补齐。
入参案例：
~~~http
GET /internal/v1/admin/conversations?user_name=张三&limit=50
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"conversation_id":"conv-001","user_id":"user-001","user_name":"张三","title":"AI 工具学习","summary":"用户希望学习 AI 工具。","summary_gap":false,"last_active_at":"2026-07-28T09:30:00Z","recommended_courses":[{"course_id":"course-ai-001","name":"AI 工具入门"}],"professional_insight":"","recommendation_note":"适合从基础工具开始。","token_usage":120,"first_token_ms":500,"total_ms":2500}],"next_cursor":null}
~~~
### 3.2 管理会话详情
~~~http
GET /internal/v1/admin/conversations/{conversation_id}
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 |
| --- | --- | --- | --- |
| conversation_id | string | 是 | 否 |
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| conversation_id | string | 是 | 否 |
| user_id | string | 是 | 是 |
| user_name | string | 是 | 否 |
| title | string | 是 | 否 |
| summary | string | 是 | 否 |
| recommended_courses | array<object> | 是 | 否 |
| professional_insight | string | 是 | 否 |
| recommendation_note | string | 是 | 否 |
| token_usage | integer | 是 | 否 |
| first_token_ms | integer | 是 | 是 |
| total_ms | integer | 是 | 是 |
| messages | array<object> | 是 | 否 |
| messages[].message_id | string | 是 | 否 |
| messages[].role | string | 是 | 否 |
| messages[].content | string | 是 | 否 |
| messages[].status | string | 是 | 否 |
| messages[].recommended_courses | array<object> | 是 | 否 |
| messages[].professional_insight | string | 是 | 否 |
| messages[].recommendation_note | string | 是 | 否 |
| messages[].token_usage | integer | 是 | 否 |
| messages[].first_token_ms | integer | 是 | 是 |
| messages[].total_ms | integer | 是 | 是 |
| messages[].recommendation | object | 是 | 是 |
| messages[].retriever_resources | array<object> | 是 | 是 |
| messages[].feedback | object | 是 | 是 |
| messages[].dify_message_id | string | 是 | 是 |
| messages[].dify_task_id | string | 是 | 是 |
| messages[].created_at | datetime | 是 | 否 |
入参案例：
~~~http
GET /internal/v1/admin/conversations/conv-001
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"conversation_id":"conv-001","user_id":"user-001","user_name":"张三","title":"AI 工具学习","summary":"用户希望学习 AI 工具。","recommended_courses":[{"course_id":"course-ai-001","name":"AI 工具入门"}],"professional_insight":"","recommendation_note":"适合从基础工具开始。","token_usage":120,"first_token_ms":500,"total_ms":2500,"messages":[{"message_id":"msg-001","role":"assistant","content":"建议先从入门开始。","status":"completed","recommended_courses":[{"course_id":"course-ai-001","name":"AI 工具入门"}],"professional_insight":"","recommendation_note":"适合从基础工具开始。","token_usage":120,"first_token_ms":500,"total_ms":2500,"recommendation":{},"retriever_resources":[],"feedback":null,"dify_message_id":"external-msg-001","dify_task_id":"task-001","created_at":"2026-07-28T09:00:05Z"}]}
~~~
### 3.3 课程点击统计
对接级别：按需
~~~http
GET /internal/v1/admin/course-clicks
~~~
Query 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 约束 |
| --- | --- | --- | --- | --- | --- |
| course_id | string | 否 | 是 | null | 课程 ID |
| from_date | date | 否 | 是 | null | YYYY-MM-DD |
| to_date | date | 否 | 是 | null | YYYY-MM-DD |
| page | integer | 否 | 否 | 1 | 大于等于 1 |
| limit | integer | 否 | 否 | 50 | 1～100 |
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| items | array<object> | 是 |
| items[].course_id | string | 是 |
| items[].course_name | string | 是 |
| items[].date | date | 是 |
| items[].clicks | integer | 是 |
| items[].unique_users | integer | 是 |
| items[].live | boolean | 否 |
| page | integer | 是 |
| limit | integer | 是 |
| total | integer | 是 |
| total_clicks | integer | 是 |
| total_unique_users | integer | 是 |
| course_totals | array<object> | 是 |
入参案例：
~~~http
GET /internal/v1/admin/course-clicks?course_id=course-ai-001&from_date=2026-07-01&to_date=2026-07-28&page=1&limit=50
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"course_id":"course-ai-001","course_name":"AI工具入门","date":"2026-07-28","clicks":12,"unique_users":9,"live":true}],"page":1,"limit":50,"total":1,"total_clicks":12,"total_unique_users":9,"course_totals":[{"course_id":"course-ai-001","course_name":"AI工具入门","clicks":12,"unique_users":9}]}
~~~
### 3.4 课程与路线目录
~~~http
GET /internal/v1/admin/catalog
~~~
无 Path、Query 和 Body 参数。
课程出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| courses | array<object> | 是 | 否 |
| courses[].course_id | string | 是 | 否 |
| courses[].name | string | 是 | 否 |
| courses[].direction | string | 是 | 否 |
| courses[].stage | string | 是 | 否 |
| courses[].source_platform | string | 是 | 否 |
| courses[].series_name | string | 是 | 是 |
| courses[].published_at | date | 是 | 是 |
| courses[].first_imported_at | datetime | 是 | 是 |
| courses[].prerequisite_course_ids | array<string> | 是 | 否 |
| courses[].enabled | boolean | 是 | 否 |
| courses[].review_status | string | 是 | 否 |
| courses[].optional_data | object | 是 | 否 |
路线出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| routes | array<object> | 是 | 否 |
| routes[].route_id | string | 是 | 否 |
| routes[].name | string | 是 | 否 |
| routes[].direction | string | 是 | 否 |
| routes[].target | string | 是 | 否 |
| routes[].enabled | boolean | 是 | 否 |
| routes[].review_status | string | 是 | 否 |
| routes[].stages | array<object> | 是 | 否 |
| routes[].stages[].stage_id | string | 是 | 否 |
| routes[].stages[].name | string | 是 | 否 |
| routes[].stages[].order | integer | 是 | 否 |
| routes[].stages[].course_ids | array<string> | 是 | 否 |
入参案例：
~~~http
GET /internal/v1/admin/catalog
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"courses":[{"course_id":"course-ai-001","name":"AI工具入门","direction":"AI基础","stage":"入门","source_platform":"小鹅通","series_name":null,"published_at":null,"first_imported_at":"2026-07-29T08:00:00+00:00","prerequisite_course_ids":[],"enabled":true,"review_status":"approved","optional_data":{}}],"routes":[{"route_id":"route-ai-basic","name":"AI入门路线","direction":"AI基础","target":"完成AI基础使用","enabled":true,"review_status":"approved","stages":[{"stage_id":"stage-1","name":"入门","order":1,"course_ids":["course-ai-001"]}]}]}
~~~
注意：当前目录接口不返回课程 description 和 official_url。
### 3.5 删除用户 AI 数据
对接级别：按需
~~~http
DELETE /internal/v1/admin/users/{raw_user_id}/ai-data
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 |
| --- | --- | --- | --- |
| raw_user_id | string | 是 | 否 |
入参案例：
~~~http
DELETE /internal/v1/admin/users/user-001/ai-data
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| deleted | boolean | 是 |
| conversation_count | integer | 是 |
| external_deletions_queued | integer | 是 |
出参案例：
~~~json
{"deleted":true,"conversation_count":3,"external_deletions_queued":3}
~~~
### 3.6 查询历史保留配置
~~~http
GET /internal/v1/admin/settings/retention
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| history_retention_days | integer | 是 |
| cleanup_hour | integer | 是 |
| cleanup_minute | integer | 是 |
入参案例：
~~~http
GET /internal/v1/admin/settings/retention
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"history_retention_days":7,"cleanup_hour":2,"cleanup_minute":0}
~~~
### 3.7 更新历史保留配置
~~~http
PUT /internal/v1/admin/settings/retention
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| history_retention_days | integer | 是 | 否 | 1～365 |
入参案例：
~~~http
PUT /internal/v1/admin/settings/retention
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: application/json
{"history_retention_days":14}
~~~
出参结构与查询接口相同。
### 3.8 查询输出风格
~~~http
GET /internal/v1/admin/style-settings
~~~
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| items | array<object> | 是 | 否 |
| items[].code | string | 是 | 否 |
| items[].label | string | 是 | 否 |
| items[].enabled | boolean | 是 | 否 |
| items[].style_prompt | string | 是 | 否 |
| items[].version | integer | 是 | 否 |
| items[].updated_at | datetime | 是 | 否 |
| items[].updated_by | string | 是 | 否 |
入参案例：
~~~http
GET /internal/v1/admin/style-settings
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"code":"business_answer","label":"商业顾问主回答风格","enabled":true,"style_prompt":"回答直接、清楚，不空泛。","version":3,"updated_at":"2026-07-28T10:00:00Z","updated_by":"system"}]}
~~~
注意：business_expert_insight 可能作为历史兼容项返回；新管理页面应隐藏或忽略。
### 3.9 更新输出风格
~~~http
PUT /internal/v1/admin/style-settings/{style_code}
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| style_code | string | 是 | 否 | 接口接受：business_answer、business_expert_insight、moments、writing |
注意：`business_expert_insight` 是历史兼容项，新版 AI 管理页面不提供该项的新配置入口；课程推荐不依赖该输出风格。
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| enabled | boolean | 是 | 否 | 是否启用 |
| style_prompt | string | 否 | 否 | 默认空字符串，最长 4000 |
入参案例：
~~~http
PUT /internal/v1/admin/style-settings/moments
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: application/json
{"enabled":true,"style_prompt":"自然、克制、避免夸张表达。"}
~~~
出参案例：
~~~json
{"code":"moments","label":"朋友圈输出风格","enabled":true,"style_prompt":"自然、克制、避免夸张表达。","version":2,"updated_at":"2026-07-28T10:00:00Z","updated_by":"system"}
~~~
### 3.10 获取知识库概览
~~~http
GET /internal/v1/admin/knowledge-bases
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| items | array<object> | 是 |
| items[].code | string | 是 |
| items[].name | string | 是 |
| items[].document_count | integer | 是 |
| items[].enabled_document_count | integer | 是 |
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"code":"general","name":"通用知识库","document_count":10,"enabled_document_count":8},{"code":"course","name":"课程知识库","document_count":20,"enabled_document_count":20}]}
~~~
### 3.11 获取知识库文档
~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/documents
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 | 允许值 |
| --- | --- | --- | --- | --- |
| kb_code | string | 是 | 否 | general、course |
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| items | array<object> | 是 | 否 |
| items[].document_id | string | 是 | 否 |
| items[].name | string | 是 | 否 |
| items[].enabled | boolean | 是 | 否 |
| items[].indexing_status | string | 是 | 是 |
| items[].word_count | integer | 是 | 否 |
| items[].segment_count | integer | 是 | 否 |
| items[].created_at | integer/datetime | 是 | 是 |
| items[].updated_at | integer/datetime | 是 | 是 |
| items[].source_sha256 | string | 是 | 否 |
| items[].source_filename | string | 是 | 否 |
| next_cursor | null | 是 | 是 |
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases/course/documents
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"document_id":"doc-001","name":"AI工具入门.md","enabled":true,"indexing_status":"completed","word_count":1024,"segment_count":8,"created_at":1722150000,"updated_at":1722150300,"source_sha256":"abcdef123456","source_filename":"课程资料.md"}],"next_cursor":null}
~~~
### 3.12 获取清洗策略
~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| items | array<object> | 是 |
| items[].code | string | 是 |
| items[].display_name | string | 是 |
| items[].supported_extensions | array<string> | 是 |
| items[].knowledge_base_code | string | 是 |
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases/course/cleaning-strategies
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"code":"course_information","display_name":"课程信息","supported_extensions":[".docx",".markdown",".md",".txt"],"knowledge_base_code":"course"},{"code":"learning_route","display_name":"学习路线","supported_extensions":[".docx",".markdown",".md",".txt"],"knowledge_base_code":"course"}]}
~~~
### 3.13 创建上传批次
~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches
Content-Type: multipart/form-data
~~~
Path 参数：
| 字段 | 类型 | 必填 | 可空 | 允许值 |
| --- | --- | --- | --- | --- |
| kb_code | string | 是 | 否 | general、course |
Form 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- | --- |
| files | array<file> | 是 | 否 | - | 一个或多个文件 |
| upload_mode | string | 否 | 否 | cleaned | cleaned 或 raw |
| strategy_code | string | 否 | 否 | standard_document | 建议显式传入查询接口返回的 code |
| common_metadata | string | 否 | 否 | {} | JSON 对象字符串 |
common_metadata 允许字段：
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| document_type | string | 文档类型 |
| review_status | string | 审核状态 |
| course_id | string | 课程 ID |
| route_id | string | 学习路线 ID |
| direction | string | 方向 |
| stage | string | 课程全局阶段 |
| source_platform | string | 课程来源平台 |
| series_name | string | 系列名称 |
| status | string | 业务状态 |
入参案例：
~~~http
POST /internal/v1/admin/knowledge-bases/course/upload-batches
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: multipart/form-data
files: <course-information.md>
upload_mode: cleaned
strategy_code: course_information
common_metadata: {"document_type":"course","direction":"AI基础","stage":"入门","source_platform":"小鹅通","status":"active"}
~~~
批次出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| batch_id | string | 是 | 否 |
| strategy_code | string | 是 | 否 |
| knowledge_base_code | string | 是 | 否 |
| upload_mode | string | 是 | 否 |
| status | string | 是 | 否 |
| progress | integer | 是 | 否 |
| next_poll_after_seconds | integer | 是 | 否 |
| all_succeeded | boolean | 是 | 否 |
| all_processed_without_error | boolean | 是 | 否 |
| total | integer | 是 | 否 |
| succeeded | integer | 是 | 否 |
| failed | integer | 是 | 否 |
| excluded | integer | 是 | 否 |
| review_required | integer | 是 | 否 |
| mixed | integer | 是 | 否 |
| file_counts | object | 是 | 否 |
| item_counts | object | 是 | 否 |
| files | array<object> | 是 | 否 |
files 项字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| file_id | string | 是 | 否 |
| original_filename | string | 是 | 否 |
| status | string | 是 | 否 |
| outcome | string | 是 | 否 |
| stage | string | 是 | 否 |
| error_code | string | 是 | 是 |
| error_message | string | 是 | 是 |
| updated_at | datetime | 是 | 否 |
| source_expires_at | datetime | 是 | 是 |
| source_deleted_at | datetime | 是 | 是 |
| item_counts | object | 是 | 否 |
| item_total | integer | 是 | 否 |
| item_terminal | integer | 是 | 否 |
| progress | integer | 是 | 否 |
| items | array<object> | 是 | 否 |
出参案例：
~~~json
{"batch_id":"batch-001","strategy_code":"course_information","knowledge_base_code":"course","upload_mode":"cleaned","status":"processing","progress":0,"next_poll_after_seconds":2,"all_succeeded":false,"all_processed_without_error":false,"total":1,"succeeded":0,"failed":0,"excluded":0,"review_required":0,"mixed":0,"file_counts":{"succeeded":0,"failed":0,"excluded":0,"review_required":0,"mixed":0},"item_counts":{"pending":0,"processing":0,"succeeded":0,"excluded":0,"review_required":0,"failed":0},"files":[{"file_id":"file-001","original_filename":"course-information.md","status":"pending","outcome":"pending","stage":"upload","error_code":null,"error_message":null,"updated_at":"2026-07-29T10:00:00Z","source_expires_at":"2026-08-05T10:00:00Z","source_deleted_at":null,"item_counts":{"pending":0,"processing":0,"succeeded":0,"excluded":0,"review_required":0,"failed":0},"item_total":0,"item_terminal":0,"progress":0,"items":[]}]}
~~~
注意：
- 详细文件内容格式不在本文定义，参见资料模板和清洗策略说明。
- common_metadata 必须是合法 JSON 对象字符串。
- strategy_code 与 kb_code 必须匹配。
- 每个文件独立返回处理结果。
### 3.14 查询上传批次
~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}
~~~
Path 参数：kb_code、batch_id 均必填。
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参结构与创建上传批次相同。
注意：status=processing 时继续按 next_poll_after_seconds 轮询；status=completed 时根据 all_succeeded、failed、review_required、mixed 判断结果。
### 3.15 获取文件条目列表
~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items
~~~
Query 参数：
| 字段 | 类型 | 必填 | 可空 | 默认值 | 说明 |
| --- | --- | --- | --- | --- | --- |
| status | string | 否 | 是 | null | 状态过滤 |
| cursor | string | 否 | 是 | null | 上一页 next_cursor |
| limit | integer | 否 | 否 | 50 | 服务端限制为 1～200 |
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| file_id | string | 是 | 否 |
| items | array<object> | 是 | 否 |
| next_cursor | string | 是 | 是 |
| total | integer | 是 | 否 |
条目字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| item_id | string | 是 | 否 |
| sequence | integer | 是 | 否 |
| title | string | 是 | 否 |
| document_key | string | 是 | 否 |
| status | string | 是 | 否 |
| stage | string | 是 | 否 |
| error_code | string | 是 | 是 |
| error_message | string | 是 | 是 |
| retryable | boolean | 是 | 否 |
| review_revision | integer | 是 | 否 |
| warnings | array | 是 | 否 |
| metadata | object | 是 | 否 |
| chunking | object | 是 | 否 |
| dify_document_id | string | 是 | 是 |
| remote_parent_count | integer | 是 | 是 |
| remote_child_count | integer | 是 | 是 |
| updated_at | datetime | 是 | 否 |
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001/files/file-001/items?limit=50
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"file_id":"file-001","items":[{"item_id":"item-001","sequence":1,"title":"AI工具入门","document_key":"file-001:1:abcdef","status":"succeeded","stage":"completed","error_code":null,"error_message":null,"retryable":false,"review_revision":0,"warnings":[],"metadata":{"document_type":"course","course_id":"course-ai-001"},"chunking":{},"dify_document_id":"doc-001","remote_parent_count":1,"remote_child_count":1,"updated_at":"2026-07-29T10:00:05Z"}],"next_cursor":null,"total":1}
~~~
### 3.16 获取条目详情
~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}
~~~
出参字段为条目列表字段，并增加：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| cleaned_markdown | string | 是 | 是 |
| audit | object | 是 | 否 |
入参案例：
~~~http
GET /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001/files/file-001/items/item-001
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"item_id":"item-001","sequence":1,"title":"AI工具入门","document_key":"file-001:1:abcdef","status":"succeeded","stage":"completed","error_code":null,"error_message":null,"retryable":false,"review_revision":0,"warnings":[],"metadata":{"document_type":"course","course_id":"course-ai-001"},"chunking":{},"dify_document_id":"doc-001","remote_parent_count":1,"remote_child_count":1,"updated_at":"2026-07-29T10:00:05Z","cleaned_markdown":"# AI工具入门","audit":{}}
~~~
### 3.17 重试失败文件
~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry
~~~
无 Query 和 Body 参数。
入参案例：
~~~http
POST /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001/files/file-001/retry
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"file_id":"file-001","status":"pending"}
~~~
注意：没有失败条目或文件不允许重试；待复核条目应调用复核接口。
### 3.18 重试失败条目
~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry
~~~
入参案例：
~~~http
POST /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001/files/file-001/items/item-001/retry
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"item_id":"item-001","status":"pending"}
~~~
注意：仅 status=failed 的条目可以重试。
### 3.19 提交条目复核决定
~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions
Content-Type: multipart/form-data
~~~
Form 参数：
| 字段 | 类型 | 必填 | 可空 | 说明 |
| --- | --- | --- | --- | --- |
| action | string | 是 | 否 | approve、replace_and_approve、exclude |
| expected_revision | integer | 是 | 否 | 当前 review_revision，大于等于 0 |
| reason | string | 否 | 否 | 默认空字符串，最长 1000 |
| metadata_patch | string | 否 | 否 | JSON 对象字符串，默认 {} |
| markdown_file | file | 条件必填 | 是 | replace_and_approve 时必须提供 |
入参案例：
~~~http
POST /internal/v1/admin/knowledge-bases/course/upload-batches/batch-001/files/file-001/items/item-001/review-decisions
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: multipart/form-data
action: approve
expected_revision: 0
reason: 已确认内容
metadata_patch: {}
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| item_id | string | 是 |
| status | string | 是 |
| review_revision | integer | 是 |
出参案例：
~~~json
{"item_id":"item-001","status":"pending","review_revision":1}
~~~
注意：expected_revision 不一致返回 409；replace_and_approve 必须上传非空 Markdown 文件。
### 3.20 启用或停用知识库文档
~~~http
PATCH /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 |
| --- | --- | --- | --- |
| enabled | boolean | 是 | 否 |
入参案例：
~~~http
PATCH /internal/v1/admin/knowledge-bases/course/documents/doc-001/status
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: application/json
{"enabled":false}
~~~
出参字段：
| 字段 | 类型 | 一定返回 | 可空 |
| --- | --- | --- | --- |
| document_id | string | 是 | 否 |
| enabled | boolean | 是 | 否 |
| catalog_record | object | 是 | 是 |
| catalog_record.record_type | string | 条件返回 | 否 |
| catalog_record.record_id | string | 条件返回 | 否 |
出参案例：
~~~json
{"document_id":"doc-001","enabled":false,"catalog_record":{"record_type":"course","record_id":"course-ai-001"}}
~~~
注意：未审核的课程或路线文档不能启用。
### 3.21 删除知识库文档
~~~http
DELETE /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}
~~~
Body 参数：
| 字段 | 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| confirm_name | string | 是 | 否 | 必须与当前文档名称完全一致，最长 500 |
入参案例：
~~~http
DELETE /internal/v1/admin/knowledge-bases/general/documents/doc-001
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
Content-Type: application/json
{"confirm_name":"测试文档.md"}
~~~
出参案例：
~~~json
{"deleted":true,"document_id":"doc-001"}
~~~
注意：课程或学习路线目录文档不能通过此接口直接删除，可能返回 409。
## 4. 附录接口
### 4.1 存活检查
业务页面无需对接，供运维使用。
~~~http
GET /health/live
~~~
出参案例：
~~~json
{"status":"ok"}
~~~
### 4.2 就绪检查
业务页面无需对接，供运维使用。
~~~http
GET /health/ready
~~~
出参字段：
| 字段 | 类型 | 一定返回 |
| --- | --- | --- |
| status | string | 是 |
| database | boolean | 是 |
| redis | boolean | 是 |
| dify_apps_configured | boolean | 是 |
| dify_knowledge_configured | boolean | 是 |
| dify_reachable | boolean | 是 |
成功时 HTTP 200；未就绪时 HTTP 503。
入参案例：
~~~http
GET /health/ready
~~~
出参案例：
~~~json
{"status":"ok","database":true,"redis":true,"dify_apps_configured":true,"dify_knowledge_configured":true,"dify_reachable":true}
~~~
### 4.3 日志列表
AI 管理业务主流程无需对接，运维页面按需使用。
~~~http
GET /internal/v1/admin/logs
~~~
Query 参数：from_time、to_time、level、component、event_code、status、request_id、resource_id、page、limit。page 默认 1；limit 默认 50，范围 1～100。
出参包含 items、page、limit、total。日志项包含 log_id、occurred_at、level、category、component、event_code、status、request_id、actor_hash、resource_type、resource_id、message、duration_ms、build_version。
入参案例：
~~~http
GET /internal/v1/admin/logs?level=ERROR&page=1&limit=50
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"items":[{"log_id":"log-001","occurred_at":"2026-07-29T10:00:00Z","level":"ERROR","category":"application","component":"knowledge","event_code":"UPLOAD_FAILED","status":"failed","request_id":"req-001","actor_hash":"actor-xxxxx","resource_type":"cleaning_batch","resource_id":"batch-001","message":"文件处理失败","duration_ms":850,"build_version":"2026.07.29"}],"page":1,"limit":50,"total":1}
~~~
### 4.4 日志详情
~~~http
GET /internal/v1/admin/logs/{log_id}
~~~
返回日志列表的单项字段，并增加 details 对象。
入参案例：
~~~http
GET /internal/v1/admin/logs/log-001
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
~~~
出参案例：
~~~json
{"log_id":"log-001","occurred_at":"2026-07-29T10:00:00Z","level":"ERROR","category":"application","component":"knowledge","event_code":"UPLOAD_FAILED","status":"failed","request_id":"req-001","actor_hash":"actor-xxxxx","resource_type":"cleaning_batch","resource_id":"batch-001","message":"文件处理失败","duration_ms":850,"build_version":"2026.07.29","details":{"error_code":"FILE_PARSE_FAILED"}}
~~~
### 4.5 历史兼容清洗接口
以下接口仅为历史兼容，新系统不对接：
~~~http
GET  /internal/v1/knowledge/cleaning-strategies
POST /internal/v1/knowledge/cleaning-batches
GET  /internal/v1/knowledge/cleaning-batches/{batch_id}
POST /internal/v1/knowledge/cleaning-batches/{batch_id}/files/{file_id}/retry
~~~
新 AI 管理端统一使用第 3.12～3.21 节接口。
## 5. 文档外内容
以下内容不属于本接口契约：
- 课程信息、学习路线及其他资料的详细文件模板
- 清洗策略业务规则
- 服务内部技术设计
- 部署、发布和回滚流程
文件模板和资料字段要求参见：
- 清洗策略接入说明.md
- 知识库问答和课程推荐资料提供要求.docx
