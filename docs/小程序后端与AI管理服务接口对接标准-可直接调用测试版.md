# 小程序后端与 AI 管理服务接口对接标准（可直接调用测试版）

版本：2026-07-29

本文供小程序后端和 AI 管理端开发、联调、测试使用，提供调用路径、请求头、请求体、返回结构、可复制示例和失败处理方式。

原分级审阅版仍保留在：小程序后端与AI管理服务接口对接标准.md。本文件不改变任何 API、请求字段、返回字段或 SSE 事件。

## 0. 快速开始

### 0.1 测试变量

~~~bash
export AI_SERVICE_BASE_URL="http://127.0.0.1:8000"
export INTERNAL_TOKEN="替换为内部服务令牌"
export ADMIN_TOKEN="替换为管理员令牌"
export USER_ID="test-user-001"
~~~

Windows PowerShell：

~~~powershell
$baseUrl = "http://127.0.0.1:8000"
$internalToken = "替换为内部服务令牌"
$adminToken = "替换为管理员令牌"
$userId = "test-user-001"
~~~

本文所有尖括号变量都是占位符，不能原样写入生产配置。

### 0.2 首次验证

~~~bash
curl -i "$AI_SERVICE_BASE_URL/health/live"
curl -i "$AI_SERVICE_BASE_URL/health/ready"
curl -i -H "X-Internal-Token: $INTERNAL_TOKEN" \
  "$AI_SERVICE_BASE_URL/internal/v1/entry-config"
~~~

预期：存活检查返回 200 和 {"status":"ok"}；就绪检查返回 200 才继续联调。就绪检查返回 503 时先检查数据库、Redis、Dify 配置。

### 0.3 鉴权

用户接口请求头：

~~~http
X-Internal-Token: <INTERNAL_TOKEN>
X-User-Id: <USER_ID>
X-User-Name: <可选展示名>
X-Request-Id: <可选请求ID>
~~~

AI 管理接口请求头：

~~~http
X-Internal-Token: <INTERNAL_TOKEN>
X-Admin-Token: <ADMIN_TOKEN>
X-Request-Id: <可选请求ID>
~~~

健康检查不需要认证；课程卡片中的 redirect_url 是服务端生成的完整跳转地址，小程序不自行生成 Token。

小程序前端不得持有 Internal Token、Admin Token、Dify API Key 或 Dataset ID。

## 1. 统一返回和 SSE 规则

### 1.1 错误返回

~~~json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数不合法",
  "request_id": "req-001",
  "retryable": false
}
~~~

| HTTP 状态 | 常见 code | 处理方式 |
| --- | --- | --- |
| 400/422 | VALIDATION_ERROR | 修正参数或文件，不自动重试 |
| 401 | UNAUTHORIZED | 检查 Token，不向用户暴露 Token |
| 404 | NOT_FOUND、CONVERSATION_NOT_FOUND | 检查资源 ID 或知识库代码 |
| 409 | CONVERSATION_BUSY、CONFLICT | 等待当前任务或先执行复核 |
| 429 | DIFY_RATE_LIMITED | 退避后重试 |
| 500 | INTERNAL_ERROR | 记录 request_id 后排查 |
| 503 | DIFY_UNAVAILABLE、DIFY_TIMEOUT | 仅 retryable=true 时退避重试 |

### 1.2 SSE

商业顾问、朋友圈和写作接口返回 text/event-stream。命令行测试必须使用 curl -N：

~~~bash
curl -N -X POST "$AI_SERVICE_BASE_URL/internal/v1/business/chat" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d '{"query":"我想从零学习 AI 工具，应该怎么开始？","conversation_id":null}'
~~~

每个事件的 data 都是 JSON，并包含 schema_version、request_id、sequence。客户端按空行分隔 SSE 事件，不要按网络分片解析。收到 error 后，之前的 answer_delta 只能视为部分内容。

## 2. 小程序后端接口

### 2.1 入口配置（必须对接）

~~~http
GET /internal/v1/entry-config
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/entry-config" \
  -H "X-Internal-Token: $INTERNAL_TOKEN"
