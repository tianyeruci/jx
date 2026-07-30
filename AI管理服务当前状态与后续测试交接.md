# AI 管理服务当前状态与后续测试交接

> 更新时间：2026-07-30
> 用途：保存最新线上事实、回滚点、数据状态和开放事项。新任务先读 `AGENTS.md` 与 `docs/README.md`，再按功能域读取本文顶部最新状态。  
> 本文不是需求或设计基线；权威顺序以 `docs/V2决策记录.md` 和 `docs/README.md` 为准。第 1～8 节为历史过程记录，不再作为当前执行指令。

## 0.1 2026-07-30 会话记忆与管理审计字段发布（本任务最新）

本任务已发布到 jx，范围仅包含商业会话/管理审计字段、测试页会话状态和对应迁移；未同步 Dify DSL、Prompt 或清洗脚本。

- 核心镜像：`jx-now-ai-management:20260730-user-id-session-77481e5-r2`
- 测试页镜像：`jx-now-ai-management-ui:20260730-user-id-session-77481e5-r2`
- Alembic：`f2a1d6c4b8e9 (head)`
- wheel SHA-256：`1ebf1ba810cb806dbaa4ff1b025c0cbc7d5039963efdbc255cf89a8c0125f7d2`
- 发布前备份：`/opt/ai-management/backups/20260730-user-id-session-r2-predeploy`
- 发布证据：`/opt/ai-management/var/reports/v2/20260730-user-id-session-77481e5-r2-deploy`

已验证：

- 本地按文件分批执行测试共 `100/100` 通过；单次全量命令 900 秒超时，未报告断言失败，已作为既有慢套件问题留在 `var/reports/20260730-user-id-session/issues.jsonl`。
- 迁移在临时 SQLite 数据库完成升级→降级→再次升级；jx PostgreSQL 已从 `e11c7f4a2d90` 升至 `f2a1d6c4b8e9`。
- jx API/Worker/Scheduler/测试页均运行新镜像；live/ready 和测试页健康入口通过。
- 合成用户回归验证第二轮复用本地会话、显式新建产生不同会话、管理列表/详情返回 `user_id` 与课程/见解/token/耗时字段；清理后会话数为 `0`，未写入知识库。

本次清洗格式核对结论：上传接口按所选策略的 `supported_extensions` 校验，不是所有策略都接受全部格式；`.docx` 由文档解析器转为 Markdown，`.pdf` 由 PDF 解析器转为 Markdown，`.md/.markdown/.txt` 读取为文本/Markdown，随后再交给策略自己的解析、脱敏和门禁。具体策略支持矩阵以 `docs/清洗策略接入说明.md` 和服务端策略声明为准。

日期化证据：[会话记忆与管理审计字段发布验收记录-2026-07-30](docs/会话记忆与管理审计字段发布验收记录-2026-07-30.md)。

## 0. 2026-07-30 接口契约与清洗全链路发布结论（最新）

本轮“接口契约、清洗全链路与 jx 发布验收”范围已经完成并通过，旧章节保留为历史记录。

当前线上版本：

- 核心镜像：`jx-now-ai-management:0.2.1-contract-cleaning-90a23f26-r3`
- 测试页镜像：`jx-now-ai-management-ui:0.2.1-contract-cleaning-90a23f26-r3`
- Alembic：`e11c7f4a2d90 (head)`
- wheel SHA-256：`90a23f26f51ff3dde4c5c4695e9f4584e4071951c09f896eb0dba99a56456fc2`
- 回滚备份：`/opt/ai-management/backups/20260730-predeploy-r3-contract-cleaning`

已通过的验收：

