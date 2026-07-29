# AI 管理服务当前状态与后续测试交接

> 更新时间：2026-07-29
> 用途：新开 Codex 对话后，先完整阅读本文，再继续联调、测试和修复。  
> 业务与实现边界仍以 `AI管理服务快速落地与测试计划.md` 为准。

## 1. 当前结论

AI 管理服务的代码、Dify 应用、测试知识库和服务器基础部署已经基本打通。

商业顾问 Chatflow 已完成双测试知识库绑定并发布：

- “通用知识检索”绑定 `business-general-test`。
- “课程路线检索”绑定 `business-course-test`。
- 发布后的真实 API 调用已确认两个节点分别召回对应知识库。

当前仍不是“全部验收完成”状态。已进入发布后的契约、业务集成、摘要画像、清洗、RAG、并发和故障测试阶段。

### 1.1 2026-07-28 文档清洗链路最新状态

文档清洗策略与入库链路已经完成代码实现、服务器发布和真实链路验收。

当前线上版本：

- 核心镜像：`jx-now-ai-management:0.2.1-cleaning-27a8bc0-r7`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0`
- Alembic 版本：`6a5f9e2b1c44 (head)`
- 离线 wheel：`jx_now_ai_management-0.2.1-py3-none-any.whl`
- 离线 wheel SHA256：`1f26e3920051afd046840b8716691aed17497b08095c900b8d4bd2edd116f3d1`

本轮新增并已验证：

- 六类正式清洗策略可从 AI 管理页读取。
- 上传批次支持文件级状态、条目级状态、单文件重试、单条目重试和人工复核。
- 成功条目会验证 Dify 索引状态、父子块数量和 Metadata 写入结果。
- Redis 可查询批次状态与文件清单。
- 成功源文件会删除；失败文件按保留期暂存。

2026-07-28 线上真实验收结果：

- `/health/live`、`/health/ready` 均通过。
- AI 管理页测试路径 `/ai-management-test` 可访问，`/ai-management-test/_stcore/health` 返回 `ok`。
- `scripts/dify_contract_check.py`、`scripts/live_business_lifecycle.py`、`scripts/live_stop_check.py` 均通过。
- 真实上传“1 个有效 Markdown + 1 个非法 EXE”批次后：
  - 批次状态 `completed`
  - 成功文件 `succeeded`
  - 非法文件 `failed`
  - `all_succeeded=false`
  - `all_processed_without_error=false`
  - 成功条目远端父块数 `1`、子块数 `1`
  - 成功源文件已删除
  - 验收生成的测试知识库文档已删除
- 真实待复核链路补测后：
  - 正式策略列表返回 6 个策略
  - 纯待复核文件终态稳定为 `review_required`
  - 待复核条目直接重试返回 `409`
  - 纯待复核文件直接重试返回 `409`
  - `replace_and_approve` 可成功入库并回读远端父子块计数 `1/1`
  - `exclude` 后文件终态为 `excluded`，批次聚合状态同步刷新

本轮发布中修复过 4 个真实缺陷：

1. 待复核条目可被直接重试并发布，绕过人工复核。
2. 纯待复核文件在条目生成完成后状态未落到终态，仍停在 `processing` / `cleaning`。
3. `exclude` 复核后文件和批次聚合状态未即时刷新。
4. 服务器 `Dockerfile.offline` 实际运行代码来自 `wheelhouse`；只同步源码再重建镜像不会带上本地修复，必须先替换 wheel 再重建。

对应本地回归结果：

- `python -m pytest tests -q`：`55 passed`

### 1.2 2026-07-29 接口、测试页与真实清洗链路复验（r8）

本轮验收范围已经完成，不应再将接口交接文档视为“仅按代码推断”：

- 核心镜像：`jx-now-ai-management:0.2.1-cleaning-27a8bc0-r8`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0-r8`
- `BUILD_VERSION`：`0.2.1-cleaning-27a8bc0-r8`
- wheel SHA256：`d3ffbbccf2dd633fc9d626fb574baf7586281809a9c6892467afd7ca1602649a`
- Alembic：`6a5f9e2b1c44 (head)`

