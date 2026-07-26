# API 与 SSE 说明

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
course_cards
learning_route
expert_insight
message_end
error
```

`answer_delta`：

```json
{"delta":"本次新增文本"}
```

`message_end` 包含本地会话/消息 ID、Dify message/task ID、状态、首 Token、总耗时和 Token 用量。若流中收到 `error`，不能把此前部分文本标记为完整回答。

同一会话已有生成时返回：

```http
409 CONVERSATION_BUSY
```

## 无状态工具

```http
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

## 入口配置

```http
GET /internal/v1/entry-config
```

服务从 Dify 参数接口聚合开场白和推荐问题，并声明入口是否支持历史、反馈和课程。

## 文件清洗

```http
GET  /internal/v1/knowledge/cleaning-strategies
POST /internal/v1/knowledge/cleaning-batches
GET  /internal/v1/knowledge/cleaning-batches/{batch_id}
POST /internal/v1/knowledge/cleaning-batches/{batch_id}/files/{file_id}/retry
```

上传为 `multipart/form-data`：

```text
strategy_code=test_markdown
knowledge_base_code=general
common_metadata={"document_type":"test"}
files=<多个文件>
```

目标知识库代码只能是 `general` 或 `course`，不能传任意 Dataset ID。

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