- 本地最终测试 `85/85`。
- jx 健康、迁移、Dify 契约、业务生命周期、停止生成、业务场景和接口契约共 8 组退出码全部为 `0`。
- 接口契约 `37/37`。
- 真实样本清洗 `15/15`，公开聊天与引用 `3/3`。
- 小报童确定性拆分 `3+3`；公众号确定性拆分 `29+84`。
- RAG：通用 Recall@5 `1.0`、课程 Recall@5 `0.9203`、聚合 Recall@5 `0.9444`，Hit@5 与关键问题命中率均为 `1.0`。
- 咨询 DOCX/PDF、身份脱敏、跨 SSE 分片过滤、Dify 管理命名、源文件级补偿回滚和课程/路线目录事务均已验证。

验收后的线上数据状态：

- 正式课程 `9`，正式路线 `2`，无额外课程或路线。
- Dify 课程知识库 `11` 份正式文档，通用知识库 `0` 份文档。
- 聊天、画像、进度、点击、清洗批次、清洗文档、外部删除任务和事件日志均为 `0`。
- 上传卷为空。
- Redis 有 `6` 个需保留的业务配置键；服务启动后另有 `5` 个 Celery 绑定/健康缓存运行时键，不属于测试数据。

证据与排坑记录：

- [接口契约与清洗全链路验收记录-2026-07-30.md](docs/接口契约与清洗全链路验收记录-2026-07-30.md)
- [接口清洗发布排坑记录-2026-07-30.md](docs/接口清洗发布排坑记录-2026-07-30.md)

仍未纳入本轮完成口径：20 并发 10 分钟压力测试、生产 Dataset 破坏性故障注入、小程序真实客户端端到端验收、40 条回答的人工语义门禁及灰度上线。后续执行时不得把本轮检索通过等同于这些项目已完成。

## 1. 历史结论（2026-07-23 至 2026-07-29）

> 本节至第 8 节保留早期联调审计链。标题中的“当前”“后续”等词均以记录当时为准；与第 0 节或当前 ADR 冲突时不得用于指导新任务。

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

### 1.4 当前交接状态（2026-07-29，接口专项核验）

- jx 当前已部署版本：`jx-now-ai-management:0.2.1-course-route-fc36f00-r4`；API、Worker、Scheduler、测试页均为同一 r4 发布版本。
- 对外接口标准以 `docs/小程序后端与AI管理服务接口对接标准.md` 为准。自 `10cd1ea` 以来，公开路由、请求头、请求体、SSE 事件名和返回字段未改变。
- 本地 `ddfbb67` 尚未发布，只增加路线确认语义的服务端校验，不要求小程序后端修改接口调用。
- 本次只进行接口契约专项测试，不重复页面上传、jx 真实链路或全量回归；专项测试 `17/17` 通过，结果已同步到本文和接口文档。
- 已知未关闭问题：全量回归中“第一阶段已经完成”未被当前阶段完成词表识别；该问题影响路线进度语义，不改变公开接口结构。

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

## 8. 历史新对话开场指令（已停用）

```text
请先完整阅读：
D:\work\projectc\jx-now\AGENTS.md
D:\work\projectc\jx-now\docs\README.md

根据 docs/README.md 的功能矩阵读取本次任务对应的当前规范、源码和测试。需要线上事实时只读取本交接文档顶部第 0 节；不要把第 1～8 节的历史“下一步”当成当前任务。

先一次性列出会实质影响结果的模糊点和关键假设。每次修改核对并更新受影响文档；未验证事项不得写成已完成。
```

## 9. 文档修改记录

后续可在任务范围内直接修改本文。每次修改完成后，在本节末尾追加记录，至少写明修改原因、修改前和修改后；不得在记录中复制密钥、正文、真实用户 ID 或其他敏感数据。

### 2026-07-30：明确滚动交接文档的直接维护与审计要求

- 修改原因：本文用于向后续协作者交接当前事实，需要允许任务执行者及时更新，同时保留可复核的口径变化。
- 修改前：项目规则允许按运行结果更新当前状态，但没有明确本文可以直接修改，也没有规定在文档结尾记录修改原因和前后差异。
- 修改后：本文可在任务范围内直接修改；每次修改必须在文档结尾追加一条记录，写明修改原因、修改前和修改后。