- 本地全量回归：`58 passed`
- 按 [小程序后端与AI管理服务接口对接标准.md](docs/小程序后端与AI管理服务接口对接标准.md) 的路径、参数和返回结构实测：`49/49` 通过。
- 浏览器测试页真实用户点击：`15/15` 通过；商业顾问问题“我想系统学习 AI 工具，应该从哪里开始？”稳定返回路线与课程卡片。
- 一条真实知识星球 Markdown 样本完成上传→`zsxq_post` 清洗→Dify 入库→父子块/Metadata 回读→禁用→启用→删除；父/子块数为 `3/11`，Metadata 中的版本值为 r8。
- 专用 stop 校验：HTTP `200`，终态 `message_end.status=stopped`。

验收报告与截图位置：

- [测试页与接口全量实测记录-2026-07-29.md](docs/测试页与接口全量实测记录-2026-07-29.md)
- 服务器：`/opt/ai-management/var/reports/v2/20260729-interface-contract-r8-retry1/`
- 浏览器截图：本地 `output/playwright/`

发布过程出现过宿主机无 `pg_restore`、Windows CRLF 传入 Bash、旧导出镜像变量覆盖 `.env` 三个运维问题，均已在部署手册中修正；最终 API、Worker、Scheduler、测试页均运行 r8。

### 1.3 2026-07-29 测试页全接口手工验收入口（r9）

- 本次仅升级测试页镜像：`jx-now-ai-management-ui:0.2.1-cleaning-27a8bc0-r9`；核心 API、Worker、Scheduler 保持 r8，未迁移或修改数据。
- “AI 管理只读”已替换为“AI 管理接口”：可手工验收管理会话详情、保留策略与输出风格读写、知识库文档列表 / 启停 / 删除、删除指定用户 AI 数据。
- 页面现有的聊天、工具、课程、清洗、日志、诊断入口仍保留，因此交接标准里的小程序后端和 AI 管理接口均有对应的手工验收入口。
- 写操作要求勾选确认；删除文档必须输入完整名称，删除用户数据必须输入 `DELETE <用户 ID>`。r9 浏览器已实测读取配置与知识库文档成功，删除按钮在确认前保持禁用。
- wheel SHA256：`4c3fdb635c055f53206adfa1887806d8836f3608ab60a41569679feae9144c37`；回退材料：`/opt/ai-management/backups/20260729-ui-contract-full-r9/`。
- 本地全量 pytest 本轮出现超时及 Windows 测试数据库文件锁，已记录在 `docs/测试页与接口全量实测记录-2026-07-29.md`；新增页面专项校验、语法和测试页 ruff 检查均通过。后续若改动 API 或清洗逻辑，必须先定位该慢用例并重新完成全量测试。

## 2. 已完成并验证的工作

### 2.1 本地代码

已经实现：

- FastAPI AI 管理服务。
- 商业顾问本地逻辑会话和 SSE。
- 朋友圈顾问、日常写作 Workflow 代理。
- Dify 应用客户端和知识库客户端。
- PostgreSQL 数据模型和 Alembic 迁移。
- Redis 缓存、锁、课程快照和点击统计。
- Celery Worker 和凌晨定时任务。
- 对话摘要、画像候选和摘要游标逻辑。
- 课程与路线 Markdown 导入 CLI。
- 课程 ID、路线 ID、启用和审核状态校验。
- 课程跳转签名链接。
- 代码内置清洗策略、批量上传、状态查询和重试。
- Dify 索引、Metadata、零分块检查和失败补偿。
- Streamlit 极简测试页代码。
- 四份 Dify DSL 和四份 Prompt 文档。
- 测试课程、测试路线、通用资料和 40 条 RAG 问题。

本地验证结果：

- `python -m pytest -q tests`：27 项通过。
- Docker Compose 配置解析通过。
- Alembic 初始迁移可执行。
- 课程测试资料 dry-run：9 门课程、8 门启用课程、2 条路线。

### 2.2 Dify 应用

| 应用 | 类型 | 当前实测 |
|---|---|---|
| 商业顾问 | Chatflow | 已发布版本可调用；两轮续聊成功 |
| 摘要画像 | Workflow | 调用成功并返回结构化结果 |
| 日常写作 | Workflow | 调用成功 |
| 朋友圈顾问 | Workflow | 调用成功 |

已经验证：

- 商业顾问第一轮返回 HTTP 200。
- 第二轮复用同一个 Dify `conversation_id` 并返回 HTTP 200。
- 按正确 JSON body 删除临时 Dify 会话返回 HTTP 204。
- 摘要画像、日常写作、朋友圈顾问 Workflow 均返回 `succeeded`。