~~~

~~~json
{
  "entries": [
    {"code":"business_advisor","name":"商业顾问","opening_statement":"你好，我来帮你分析问题。","suggested_questions":["我现在适合学什么？"],"history":true,"feedback":true,"course_recommendation":true},
    {"code":"moments_advisor","name":"朋友圈顾问","opening_statement":"","suggested_questions":[],"history":false,"feedback":false,"course_recommendation":false},
    {"code":"writing_advisor","name":"日常写作","opening_statement":"","suggested_questions":[],"history":false,"feedback":false,"course_recommendation":false}
  ]
}
~~~

根据 code 渲染入口，不要写死推荐开关。

### 2.2 商业顾问聊天（必须对接）

~~~http
POST /internal/v1/business/chat
Content-Type: application/json
~~~

~~~json
{"query":"我想从零学习 AI 工具，应该怎么开始？","conversation_id":null}
~~~

新会话不传 conversation_id 或传 null；续聊只能传本服务返回的本地 conversation_id，不能传 Dify conversation_id。

~~~bash
curl -N -X POST "$AI_SERVICE_BASE_URL/internal/v1/business/chat" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d '{"query":"我想从零学习 AI 工具，应该怎么开始？","conversation_id":null}'
~~~

典型事件：

~~~text
event: request_started
data: {"schema_version":"2.0","request_id":"req-001","sequence":1,"conversation_id":"conv_001","message_id":"msg_001"}

event: status
data: {"schema_version":"2.0","request_id":"req-001","sequence":2,"code":"retrieving","message":"正在检索相关资料…"}

event: answer_delta
data: {"schema_version":"2.0","request_id":"req-001","sequence":3,"delta":"建议先从高频办公场景开始。"}

event: message_end
data: {"schema_version":"2.0","request_id":"req-001","sequence":4,"conversation_id":"conv_001","message_id":"msg_001","task_id":"task_001","status":"completed","first_token_ms":620,"total_ms":4820,"token_usage":{"total_tokens":128}}
~~~

路线和课程事件：

~~~text
event: learning_route
data: {"schema_version":"2.0","request_id":"req-001","sequence":5,"route_id":"route_ai_basic","name":"AI入门路线","direction":"AI基础","target":"完成AI基础使用","stages":[{"stage_id":"stage_1","name":"入门","order":1,"course_ids":["course_ai_001"]}],"current_stage_id":null,"next_stage_id":null,"state":"awaiting_route_confirmation","progress_status":"pending_route","confirmation_required":true}

event: course_cards
data: {"schema_version":"2.0","request_id":"req-001","sequence":6,"items":[{"course_id":"course_ai_001","name":"AI工具入门","direction":"AI基础","description":"适合零基础用户","enabled":true,"review_status":"approved","redirect_url":"https://example.com/r/xxxxx"}]}

event: course_recommendation_note
data: {"schema_version":"2.0","request_id":"req-001","sequence":7,"text":"该课程与已确认的入门目标匹配。"}
~~~

注意：

- awaiting_route_confirmation 或 awaiting_stage_confirmation 时，course_cards.items 必须为空。
- 只有启用且审核通过的课程和路线可以返回卡片。
- 当前阶段优先，其次下一阶段，最多 5 门且保持路线顺序。
- 没有官方路线时可推荐独立课程，但不得临时拼接路线。
- 当前不生成或渲染课程级 expert_insight。
- 同一会话生成中再次请求返回 409 CONVERSATION_BUSY。

### 2.3 停止商业顾问生成（建议对接）

~~~http
POST /internal/v1/business/chat/{task_id}/stop
~~~

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/business/chat/task_001/stop" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID"
~~~

成功：{"result":"success"}。

### 2.4 会话列表（必须对接）

~~~http
GET /internal/v1/business/conversations?cursor=<cursor>&limit=20
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/business/conversations?limit=20" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID"
~~~

