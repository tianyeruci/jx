# 小程序后端与 AI 管理服务接口对接交互版本（修改后接口影响版）

> 适用范围：当前小程序后端调用 AI 管理服务的朋友圈顾问与日常写作工具。本文是本轮接口修复后的交付契约；课程 Excel、学习路线和旧会话数据不属于本轮接口或测试范围。

## 1. 通用要求

所有内部接口必须携带：

```http
X-Internal-Token: <内部服务令牌>
X-User-Id: <小程序后端稳定用户 ID>
```

不要把 Dify 的原始 `conversation_id` 暴露给小程序。服务返回的 `conversation_id` 是本服务本地会话 ID。

## 2. 会话生命周期

`kind` 只能是 `moments` 或 `writing`。

### 2.1 查询当前会话

```http
GET /internal/v1/tools/{kind}/conversations/current
```

无会话返回：

```json
{
  "conversation_id": null,
  "kind": null,
  "type": null,
  "output_format": null,
  "guide": null
}
```

### 2.2 创建会话

```http
POST /internal/v1/tools/{kind}/conversations
Content-Type: application/json
```

朋友圈请求：

```json
{"type":"sales"}
```

朋友圈 `type`：`sales`、`seeding`、`polish`。

日常写作请求：

```json
{"type":"polish","output_format":"article"}
```

写作 `type`：`polish`、`expand`；`output_format`：`article`、`wechat_public_account`。

创建成功返回本地会话 ID、类型、格式和使用提示。已有会话再次提交相同选项幂等返回；尝试修改已有会话类型返回 `409`，应先 reset。

### 2.3 重置会话

```http
POST /internal/v1/tools/{kind}/conversations/{conversation_id}/reset
```

成功后关闭当前会话并返回新的空会话状态。无 ID 的历史路径 `/internal/v1/tools/{kind}/conversations/reset` 不存在，返回 `404`。reset 只影响 `moments`/`writing` 工具会话，不影响商业顾问 `BusinessConversation`。

## 3. 生成接口（SSE）

### 3.1 朋友圈

```http
POST /internal/v1/tools/moments/run
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "conversation_id": "<2.2 返回的本地 ID>",
  "inputs": {
    "request": "写一条朋友圈",
    "source_material": "今天学习了 AI 工作流",
    "audience": "创业者",
    "tone": "自然",
    "constraints": "不要夸张"
  }
}
```

### 3.2 日常写作

```http
POST /internal/v1/tools/writing/run
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "conversation_id": "<2.2 返回的本地 ID>",
  "inputs": {
    "request": "帮我改写成更正式的版本",
    "source_text": "原始内容",
    "audience": "客户",
    "tone": "正式",
    "constraints": "保持简洁"
  }
}
```

`conversation_id` 必须在请求体顶层，且必须是当前用户通过创建/查询接口得到的本地 ID。`inputs` 禁止 `conversation_id`、`type`、`format`、`output_format`；类型和写作成稿格式由服务端会话读取。两个 `/run` 请求模型以及 `inputs` 均拒绝未知字段，错误返回 `422`，不会静默忽略。

缺少会话 ID、会话不存在或无权访问时，不会自动创建会话；请先调用 2.2。这样可以避免小程序进入页面时误复用旧模式或把控制字段发送给 Dify。

SSE 事件名保持：`request_started`、`status`、`answer_delta`、`message_end`、`error`。客户端按 SSE 事件边界解析，不要把一次网络分片当作完整 JSON。

## 4. 文本触发重置

在 `/run` 的 `inputs.request` 中仅以下七个精确短语触发重置（允许首尾空白和句末标点）：

```text
新对话
开启新对话
开始新对话
新建对话
重新开始对话
重新开启对话
重开对话
```

例如“请开启一个新会话”“创建新会话”“开始写一篇新文章”等相似表达不触发 reset，按普通内容生成。触发后通过 SSE 返回“已开启新会话”，不调用 Dify。

## 5. 案例目录与停止任务

```http
GET  /internal/v1/tools/{kind}/examples
POST /internal/v1/tools/{task_id}/stop
```

旧案例已按当前需求清除，空目录仍返回 `200` 和 `items: []`；管理员案例 CRUD 由单独管理接口提供。停止任务只能操作当前用户拥有的工具任务。

## 6. 变更影响与验收要点

- 小程序必须先创建/查询工具会话，再调用 `/run`；测试页也遵循同一顺序。
- 不需要修改商业顾问接口；`BusinessConversation` 与工具会话数据隔离。
- 本轮发布不导入或更新 Dify DSL；课程 Excel/学习路线属于历史兼容资料，不纳入当前发布门禁。
- 最低契约验收：current 空状态、两类 create、两类 run SSE、连续会话、reset、reset 后重新选型、嵌套/顶层非法字段返回 `422`、旧 reset 路径 `404`。

### 6.1 jx 验收记录（2026-08-30）

- 发布版本：`toolconv-20260830-155349-contractfix2`；API、Worker、Scheduler 和测试页已切换到该版本，API readiness 为 `ready`。
- 已通过测试页验证：朋友圈 reset 后重新选型、种草型会话创建与 SSE 生成、日常写作会话创建与文章生成、写作 reset 后重新选型；商业顾问入口正常加载。
- 迁移从 `m9d0e1f2g3h4` 升级到唯一 head `n0e1f2g3h4i5`；发布报告无回滚标记。
- 浏览器仅记录 Streamlit `WebSocket onclose` warning，无页面 error；该 warning 不影响本次接口/SSE 验收。

### 6.2 三类顾问消息操作增量

- 工具新增 feedback/regenerate：`POST /internal/v1/tools/{kind}/messages/{message_id}/feedback` 与 `/regenerate`，`kind` 仅 moments/writing；message_id 是不透明 Dify ID，重生成回读 query/inputs 后沿用同一 conversation_id 重发。
- 工具普通/重生成首次取得 task ID 后发送 `status(code=task_started,task_id)`；完成/停止发送包含 conversation_id、opaque message_id、task_id、status 的 `message_end`；失败发送 `error(status=failed)`，客户端清空本次全部 delta，停止保留已生成片段。
- 工具 Memory 目标为实际生成节点开启窗口 6；商业主 LLM Memory 关闭。旧工具问答可能继续留在 Dify Memory，页面以新 message_id 替换；本服务不持久工具正文或伪称远端删除。