注意：早先出现过一次会话删除 415，是临时探针错误地把 `user` 放在查询参数中导致。正式客户端使用 JSON body，已实测删除返回 204，不是产品缺陷。

### 2.3 Dify 测试知识库

知识库 API 地址：

```text
http://112.74.37.111/v1
```

API Key、App Key 和内部 Token 不写入本文，均已保存在本地和服务器的 `.env`。

| 知识库 | Dataset ID | 当前数据 |
|---|---|---|
| `business-general-test` | `19206ce0-80a4-4a93-aa86-63e3385d2b2a` | 1 份测试资料，22 个分块 |
| `business-course-test` | `447241bf-846a-428e-90c0-0e1fe8d7b331` | 9 门课程＋2 条路线，共 11 份文档 |

服务器上另有旧知识库：

```text
名称：商业顾问
Dataset ID：a05a3c77-eeb7-40c7-a8b2-260c5f4e0da2
```

不要删除或修改旧知识库，除非用户之后明确授权。

当前 Chatflow 发布状态：

- “通用知识检索”节点显示并实际召回 `business-general-test`。
- “课程路线检索”节点显示并实际召回 `business-course-test`。
- 旧知识库“商业顾问”未修改、未删除。

### 2.5 发布后复测与兼容修复（2026-07-23）

发布后真实调用发现并修复了两个 Dify 1.13.3 兼容问题：

1. Chatflow 节点完成事件使用 `node_finished`，服务原先只监听旧名 `workflow_node_finished`。
2. 推荐决策 JSON 位于 LLM 节点的 `outputs.text`；后续普通回答节点曾被误解析为空决策并覆盖正确结果。

修复后结果：

- 本地 `python -m pytest -q tests`：29 项通过。
- 服务器使用 `Dockerfile.offline` 和现有 wheelhouse 重新构建，API、Worker、Scheduler 已重建并运行。
- `/health/ready` 返回数据库、Redis、Dify 应用和知识库均为 `true`。
- 精确课程“AI工具入门”返回 `course_ai_001` 课程卡片和服务端签名跳转地址。
- AI 学习路线返回 `route_ai_basic`、首阶段 `course_ai_001` 和完整审核阶段。
- 公众号学习路线返回 `route_wechat_operation` 和完整审核阶段。
- 停用课程 `course_ai_legacy` 不生成课程卡片。
- Dify 契约复测：商业顾问两轮成功且复用同一 `conversation_id`；三个 Workflow 均为 `succeeded`；通用/课程知识库分别召回 5/4 条。
- 商业会话生命周期复测：消息列表 2 条；点赞、点踩并附反馈原因均返回 200；重命名成功；删除后列表中不可见。
- 停止生成复测：停止接口返回 200，最终 `message_end.status=stopped`，测试会话已删除。
- 课程跳转复测：签名链接返回 302 并跳转到课程事实表中的正式地址；`course_ai_001` 点击统计可查询。
- 40 条黄金问题已执行检索评测：33 条检索用例，聚合 Recall@5 `97.47%`、Hit@5 `100%`、关键问题 Hit@5 `100%`，检索门禁通过。
- 新增 6 条轻量全链路规则评测：课程、AI 路线、公众号路线、停用课程、未知课程、禁止虚构路线全部通过；首 Token P50/P95 为 `4912/5410 ms`，总耗时 P50/P95 为 `8506.5/10303 ms`。该结果是本轮小样本基线，不替代并发压测。
- RAG 剩余人工门禁尚未完成：引用完整率、无依据回答率、错误课程 ID、错误正式链接、虚构路线。
- 本轮创建的 5 个临时业务测试会话已清理。

### 2.4 测试课程与路线

已经通过正式导入 CLI 写入 Dify、AI 管理 PostgreSQL 和 Redis 课程快照。

```text
课程：9
启用且审核通过：8
路线：2
启用且审核通过：2
```

课程 ID：

```text
course_ai_001
course_ai_002
course_ai_003
course_wechat_001
course_wechat_002
course_wechat_003
course_business_001
course_business_002
course_ai_legacy
```

路线 ID：

```text
route_ai_basic
route_wechat_operation
```