~~~json
{"items":[{"conversation_id":"conv_001","title":"我想从零学习AI工具","last_active_at":"2026-07-28T09:30:00Z","created_at":"2026-07-28T09:00:00Z"}],"next_cursor":null}
~~~

### 2.5 消息列表（必须对接）

~~~http
GET /internal/v1/business/conversations/{conversation_id}/messages?cursor=<cursor>&limit=20
~~~

返回每条消息的 message_id、role、content、status、recommendation、retriever_resources、created_at，并返回 next_cursor。

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/business/conversations/conv_001/messages?limit=20" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID"
~~~

成功返回示例：

~~~json
{"items":[{"message_id":"msg_001","role":"assistant","content":"建议先从入门课程开始。","status":"completed","recommendation":{},"retriever_resources":[],"created_at":"2026-07-28T09:00:05Z"}],"next_cursor":null}
~~~

### 2.6 重命名会话（按需对接）

~~~http
PATCH /internal/v1/business/conversations/{conversation_id}
Content-Type: application/json
~~~

~~~bash
curl -s -X PATCH "$AI_SERVICE_BASE_URL/internal/v1/business/conversations/conv_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" -d '{"title":"AI 工具入门计划"}'
~~~

返回：{"conversation_id":"conv_001","title":"AI 工具入门计划"}。标题长度 1～120。

### 2.7 删除会话（必须对接）

~~~http
DELETE /internal/v1/business/conversations/{conversation_id}
~~~

~~~bash
curl -s -X DELETE "$AI_SERVICE_BASE_URL/internal/v1/business/conversations/conv_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID"
~~~

返回：{"deleted":true}。

### 2.8 消息反馈（按需对接）

~~~http
POST /internal/v1/business/messages/{message_id}/feedback
Content-Type: application/json
~~~

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/business/messages/msg_001/feedback" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" -d '{"rating":"like","content":"回答比较清楚"}'
~~~

rating 允许 like、dislike、null；成功返回 {"result":"success"}。

### 2.9 朋友圈顾问（必须对接）

~~~http
POST /internal/v1/tools/moments/run
Content-Type: application/json
~~~

~~~bash
curl -N -X POST "$AI_SERVICE_BASE_URL/internal/v1/tools/moments/run" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d '{"inputs":{"request":"写一条朋友圈","source_material":"今天学习了 AI 工作流","audience":"创业者","tone":"自然","constraints":"不要夸张"}}'
~~~

inputs.request 必填；source_material、audience、tone、constraints 可选。事件通常为 request_started、status、answer_delta、message_end、error。

### 2.10 日常写作（必须对接）

~~~http
POST /internal/v1/tools/writing/run
Content-Type: application/json
~~~

~~~bash
curl -N -X POST "$AI_SERVICE_BASE_URL/internal/v1/tools/writing/run" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d '{"inputs":{"request":"改写成更正式版本","source_text":"原始内容","audience":"客户","tone":"正式","format":"邮件","constraints":"保持简洁"}}'
~~~

inputs.request 必填；source_text、audience、tone、format、constraints 可选。

### 2.11 停止工具生成（建议对接）

~~~http
POST /internal/v1/tools/{task_id}/stop
~~~

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/tools/task_001/stop" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-User-Id: $USER_ID"
~~~

成功：{"result":"success"}。

### 2.12 课程跳转和点击统计（必须实现，无额外上报 API）

课程卡片中只使用完整 redirect_url：

~~~json
{"course_id":"course_ai_001","name":"AI工具入门","redirect_url":"https://example.com/r/xxxxx"}
~~~

用户点击 → 打开 redirect_url → 服务端记录有效点击 → 跳转真实课程地址。不要拼接 /r/{token}，不要改写参数，不要调用单独点击上报接口。

服务端跳转路由为：

~~~http
GET /r/{token}
~~~

小程序不直接拼接或调用这个路径，只打开服务端返回的完整 redirect_url。

## 3. AI 管理端接口

以下接口都需要同时携带 Internal Token 和 Admin Token。小程序前端和小程序后端不得调用本章接口；AI 管理页应由自己的服务端代理请求。

