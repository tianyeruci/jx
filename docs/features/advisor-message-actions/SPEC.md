# 三类 AI 顾问消息操作 — Requirement Spec

- Feature ID: `advisor-message-actions`
- Status: APPROVED
- Risk: `standard`（最终 jx/Dify 发布按 High Release Gate 执行）

## 1. Goal

为商业顾问增加严格替换语义的“重新生成”；为朋友圈顾问和日常写作增加同一 Dify 会话内的“重新生成”“消息反馈”“消息停止”。能力通过既有 `/internal/v1` 鉴权供小程序后端调用，且普通生成、既有路径和字段保持兼容。

## 2. Non-goals

- 不新增服务、通用抽象、消息版本表或数据库迁移。
- 不持久化朋友圈/写作正文或反馈，不提供工具消息历史恢复。
- 不使用 Dify 分支、`parent_message_id`、新 App、App Key 轮换、Service API 定制或 Dify 业务数据库直改。
- 不承诺从工具 Dify Memory 物理删除旧、失败或停止的远端消息。
- 不制作交互原型；现有商业反馈/停止模式与 Streamlit 测试页足以验证交互。

## 3. Background / Evidence

- `BusinessMessage` 已有正文、状态、Dify message/task、反馈、检索资源和指标字段，可原地替换。
- `ToolConversation` 只保存匿名用户、kind、输入类型、格式和 Dify conversation 映射；现有生成链路不写 `ToolMessage.content`。
- 商业顾问由本服务显式注入摘要、近期历史、画像；商业 Dify 模型 Memory 必须保持关闭。
- 工具使用 Dify `advanced-chat` 会话。产品基线要求实际生成 LLM 节点开启 Memory 窗口 6。Dify 保留同会话记录，工具重生成采用“回读再发送、旧远端问答仍可能在 Memory”的最小改动语义。

## 4. Actors and Scenarios

1. 小程序后端以现有内部 Token 和用户标识调用三个入口，并将工具 `message_id` 当作不透明 Dify ID。
2. 商业用户对当前会话最新且未被摘要覆盖的助手回答重新生成；服务立即作废旧答案，使用原问题之前的本地上下文覆盖原助手记录。
3. 朋友圈/写作用户对当前有效会话内的最新 Dify 消息重新生成；服务回读其 `query/inputs`，按当前本地控制值校验、重发到同一 Dify conversation；页面替换为新消息 ID。
4. 工具用户可对当前会话归属的 Dify 消息点赞、点踩、取消反馈，也可在拿到早期 task ID 后停止生成。
5. reset、跨日过期、跨用户、跨入口、未知消息、非最新消息或并发操作均被拒绝。

## 5. Functional Requirements

### FR-001 商业顾问重新生成

- 新增 `POST /internal/v1/business/messages/{message_id}/regenerate`，无 body、返回 SSE；`message_id` 是本地助手消息 ID。
- 只允许当前用户、当前会话最新助手消息、未被 `summary_through_message_id` 覆盖、且无生成/重置并发。列表每条消息增加 `can_regenerate`；最新失败消息允许再试。
- 接受后在会话锁内清空旧正文、反馈、推荐/检索、指标、Dify task/message ID，并将原 user/assistant 二元组移出上下文；不新增消息行。
- 新请求只以原用户问题作为 `query`，显式本地上下文同时排除原 user/assistant 二元组；商业 Dify Memory 继续关闭。
- `completed` 覆盖同一助手行；`stopped` 保留本次新片段；`failed` 不保留旧答案和新片段，二元组不进入后续上下文、摘要或画像；失败 SSE 带该本地 ID 和 `status=failed`。

### FR-002 商业摘要安全边界

- 摘要 Worker 永远暂留最新一组完整 user+assistant；仅下一回合完成后才可摘要前一组。
- 已被历史摘要覆盖的消息不重建摘要/画像，明确不可重新生成。

### FR-003 工具反馈和重新生成