`course_ai_legacy` 是停用课程，后续测试必须确认它不会生成课程卡片。

测试数据源：

```text
test_data/courses/测试课程与路线.md
```

## 3. 服务器部署状态

可重复的本地构建、上传、配置、离线重建、测试和回滚流程已整理到
[`docs/本地到线上部署联调实战手册.md`](docs/本地到线上部署联调实战手册.md)。
后续每次发布应同步更新该手册的排坑经验和本文的当前状态。

```text
公网 IP：112.74.37.111
阿里云私网 IP：172.30.157.197
部署目录：/opt/ai-management
```

运行进程：

```text
ai-management-api-1
ai-management-worker-1
ai-management-scheduler-1
ai-management-postgres-1
ai-management-redis-1
```

部署设计：

- AI 管理使用独立 PostgreSQL、Redis 容器和持久卷。
- 不读写 Dify 自身 PostgreSQL 业务表。
- API、Worker、Scheduler 通过 `docker_default` 网络访问 Dify Nginx。
- API 只绑定阿里云私网 `172.30.157.197:8000`，仍需 `X-Internal-Token`。
- 用户身份由小程序后端通过 `X-User-Id` 传入。

健康检查已通过：

```json
{"status":"ok"}
```

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

已经验证：

- 未提供内部 Token 返回 401。
- `/internal/v1/entry-config` 返回三个入口。
- 日常写作经 AI 管理服务 SSE 返回 `status`、`answer_delta`、`message_end`。
- 一次冒烟首 Token 约 913 ms，总耗时约 2136 ms；这不是性能结论。

### 3.1 构建说明

服务器访问 PyPI 很慢，因此增加 `Dockerfile.offline`。生产镜像为：

```text
jx-now-ai-management:0.1.0
```

Streamlit 已从生产核心依赖移到可选 `ui` 依赖，避免生产镜像安装无关大包。

服务器当前保留离线 wheel：

```text
/opt/ai-management/wheelhouse
```

确认不需要再次离线构建后可以清理，但不是当前紧急任务。

### 3.2 极简测试页

Streamlit 极简测试页已构建为独立镜像：

```text
jx-now-ai-management-ui:0.1.0
```

运行容器：

```text
ai-management-test-ui
```

安全边界：

- 服务器仅绑定 `127.0.0.1:8501`，不直接暴露公网。
- 本机通过 SSH 隧道访问 `http://127.0.0.1:8501`。
- 内部 Token 仍由服务器 `.env` 注入测试页容器，不复制到本机文件或浏览器。
- 已修复历史会话下拉框兼容 `conversation_id` 字段。

## 4. 新对话开始后的工作顺序

### 第一步：完成商业顾问绑定并发布（已完成）

进入：

```text
商业顾问 → 编排
```

确认：

```text
通用知识检索 → business-general-test
课程路线检索 → business-course-test
```

完成后发布。不要绑定旧知识库“商业顾问”作为新版测试节点。发布后必须真实调用 API，不能只看控制台状态。

### 第二步：重新执行 Dify 契约测试（主要项已完成）

至少验证：

1. 商业顾问两轮对话和同一个 `conversation_id`。
2. 第二轮普通动态 inputs 实际更新。
3. 停止生成。
4. 点赞、点踩和反馈原因。
5. 删除会话返回 204。
6. 三个 Workflow 返回 `succeeded`。
7. 两个知识库都能独立检索到数据。

可使用：

```text
scripts/dify_contract_check.py
```

脚本带 `--write-knowledge` 时会创建并删除一份临时知识库文档。

### 第三步：课程业务测试（核心场景已完成）

#### 精确课程

```text
请介绍 AI 工具入门课程
```

预期：返回 `course_ai_001`；卡片链接来自课程事实表；正文不自行编造正式链接。

#### AI 路线

```text
我想系统学习 AI 工具，应该从哪里开始？
```

预期：选择 `route_ai_basic`；路线阶段和课程引用来自审核资料。

#### 公众号路线

```text
我想学习公众号运营，有什么学习路线？
```

预期：选择 `route_wechat_operation`。

#### 停用课程

诱导推荐旧版 AI 速成课程。预期：`course_ai_legacy` 不生成课程卡片。

#### 错误候选

诱导模型输出不存在的课程、路线或链接。预期：服务过滤错误 ID，不返回错误卡片或链接。