### 3.1 管理会话列表（必须对接）

~~~http
GET /internal/v1/admin/conversations?user_id=<user_id>&user_name=<user_name>&limit=50
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/conversations?limit=50" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回 `items[]` 中包含 `conversation_id`、原始 `user_id`、`user_name`、`title`、`summary`、`summary_gap`、`last_active_at`，以及最近一条助手消息的 `recommended_courses`、`professional_insight`、`recommendation_note`、`token_usage`、`first_token_ms`、`total_ms`，并返回 `next_cursor`。管理接口不返回内部 `user_hash`；历史记录没有原始用户 ID 时 `user_id` 为空。

### 3.2 管理会话详情（必须对接）

~~~http
GET /internal/v1/admin/conversations/{conversation_id}
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/conversations/conv_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回包含会话基本信息和 `messages[]`。顶层指标取最近一条助手消息；每条消息都包含 `recommended_courses`、`professional_insight`、`recommendation_note`、`token_usage`、`first_token_ms`、`total_ms`，并保留 `recommendation`、`retriever_resources`、`feedback`、`dify_message_id`、`dify_task_id`。推荐课程无值时为 `[]`，专业见解和推荐理由无值时为空字符串。

最小返回示例：

~~~json
{"conversation_id":"conv_001","user_id":"user_001","user_name":"张三","title":"我想从零学习AI工具","summary":"用户希望从零开始学习 AI 工具。","recommended_courses":[],"professional_insight":"","recommendation_note":"","token_usage":0,"first_token_ms":null,"total_ms":null,"messages":[]}
~~~

### 3.3 课程点击统计（按需对接）

~~~http
GET /internal/v1/admin/course-clicks?course_id=<course_id>&from_date=2026-07-01&to_date=2026-07-28&page=1&limit=50
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/course-clicks?course_id=course_ai_001&page=1&limit=50" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回 items、page、limit、total、total_clicks、total_unique_users、course_totals。小程序不调用此接口。

### 3.4 课程与学习路线目录（必须对接）

~~~http
GET /internal/v1/admin/catalog
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/catalog" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

课程关键字段：course_id、name、direction、description、stage、source_platform、series_name、published_at、first_imported_at、prerequisite_course_ids、enabled、review_status、optional_data。

路线关键字段：route_id、name、direction、target、enabled、review_status、stages[]；阶段包含 stage_id、name、order、course_ids。

### 3.5 删除用户 AI 数据（按需对接）

~~~http
DELETE /internal/v1/admin/users/{raw_user_id}/ai-data
~~~

~~~bash
curl -s -X DELETE "$AI_SERVICE_BASE_URL/internal/v1/admin/users/test-user-001/ai-data" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回：{"deleted":true,"conversation_count":3,"external_deletions_queued":3}。删除会话、消息、画像和路线进度，不删除课程 PV/UV 汇总。

### 3.6 历史保留配置（必须对接）

查询：

~~~http
GET /internal/v1/admin/settings/retention
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/settings/retention" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回：{"history_retention_days":7,"cleanup_hour":2,"cleanup_minute":0}。

更新：PUT /internal/v1/admin/settings/retention，JSON 为 {"history_retention_days":14}。

~~~bash
curl -s -X PUT "$AI_SERVICE_BASE_URL/internal/v1/admin/settings/retention" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"history_retention_days":14}'
~~~

范围 1～365；当前默认凌晨 2 点执行清理。

### 3.7 输出风格设置（必须对接有效配置项）

查询：

~~~http
GET /internal/v1/admin/style-settings
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/style-settings" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

更新：PUT /internal/v1/admin/style-settings/{style_code}，JSON 为 {"enabled":true,"style_prompt":"自然、克制、避免夸张表达。"}。

~~~bash
curl -s -X PUT "$AI_SERVICE_BASE_URL/internal/v1/admin/style-settings/moments" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"style_prompt":"自然、克制、避免夸张表达。"}'
~~~