- 新增 `POST /internal/v1/tools/{kind}/messages/{message_id}/feedback`，`kind` 仅 `moments|writing`；请求复用 `rating: like|dislike|null` 和最大 1000 字 `content`。
- 新增 `POST /internal/v1/tools/{kind}/messages/{message_id}/regenerate`，无 body、返回 SSE；仅允许当前匿名用户、当前 kind、当天有效未 reset 的 Dify 会话中的最新消息。
- Dify `/messages` 读取必须限定当前 `user` 与 `conversation_id`。重生成回读原 `query/inputs`，仅用既有 `MomentsInputs`/`WritingInputs` 白名单验证；`type`、writing `format`、管理员风格均从当前本地会话/配置覆写。
- 使用原 `conversation_id` 和匿名 user；不发送 `parent_message_id`，不修改或伪称删除旧 Dify 消息；不写工具正文或反馈到本地。

### FR-004 工具停止和 SSE 终态

- 复用 `POST /internal/v1/tools/{task_id}/stop`，删除 Redis 映射失效后轮询两个 App 的回退；只允许 Redis 中仍存在且绑定当前用户、入口、transport 的 task。
- task 首次取得时发送 `status {code: task_started, task_id}`；成功/停止的 `message_end` 含 `conversation_id`、新不透明 `message_id`、`task_id`、`status=completed|stopped`；失败 `error` 含 `conversation_id`、已知时的 `message_id`、`status=failed`。
- 客户端失败清空本次累计 delta、停止保留本次 delta；用户绑定 stop marker 让 SSE 可靠以 `stopped` 结束，重复停止/已结束任务为 404。

### FR-005 能力、测试页和审计

- `entry-config.features`：business 是 `history/feedback/regenerate/stop=true`；moments/writing 是 `history=false, feedback/regenerate/stop=true`。
- Streamlit 保存 task/message ID：商业提供重新生成；工具提供重新生成、like/dislike/cancel、停止，并在重生成被接受后立即清空旧答案。
- 停止、反馈、重新生成只记录脱敏操作事件（request ID、哈希用户、入口、消息/task ID、状态）；不得记录 query、inputs、正文或真实用户 ID。

### FR-006 Dify 资产和上线

- 仓库权威工具 DSL 的实际生成 LLM 节点必须 Memory enabled、window size 6；商业主 LLM Memory 保持关闭，且有静态契约测试。
- 发布前检查实际绑定 Dify App 发布图；若不符合工具 Memory 基线，在原图最小补齐、保存/发布。不得以本地文件名代替线上核验。
- jx 走完整 wheel 发布，先建立和验证本次备份/回滚点；不 push、合并或生产数据清理。实际小程序后端主机不可达时明确作为外部 E2E 阻塞，不能以 jx 本机替代。

## 6. Applicable Constraints

- 维持 L2 FastAPI/Celery/Redis/PostgreSQL/Dify/Streamlit 模块化单体。router 协调 HTTP/SSE；现有 conversations、Dify client、summary worker、tool-conversation service 各自负责原职责。
- 内部鉴权、普通生成 body、现有路径/SSE 字段不破坏；新增 JSON/SSE 字段要求旧客户端可忽略。
- Dify 超时、不可达或非法响应继续使用 `DIFY_UNAVAILABLE` / `DIFY_RESPONSE_INVALID`。
- 日志、报告、测试数据必须脱敏；远端清理仅按精确资源 ID/关联记录执行。

## 7. State / Data / Permission Rules

- 商业未找到/跨用户/非有效会话为 `404 MESSAGE_NOT_FOUND`；非最新、已摘要或状态不允许为 `409 MESSAGE_NOT_REGENERATABLE`；并发沿用 `CONVERSATION_BUSY`/`CONFLICT`。
- 工具跨用户、跨 kind、过期/reset、非当前会话、未知消息为 `404 MESSAGE_NOT_FOUND`；非最新重生为 `409 MESSAGE_NOT_REGENERATABLE`。
- 工具普通消息可按 Dify 原生反馈契约反馈；只有最新消息可重生成。刷新后客户端未保存 ID 时不新增服务端正文恢复。
- 工具失败/停止的远端记录可能仍留在 Dify 会话；本服务只保证页面终态以及当天/reset 的既有精确整会话删除。

## 8. Error and Boundary Behavior

- 商业重生成被服务接受后不恢复旧答案：失败内容为空，停止内容为本次新片段。
- 工具重生成被接受后客户端移除旧页面答案：失败清空新 delta，停止保留新 delta；服务不宣称能清除 Dify Memory 的旧问答。
- `/messages` 记录不属于当前会话、缺少 query/inputs 或不通过白名单时，拒绝且不调用生成。