### 第四步：完整商业会话测试

- 新建、列表、续聊、重命名和删除。
- SSE 断流和停止生成。
- Dify 反馈。
- 同一会话并发返回 409。
- 多用户不串会话、摘要和画像。
- 跨自然日生成新 Dify 会话段。
- 用户画像长期保留。
- 活跃会话删除 7 天前消息但保留摘要。
- 连续 7 天未活动的会话、消息和摘要一起删除。

### 第五步：摘要和画像测试

- 6 个用户轮次触发异步摘要。
- 约 6000 中文字符时按字符阈值触发。
- 主聊天不等待摘要 Workflow。
- 摘要游标之前的原始消息不重复注入。
- 旧摘要任务不能覆盖新摘要。
- 删除历史对话不删除画像。
- 删除完整闲置会话时删除摘要。

### 第六步：RAG 评测

使用：

```text
test_data/rag/golden_questions.jsonl
```

目标：

```text
关键问题 Hit@5 >= 90%
全量 Recall@5 >= 80%
引用完整率 >= 90%
无依据回答率 <= 5%
错误课程 ID = 0
错误正式链接 = 0
虚构路线 = 0
```

资料本身不足时，应调整测试资料或测试集，不得为了过指标让模型编造答案。

### 第七步：批量清洗测试

- 策略列表、`test_markdown` 和现有咨询策略。
- 多文件上传和部分失败。
- 索引完成、分块大于 0、Metadata 成功后才算成功。
- Worker 中断后自动重试一次。
- 失败文件手动重试。
- 所有文件最终进入 `succeeded` 或 `failed`。
- 文件来源可追踪，文件名不直接作为磁盘名或 Redis Key。

正式清洗策略仍需老板提供规则和样本，不能猜测“万能清洗器”。

### 第八步：并发和故障测试

- 20 个并发流式请求，持续 10 分钟。
- Redis、PostgreSQL、Dify 短暂不可用。
- Worker 重启。
- 客户端中途断开。
- 同一会话并发冲突。
- 200 MB 文件流式上传。

不得为了故障测试删除 Dify 正式数据或服务器持久卷。

## 5. 目前不能声称已完成

- 动态 inputs 第二轮更新的运行日志证据。
- 摘要阈值、异步任务和 7 天清理集成测试。
- 批量清洗端到端 Dify 入库测试。
- 40 条 RAG 完整回答人工门禁（检索指标已通过）。
- 20 并发 10 分钟压力测试。
- Redis、PostgreSQL、Dify、Worker 故障演练。
- 小程序后端端到端测试。
- 灰度上线和正式验收报告。

## 6. 防止测试误判

- “已发布”不代表最新草稿已发布，也不代表知识库绑定正确。
- Dify 创建文档成功不等于入库成功；必须索引完成、分块大于 0、Metadata 成功。
- 能回答课程问题不等于推荐正确；还要校验 ID、启用、审核、路线引用和官方链接。
- 点击课程只用于统计，不代表完成学习。
- 一次冒烟耗时不是性能结论。
- Dify 当前仍使用公网 HTTP 地址，正式上线仍应配置域名和 HTTPS。

## 7. 常用位置

```text
本地：D:\work\projectc\jx-now
服务器：/opt/ai-management
```

核心资料：

```text
AI管理服务快速落地与测试计划.md
docs/
dify/dsl/
dify/prompts/
test_data/courses/
test_data/knowledge/
test_data/rag/
test_data/cleaning/
```

## 8. 新对话开场指令

```text
请先完整阅读：
D:\work\projectc\jx-now\AI管理服务当前状态与后续测试交接.md
D:\work\projectc\jx-now\AI管理服务快速落地与测试计划.md

继续完成 AI 管理服务联调和测试。优先确认商业顾问两个知识库节点分别绑定 business-general-test 和 business-course-test，发布后进行真实 API 调用；然后按交接文档顺序完成课程推荐、摘要画像、文件清洗、RAG 和并发故障测试。

你可以直接访问服务器和 Dify，非必要不要让我手动操作。不要输出或提交 .env 中的密钥。不要改动或删除旧知识库“商业顾问”，不要直接操作 Dify 自身业务数据库，不要删除持久卷。每完成一部分都要保存可复核证据并更新交接文档，不能把未验证事项写成已完成。
```
