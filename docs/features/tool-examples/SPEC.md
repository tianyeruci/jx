# 朋友圈与日常写作可配置案例目录 — Requirement Spec

- Feature ID: `tool-examples`
- Status: APPROVED
- Risk: `standard`

## 1. Goal

本轮共享路径在现有 ys 专用服务验证既有案例目录、朋友圈/写作会话协议及权限兼容；jx 正式服务仅作只读基线。EV-05/EV-06 通过实际 App/SSE 会话验证，调用和后台重试计入 `cleaning-tolerance-methodology` 的累计 ¥5/北京时间单日 ¥4 总账；本轮不新增案例目录功能或模拟数据替代真实验收。

在现有“朋友圈顾问”和“日常写作”工具中提供可点击的案例目录，让首次使用者能看到完整的模拟提示词并一键生成；同时允许管理员在不改 DSL 的情况下新增、编辑、启用/停用、删除和恢复案例。

## 2. Non-goals

案例目录本身不改变两个工具的 Dify 工作流；输出格式、公开流式协议、用户历史记录和鉴权模型保持兼容。本轮 EV-05/EV-06 证明有必要时，允许指定工具 App 的最小兼容修正与发布，先保留现用发布版本并按同一候选复验；不新增 Start 变量或改变公开协议，除非出现新的实质合同决定。不在本 Feature 内实现真实小程序页面，只维护当前仓库已有的 Streamlit 测试/管理 UI 和后端接口。

## 3. Background / Evidence

当前仓库没有小程序前端源码，已有测试 UI 只有自由输入框，用户无法从界面理解 `request` 应如何组织。两个工具的 Workflow Start 已支持 `request`、素材、受众、语气等运行时输入，因此案例内容应作为运行时 `request` 注入，而不是复制进 DSL。

## 4. Actors and Scenarios

| Actor | Scenario | Expected result |
|---|---|---|
| 普通用户/测试 UI 用户 | 打开朋友圈顾问或日常写作 | 看到该工具当前启用的案例卡片及完整模拟提示词摘要 |
| 普通用户/测试 UI 用户 | 点击案例 | 案例完整提示词显示在输入区，并立即调用既有工具生成接口 |
| 普通用户/测试 UI 用户 | 案例为空或不适合当前需求 | 仍可手动填写原有输入并生成 |
| 管理员 | 管理案例 | 可按工具查询全部案例，新增、编辑、启停、删除，且可恢复缺失默认案例 |

## 5. Functional Requirements

### FR-001

后端为 `moments`、`writing` 分别提供用户读取接口 `GET /internal/v1/tools/{kind}/examples`，仅返回启用案例，按 `sort_order`、创建稳定顺序返回；每项包含稳定 ID、标题、说明、完整 `request`、启用状态和排序值。

### FR-002

后端为管理员提供案例查询与 CRUD 接口：`GET/POST /internal/v1/admin/tools/{kind}/examples`、`PATCH/DELETE /internal/v1/admin/tools/{kind}/examples/{example_id}`，以及 `POST /internal/v1/admin/tools/{kind}/examples/restore-defaults`。管理员接口返回启用和停用案例，所有写操作都校验工具类型、字段长度和 ID 存在性。

### FR-003

默认案例覆盖：朋友圈顾问的“介绍生活”“介绍产品”“分享思考”，日常写作的“工作总结”“通知邮件”“改写润色”。默认 `request` 必须是可直接提交的完整模拟提示词，并在文本中明确领域/主题、受众和期望结果等关键信息。

### FR-004

测试 UI 在两个工具中展示案例；点击案例时将完整 `request` 放入输入区并立即复用原有 `/run` 请求，不能绕过原有鉴权、停止或 SSE 处理。管理员 UI 提供同一套案例的维护入口。

### FR-005

案例配置持久化在现有 `AdminSetting` JSON 存储中，按工具类型隔离，不新增数据库表或迁移；未配置时返回代码内置默认值，删除案例后不会自动回填，使用恢复默认接口显式回填。

## 6. Applicable Constraints

案例无数量上限；单项 `request` 不超过现有工具 `request` 的 12000 字符限制，标题不超过 100 字，说明不超过 500 字。普通读取沿用内部 Token 与用户标识要求，管理员写操作沿用双 Token 要求。案例不包含密钥、用户私密资料或服务端凭据。

## 7. State / Data / Permission Rules

配置 key 使用工具类型隔离的 JSON 文档；默认值为只读代码常量，首次管理员写操作时 materialize 到 `AdminSetting`。案例 ID 在同一工具内唯一；排序值相同按稳定创建顺序处理。用户读取不返回停用项，管理员读取返回全部项。