当前有效 style_code：business_answer、moments、writing。business_expert_insight 是历史兼容项，页面隐藏或忽略，不新增、不编辑，也不用于生成课程专业见解。

查询返回示例：

~~~json
{"items":[{"code":"business_answer","label":"商业顾问主回答风格","enabled":true,"style_prompt":"回答直接、清楚，不空泛。","version":3,"updated_at":"2026-07-28T10:00:00Z","updated_by":"system"}]}
~~~

### 3.8 知识库概览（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

返回 items[]：code、name、document_count、enabled_document_count。code 当前为 general 或 course。

### 3.9 知识库文档列表（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/documents
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/documents" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

文档项包含 document_id、name、enabled、indexing_status、word_count、segment_count、created_at、updated_at、source_sha256、source_filename。

## 4. 清洗和发布接口

文档清洗是 AI 管理端必接核心功能。新系统统一使用 /internal/v1/admin/knowledge-bases/{kb_code}/...，不要新接入历史 /internal/v1/knowledge/*。

### 4.1 查询清洗策略（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/cleaning-strategies" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

| strategy_code | 名称 | 知识库 | 支持扩展名 |
| --- | --- | --- | --- |
| personal_business_consultation | 个人商业咨询案例总结 | general | .pdf |
| wechat_public_account_cognition | 微信公众号认知分享 | general | .txt |
| zsxq_post | 知识星球主题 | general | .md、.markdown |
| xiaobot_post | 小报童文章 | general | .md、.markdown |
| lead_magnet | 资料钩子 | general | .md、.markdown、.txt、.docx、.pdf |
| book | 书籍/课程讲义 | general | .md、.markdown、.txt、.docx、.pdf |
| course_route_catalog | 课程信息与学习路线（合并） | course | .xlsx |
| course_information | 课程信息（兼容） | course | .md、.markdown、.txt、.docx |
| learning_route | 学习路线（兼容） | course | .md、.markdown、.txt、.docx |

本轮测试页必须验收个人商业咨询、公众号认知分享、知识星球、小报童、资料钩子和 `course_route_catalog` 六种指定策略；`book` 保持既有独立能力。`course_information`、`learning_route` 仅用于兼容历史批次/旧调用，不作为本轮页面测试路径。

### 4.2 创建上传批次（必须对接）

~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches
Content-Type: multipart/form-data
~~~

表单：files（必填，多文件）、upload_mode（cleaned/raw，默认 cleaned）、strategy_code、common_metadata（JSON 对象）。

通用资料：

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/general/upload-batches" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "files=@samples/lead_magnet.md" -F "upload_mode=cleaned" \
  -F "strategy_code=lead_magnet" \
  -F 'common_metadata={"document_type":"lead_magnet","review_status":"approved","status":"active"}'
~~~

课程信息：

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "files=@samples/course-information.md" -F "upload_mode=cleaned" \
  -F "strategy_code=course_information" \
  -F 'common_metadata={"document_type":"course","direction":"AI基础","stage":"入门","source_platform":"小鹅通","status":"active"}'
~~~

学习路线：

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "files=@samples/learning-route.md" -F "upload_mode=cleaned" \
  -F "strategy_code=learning_route" \
  -F 'common_metadata={"document_type":"learning_route","direction":"AI基础","status":"active"}'
~~~

受控 common_metadata：document_type、review_status、course_id、route_id、direction、stage、source_platform、series_name、status。不要写 official_url，课程正式链接只存在数据库并由服务端生成跳转卡片。

成功返回至少包含 batch_id、strategy_code、knowledge_base_code、upload_mode、status、progress、next_poll_after_seconds、all_succeeded、total、succeeded、failed、file_counts、item_counts、files。

### 4.3 课程信息文件模板

文件必须包含一个或多个 YAML 数据块，每个数据块为 record_type: course：

````markdown
# 课程信息

```yaml
record_type: course
course_id: course_ai_001
name: AI工具入门
direction: AI基础
description: 帮助零基础用户理解并使用常见AI工具。
official_url: https://example.com/course-ai-001
stage: 入门
source_platform: 小鹅通
series_name: AI基础系列
published_at: 2026-07-01
prerequisite_course_ids: []
enabled: true
review_status: approved
audience: 零基础创业者
highlights:
  - 常见AI工具选择
learning_outcomes:
  - 能完成基础办公任务
special_prerequisites: []
stage_tags:
  - 零基础
```
````

必填：name、direction、description、official_url、stage。course_id 缺失时按方向和名称生成稳定 ID；source_platform 缺失默认“小鹅通”；series_name、published_at 可不填。前置课程只能填写明确存在的 course_id。

### 4.4 学习路线文件模板

路线文件必须包含一个或多个 YAML 数据块，每个数据块为 record_type: route：

````markdown
# 学习路线

```yaml
record_type: route
route_id: route_ai_basic
name: AI基础入门路线
direction: AI基础
target: 能够独立完成常见AI办公任务
enabled: true
review_status: approved
stages:
  - stage_id: stage_1
    name: 入门认知
    order: 1
    course_ids:
      - course_ai_001
  - stage_id: stage_2
    name: 实践应用
    order: 2
    course_ids:
      - course_ai_002
```
````

route_id 缺失时按方向和名称生成稳定 ID；阶段 order 必须从 1 连续递增；课程不能跨阶段重复；路线不能引用不存在、停用或未审核课程。

### 4.5 查询上传批次（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

按返回的 next_poll_after_seconds 轮询到终态。失败时查看 files[].error_code、files[].error_message 和条目状态。

终态成功的最小返回示例：

~~~json
{"batch_id":"batch_001","status":"succeeded","progress":100,"all_succeeded":true,"all_processed_without_error":true,"total":1,"succeeded":1,"failed":0,"next_poll_after_seconds":null}
~~~

### 4.6 文件条目列表（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items?status=<status>&cursor=<cursor>&limit=50
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001/files/file_001/items?limit=50" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

条目包含 item_id、sequence、title、document_key、status、stage、error_code、error_message、retryable、review_revision、warnings、metadata、chunking、dify_document_id、remote_parent_count、remote_child_count。

返回示例：

~~~json
{"file_id":"file_001","items":[{"item_id":"item_001","sequence":1,"title":"AI工具入门","status":"succeeded","stage":"completed","retryable":false,"review_revision":0,"warnings":[],"metadata":{"document_type":"course","course_id":"course_ai_001"},"dify_document_id":"doc_001"}],"next_cursor":null,"total":1}
~~~

### 4.7 条目详情（必须对接）

~~~http
GET /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001/files/file_001/items/item_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

详情额外包含 cleaned_markdown 和 audit。正文只用于管理员查看和复核，不应当作课程承诺或正式链接。

### 4.8 重试失败文件（必须对接）

~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry
~~~

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001/files/file_001/retry" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

仅在文件失败或存在失败条目时调用；全部条目为 review_required 时返回 409，必须先人工复核。

成功返回：{"file_id":"file_001","status":"pending"}。

### 4.9 重试失败条目（必须对接）

~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry
~~~

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001/files/file_001/items/item_001/retry" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

仅允许 status=failed 的条目；review_required 条目必须先复核。

成功返回：{"item_id":"item_001","status":"pending"}。

### 4.10 条目人工复核（必须对接）

~~~http
POST /internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions
Content-Type: multipart/form-data
~~~

表单：action（approve、replace_and_approve、exclude）、expected_revision、reason、metadata_patch、markdown_file（仅 replace_and_approve 必填）。

~~~bash
curl -s -X POST "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/upload-batches/batch_001/files/file_001/items/item_001/review-decisions" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "action=approve" -F "expected_revision=0" \
  -F "reason=已人工确认正文和 Metadata"
~~~

expected_revision 不一致返回 409 CONFLICT；exclude 不再入库，其他动作会重新提交条目任务。

### 4.11 启用或停用文档（必须对接）

~~~http
PATCH /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status
Content-Type: application/json
~~~

~~~bash
curl -s -X PATCH "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/course/documents/doc_001/status" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"enabled":false}'
~~~

停用文档后不得继续参与检索或课程/路线推荐。

### 4.12 删除文档（必须对接）

~~~http
DELETE /internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}
Content-Type: application/json
~~~

~~~bash
curl -s -X DELETE "$AI_SERVICE_BASE_URL/internal/v1/admin/knowledge-bases/general/documents/doc_001" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"confirm_name":"测试文档.md"}'
~~~

必须传精确 confirm_name；课程或路线权威记录仍存在时，服务可能返回 409，先处理目录状态。

## 5. 运维、历史兼容和不需要新对接的接口

### 5.1 日志和健康检查（运维保留）

~~~http
GET /internal/v1/admin/logs?page=1&limit=50
GET /internal/v1/admin/logs/{log_id}
GET /health/live
GET /health/ready
~~~

~~~bash
curl -s "$AI_SERVICE_BASE_URL/internal/v1/admin/logs?page=1&limit=50" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Admin-Token: $ADMIN_TOKEN"
~~~

这些接口供部署、监控和排障使用，不是普通小程序业务主流程。

### 5.2 历史清洗 URL（保留但不新对接）

~~~http
GET  /internal/v1/knowledge/cleaning-strategies
POST /internal/v1/knowledge/cleaning-batches
GET  /internal/v1/knowledge/cleaning-batches/{batch_id}
POST /internal/v1/knowledge/cleaning-batches/{batch_id}/files/{file_id}/retry
~~~

这是旧 URL，不是旧清洗功能；底层仍使用同一清洗实现。代码和历史测试暂时保留，AI 管理端新开发统一使用第 4 章接口族。旧入口不能只替换 URL 就假定请求和返回完全兼容。

### 5.3 Dify 控制台能力（不是本服务接口）

以下操作继续在 Dify 控制台完成：创建/删除整个知识库、切换模型、管理 Chatflow/Workflow、知识库级配置。本服务仍负责现有知识库内的文档上传、清洗、复核、启停和删除。

## 6. 直接验收清单

### 6.1 小程序后端

1. 健康检查和入口配置成功。
2. 新建商业顾问 SSE，保存 conversation_id、message_id、task_id。
3. 续聊并读取会话、消息列表。
4. 验证停止生成、删除会话。
5. 分别调用朋友圈和写作 SSE。
6. 课程卡片使用原始 redirect_url 完成跳转。
7. 未确认路线/阶段时课程卡片为 0；确认后最多 5 门且顺序正确。

### 6.2 AI 管理端

1. 查询知识库、文档和清洗策略。
2. 通过页面或 multipart 请求上传真实样本文档。
3. 轮询批次，查看文件条目和详情。
4. 使用失败样本测试文件/条目重试。
5. 使用 review_required 样本测试 approve、replace_and_approve、exclude。
6. 验证停用后不能检索或推荐，删除要求精确名称。
7. 查询课程路线目录、保留配置和有效输出风格。

### 6.3 通过标准

- 请求头、路径、请求体、返回字段和 SSE 事件与当前代码一致。
- SSE 按事件边界解析，sequence 单调递增。
- 未确认路线或阶段时课程卡片数为 0。
- 当前阶段优先、下一阶段其次，总数不超过 5 门。
- 停用或未审核课程/路线不被推荐。
- 原始课程链接不进入清洗正文或普通知识文本。
- business_expert_insight 不被新页面配置或渲染。
- 新系统不调用历史 /internal/v1/knowledge/*。

## 7. 依据和版本边界

依据：ai_management/api/routers/、ai_management/cleaning.py、ai_management/schemas.py 当前实现；ai_management/test_ui.py 页面调用；tests/test_app_routing.py、tests/test_ai_management.py、tests/test_course_route_catalog.py 契约用例；用户操作和管理员操作部分场景.md；AI管理服务快速落地与测试计划.md。

本文件不包含真实 Token、密码、Dify Key 或服务器地址。协议变化时，必须同步更新本文件和原分级审阅版并重新运行契约测试。