## 9. Assumptions and Resolved Decisions

| Decision ID | Question / assumption | Decision | Evidence / reason | Alternatives rejected |
|---|---|---|---|---|
| RD-001 | 三类入口是否共用重生成 | 商业严格本地替换；工具同会话回读重发 | 商业 Memory 关闭且有本地历史；工具需 Memory 6、无正文持久化 | 泛化版本/抽象层 |
| RD-002 | 工具是否能严格排除旧回答 | 不能；明确同会话最小重发 | Dify Service API 无分支/删除/助手历史注入 | 伪称 Memory 已删除或新会话丢上下文 |
| RD-003 | 商业摘要如何可替换 | 暂留最新完整二元组 | 防旧答案进入摘要/画像，代价最多一轮延迟 | 对旧摘要反向重建 |
| RD-004 | 是否存工具正文/反馈 | 不存 | Dify 原生消息/反馈及会话映射足够 | 新表/本地历史 |
| RD-005 | 是否需要原型 | 不需要 | 既有交互及测试页可复用 | 一次性 UI 原型 |
| RD-006 | DSL 同步依据 | 先核验线上绑定 App，再最小同步 | 本地资产不自动等于线上发布图 | 本地完整 DSL 覆盖线上 |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 三条新增路由、入口能力字段和既有停止路由在内部鉴权下正确注册 | 普通生成兼容 |
| AC-002 | 商业只能重生最新未摘要回答，且以同一本地消息严格替换 | 覆盖成功、失败、停止、重试 |
| AC-003 | 摘要 Worker 暂留最新完整二元组，下一回合只摘要替换后的答案 | 不回滚画像 |
| AC-004 | 工具反馈/重生严格校验用户/kind/会话/最新性与白名单输入 | 不写 ToolMessage 正文 |
| AC-005 | 工具 SSE 提前 task ID，成功/失败/停止终态和页面语义正确 | stop marker 可靠结束 |
| AC-006 | 两工具 DSL Memory 窗口 6，商业主 LLM Memory 关闭且原有关键契约不变 | 上线前再核验 |
| AC-007 | Streamlit 可操作新增能力；聚焦、完整 quality、独立审查、jx/Dify 验收有证据 | 小程序 E2E 不可达须说明 |
| AC-008 | API/SSE、正式小程序接口、Dify、架构、Feature、索引同步且 PROFILE 悬空 Feature 引用被修正 | 不伪造历史 Feature |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error / negative | Security / permission | Compatibility / migration |
|---|---|---|---|---|---|
| AC-001 | 路由、entry config、SSE content type | 全部 kind | 缺 Token/header | 跨用户拒绝 | 旧字段可解析、无迁移 |
| AC-002 | 同 ID 覆盖完成 | stopped、failed 重试、摘要游标 | 旧/非最新/已摘要/并发 409 | 非所属消息 404 | 原 chat 和 ID 不变 |
| AC-003 | 第二回合摘要前一组 | 最新 incomplete/failed | 旧答案不摘要 | N/A | 原游标兼容 |
| AC-004 | like/dislike/null、同会话重发 | type/format/style 覆盖 | 缺 query/非法 inputs/非最新 | 用户/kind/会话隔离 | 不写 ToolMessage.content |
| AC-005 | task_started→delta→completed | stopped 保留、failed 清空 | 已结束 stop 404 | task mapping 绑定 | 旧客户端忽略新增字段 |
| AC-006 | DSL 静态属性 | 两工具各生成节点 | business memory-on 失败 | N/A | Start/Prompt/Key 不变 |
| AC-007 | 测试页完整交互 | ID 获取后启用 | Dify/API 错误清页面 | 不泄露敏感信息 | 无迁移 |
| AC-008 | 文档/索引检查 | generated state | 漏同步失败 | 文档无敏感数据 | 增量版本兼容 |

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | 当前产品合同 | Generate |
| Adaptive Prototype | Not needed | 既有交互已解决 UI 歧义 | Skip, user-confirmed |
| Implementation Spec | Required | 多模块 API/SSE/Dify/worker 改动需要 ownership | Generate |
| ADR / Ops docs | Existing docs only | 修正 PROFILE、记录发布事实 | Update affected docs |

## 13. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