## 8. Error and Boundary Behavior

非法工具类型、字段超长、空 `request`、重复 ID、未知案例 ID、未知字段、空 PATCH 和不合法排序值返回 4xx；服务业务错误沿用 `ServiceError` 结构，Pydantic 请求模型错误沿用 FastAPI 422 `detail` 结构。并发写入以数据库事务最后一次成功写入为准。空启用目录返回 200 和空数组，不影响手动输入。

## 9. Assumptions and Resolved Decisions

Use stable decision IDs when a material choice is resolved so later implementation can trace it.

| Decision ID | Question / assumption | Decision | Evidence / reason | Alternatives rejected |
|---|---|---|---|---|
| RD-001 | 案例内容放在哪里 | 作为运行时 `request` 保存和提交，不增加 Dify Start 变量 | 现有 DSL 已有 `request`，避免 DSL 与案例目录双重维护 | 为每个案例复制/分叉 Workflow |
| RD-002 | 如何持久化 | 复用 `AdminSetting` JSON，按 kind 存储 | 已有管理员配置存储与事务边界，无需迁移 | 新增表或引入外部 CMS |
| RD-003 | 默认案例如何恢复 | 删除后由 `restore-defaults` 显式补回缺失默认 ID，保留自定义项 | 避免恢复操作丢失管理员新增案例 | 无提示地重置整个目录 |
| RD-004 | 前端范围 | 当前仓库只改 Streamlit 测试/管理 UI，真实小程序按接口消费 | 仓库没有小程序源码，后端接口可先独立验收 | 虚构并改动不存在的前端工程 |
| RD-005 | 风险与架构级别 | 维持现有 L2/Standard：接口只在内部 Token 边界内，配置可逆、无迁移、无 DSL 变更 | 变化局限于现有模块边界和测试环境管理配置 | 新服务、公开互联网 API 或高风险数据迁移 |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 两个工具首次无配置时分别返回 3 个默认启用案例，案例文本含完整模拟提示词 | 用户 GET 接口只返回启用项 |
| AC-002 | 工具页面案例卡片可查看完整 `request`；点击案例后输入区显示同一 `request`，并立即生成一次；生成链路与手动输入一致 | 覆盖 moments/writing 两种工具 |
| AC-003 | 管理员可新增、编辑、启停、删除案例，列表可查询全部案例；删除默认项后可恢复 | 自定义案例在恢复默认时保留 |
| AC-004 | 非管理员不能调用管理员案例接口；普通用户不能看到停用案例 | 验证双 Token 与读取过滤 |
| AC-005 | 案例目录为空时返回空数组，手动输入仍能正常生成；旧 `/run` 请求契约不变 | 无 DSL 变量或迁移变更 |
| AC-006 | 接口文档、索引、测试交接文档反映新增 API；本地质量检查通过 | 部署前后记录可复核证据 |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error/negative | Security/permission | Compatibility/migration |
|---|---|---|---|---|---|
| AC-001 | 默认值读取与字段完整性 | 空配置、停用全部、排序相同 | 非法 kind/超长 request | 普通读取不泄露停用项 | 旧配置缺失时无迁移也可读 |
| AC-002 | 两个工具卡片点击生成 | 多案例、长 prompt | 重复点击、SSE 错误 | 仍走原鉴权和既有 run 路由 | 旧手动输入行为不变 |
| AC-003 | 管理员 CRUD/恢复 | 自定义项、删除全部默认 | 重复/未知 ID、并发写 | 双 Token、无敏感字段 | AdminSetting 无迁移 |
| AC-004 | 权限边界 | Token 缺失/错误 | 普通用户访问 admin | 不能越权读写 | 既有权限中间件 |
| AC-005 | 空目录与旧接口 | 0/1/多案例 | 目录失败不阻断手动生成 | 不改变 DSL | API 向后兼容 |
| AC-006 | 文档与质量 | 精确路由索引 | 文档不一致 | 不写入密钥/私密样本 | Dify 无语义更新时不重复发布 |

The human approves the behavior/test intent. The human is not expected to author unit-test cases.

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | Standard/high feature source of truth | Generate |
| Adaptive Prototype | Not needed | 现有 Streamlit UI 足以验证卡片点击、立即生成和管理 CRUD；无独立前端需要试验 | Skipped |
| Implementation Spec | Required | Sol → Luna execution contract | Generate |
| ADR / Ops docs | Recommended | 接口文档必须更新；部署交接若产生新事实则追加现有交接记录，不另建版本文档 | Update existing |

## 13. Open Questions

None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
