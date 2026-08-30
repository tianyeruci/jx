# 朋友圈顾问 IP 方法论 Prompt 与案例增量 — Requirement Spec

- Feature ID: `moments-ip-methodology`
- Status: DRAFT
- Risk: `standard`

## 1. Goal

将已确认的“朋友圈 IP 人设打造”方法论吸收到现有朋友圈顾问 Workflow 的实际生成行为中，并增量补充与五类内容方法对应的朋友圈案例。保持现有输入、SSE、无状态调用、鉴权和写作助手不变；继续使用现有 Dify App、现有 API Key 和现有 qwen-plus，不新增 App、Key、模型或插件。

## 2. Non-goals

不改日常写作助手；不新增 `category` 输入字段、Dify Workflow 节点、数据库表、迁移、知识库检索、发布排期、配图或评论运营功能；不删除或覆盖旧案例、现有 Dify App、现有 API Key、旧业务数据和正式知识库数据。

## 3. Background / Evidence

项目当前朋友圈 Workflow 为 `dify/dsl/moments_advisor.yml` 的 Start→LLM→End 图，运行 Prompt 内嵌在 LLM 节点；案例目录通过 `AdminSetting` 的 `tool_examples:moments` JSON 持久化。老板文档提供的是五类朋友圈内容方法论、辅助写作要求和发布前检查，不是可直接导入的 DSL 或运行时 Prompt。现有案例覆盖生活、产品和思考，需在不影响旧数据的前提下增加反馈、专业干货和互动案例。

## 4. Actors and Scenarios

| Actor | Scenario | Expected result |
|---|---|---|
| 普通用户 | 输入生活、反馈、干货、产品或互动主题 | 朋友圈顾问自动采用最匹配的一类，只输出可直接使用的内容 |
| 普通用户 | 指定字数、语气、素材或禁用要求 | 保留现有输入契约并优先遵守用户明确限制，不编造事实 |
| 普通用户 | 点击旧案例或新增案例 | 旧案例继续可用；新增三类案例可通过既有案例目录接口和测试页生成 |
| 管理员/发布人员 | 更新现有 Dify Workflow 和 jx 配置 | 现有 App、现有 Key、jx 配置、容器和真实 SSE 分别可核验；旧案例和旧业务数据保留 |

## 5. Functional Requirements

### FR-001

朋友圈顾问应根据 `request` 和现有输入字段自动选择最匹配的生活、好评反馈、专业干货、产品推广或互动方法；用户明确的任务、素材、限制和字数要求不得被方法论默认值覆盖。

### FR-002

生成规则必须继续禁止虚构用户经历、客户评价、数字、收益、产品能力和权威背书；商业内容避免绝对化承诺和硬广；输出保持单主版本、自然易读、只包含用户可见结果。

### FR-003

在 `DEFAULT_EXAMPLES["moments"]` 中新增稳定 ID 为 `moments-feedback`、`moments-knowledge`、`moments-interaction` 的案例；现有 `moments-life`、`moments-product`、`moments-thought` 的字段和排序不得改变。

### FR-004

jx 当前案例目录通过既有管理员接口增量写入新 ID；写入前保存当前快照，重复 ID 或无法证明旧数据未变化时停止，不调用删除、全量重置或 `restore-defaults`。

### FR-005

本地检查通过后，在现有“朋友圈顾问” Dify App 上更新并发布本地 DSL 对应的 Workflow；保持现有 qwen-plus、现有 API Key 和现有入口参数不变，并只更新 jx 测试环境代码。

### FR-006

当 `source_material` 为空时仍保持既有 request-only 调用兼容；Prompt 应使用概括表达、占位符或用户明确给出的事实，不把主题和写作目标扩写成具体事实。该约束属于模型写作规则，不改变 API/SSE 契约。

## 6. Applicable Constraints

- 现有 `POST /internal/v1/tools/moments/run`、`GET /internal/v1/tools/moments/examples`、管理员案例 CRUD 和 SSE 事件契约保持不变。
- 本次不引入数据库迁移；案例仍存储在现有 `AdminSetting` JSON 中。
- API Key、Token、真实用户标识、原始正文和受保护映射不得写入仓库、报告或回复。
- jx 发布只针对测试环境，使用 `--keep-data` 语义；禁止清理正式数据、课程数据、知识库或 Redis 整库。
- Dify 发布与 jx Key 映射必须分开记录，并使用测试页面完成真实链路回归。

## 7. State / Data / Permission Rules

- 新增案例是正式业务配置，不属于本轮测试清理对象。
- 旧案例、旧 `AdminSetting` 项、现有 Dify App/Key、旧数据库/Redis/知识库数据均为保留对象。
- 测试数据只有在最终回归通过后，且能由本轮精确运行 ID、任务 ID、Dify 记录 ID 或 manifest 证明归属时才允许清理。
- 无法精确删除的 Dify 平台日志不得通过业务数据库或模糊匹配强行清理，应保留并记录限制。
- 管理员案例写入继续使用现有内部 Token + 管理员权限边界。

## 8. Error and Boundary Behavior

- 本地契约、质量门禁、Dify 发布、jx ready、Key 映射或测试页面 SSE 任一失败，都不能进入最终清理和“已生效”结论。
- 测试失败先保留精确证据，统一修复后重新执行本地检查、Dify 发布、jx 部署和测试页面回归。
- 现有 Workflow 发布或 jx 部署失败时恢复发布前 Workflow/镜像；不删除现有 App/Key，不删除业务数据。
- 新增案例 ID 已存在、案例快照发生非预期变化或旧案例指纹不一致时停止写入。

## 9. Assumptions and Resolved Decisions

Use stable decision IDs when a material choice is resolved so later implementation can trace it.

| Decision ID | Question / assumption | Decision | Evidence / reason | Alternatives rejected |
|---|---|---|---|---|
| RD-001 | 老板文档是否作为知识库资料导入？ | 不导入知识库；将其方法论提炼为朋友圈顾问运行 Prompt/DSL 规则。 | 文档是写作方法、分类和检查清单，不是需要检索的事实资料；当前 Workflow 已有运行 Prompt。 | 新建知识库/RAG，增加检索不确定性。 |
| RD-002 | 是否新增 category 输入字段或节点？ | 不新增；由 Prompt 根据现有 request、素材和约束自动判断，无法明确判断时沿用通用行为。 | 保持现有 API/SSE/前端契约，最小改动。 | 新字段、新节点或前端改造。 |
| RD-003 | 旧案例如何处理？ | 原三条案例完全保留，新增 moments-feedback、moments-knowledge、moments-interaction 三条。 | 旧数据不删除且不受影响；新增 ID 可独立回滚。 | 覆盖旧案例、全量 restore defaults、重排旧案例。 |
| RD-004 | 新 DSL 与 API Key 如何发布？ | 更新并发布现有“朋友圈顾问” Dify App；继续使用现有 API Key、qwen-plus 和入口参数，不创建新 App、Key 或插件。jx 代码使用 `--keep-data` 发布。 | 用户已明确现有 DSL 和模型均在使用；新增 App/Key 会扩大范围且不是本次需求。 | 新建 App、生成新 Key、切换模型或安装插件。 |
| RD-005 | 测试数据如何清理？ | 最终真实回归通过后，仅按本轮精确 ID/运行 ID/manifest 清理；无法精确定位的平台日志不清理。 | 避免误删旧案例、正式数据或他人测试数据。 | 按名称、时间或模糊文本批量删除。 |

## 10. Acceptance Criteria

| ID | Observable result | Notes |
|---|---|---|
| AC-001 | 朋友圈顾问对五类输入能够自动采用匹配方法；现有输入字段、无状态调用、SSE 事件和鉴权契约不变。 | 本地 Prompt/DSL 静态与契约检查通过。 |
| AC-002 | 输出保留用户明确素材、语气、字数和禁用要求，不虚构经历、评价、数字、收益、能力或背书，并保持单主版本。 | 以 Prompt 规则检查和测试样例验证。 |
| AC-003 | 旧三条案例的 ID、字段、内容和排序不变；新增三条案例以稳定新 ID 出现在默认值、本地接口和 jx 测试环境。 | 通过案例快照/指纹和既有管理员接口核验。 |
| AC-004 | 现有 Dify App 已更新并发布正确 DSL，入口参数、qwen-plus 和现有 Key 保持不变；部署后测试页真实 SSE 成功，request-only 与有素材调用均保持既有链路。 | 使用现有 App/Key，失败可回退到发布前 Workflow/镜像。 |
| AC-005 | 最终回归通过后，测试数据按 manifest 精确清理；旧案例、旧 App/Key 和正式数据仍存在且指纹未变。 | 清理证据可定位；无法精确删除的日志明确记录保留。 |

## 11. Model-generated Test Intent

| Acceptance | Happy path | Boundary | Error/negative | Security/permission | Compatibility/migration |
|---|---|---|---|---|---|
| AC-001 | 五类代表性 request 生成有效朋友圈文本。 | 模糊主题走通用规则；明确字数/语气优先。 | DSL/契约检查失败时阻断发布。 | 不泄露 Prompt、Key 或内部字段。 | API/SSE/鉴权请求结构不变。 |
| AC-002 | 反馈、干货、产品和互动文本按 Prompt 使用给定事实并尽量保持概括。 | 素材为空、约束冲突、商业表达过强时保持保守。 | 检查虚构、绝对化承诺、硬广和多版本输出；模型输出仍需以真实回归观察为准。 | 输出仅含用户可见结果。 | 既有调用方无需改字段。 |
| AC-003 | 默认值与 jx 目录新增三条案例。 | 重复 ID、旧快照变化时停止。 | 不调用删除、全量重置或覆盖更新。 | 写入需现有管理员权限。 | 旧案例逐项指纹一致。 |
| AC-004 | 现有 App 发布后 jx 测试页 SSE 成功；空素材走固定兜底。 | 现有 Key/App 映射可核对。 | 发布或 ready/SSE 失败则恢复发布前版本并保留证据。 | Key 不进入仓库、报告或聊天。 | 使用 --keep-data，不改数据卷。 |
| AC-005 | 精确清理本轮测试运行。 | 仅清理 manifest 明确对象。 | 无精确删除能力时不强删。 | 不触碰正式数据和旧数据。 | 清理后旧对象指纹复核。 |

The human approves the behavior/test intent. The human is not expected to author unit-test cases.

## 12. Artifact Plan

| Artifact | Required / Recommended / Not needed | Reason | Decision |
|---|---|---|---|
| Requirement Spec | Required | Standard/high feature source of truth | Generate |
| Adaptive Prototype | Not needed | 现有测试页、API/SSE 和案例目录足以验证本次增量；没有新的 UI/交互决策。 | Skip |
| Implementation Spec | Required | Sol → Luna execution contract | Generate |
| ADR / Ops docs | Recommended | 发布涉及现有 App 原地更新、jx 测试回归和精确清理，应把实际结果写入当前交接/日期化记录；不新增架构 ADR。 | Update after verified release |

## 13. Open Questions

- None

<!-- gov:requirements-approval:begin -->
## 14. Approval
- Material questions resolved: YES
- Acceptance/Test Intent approved: YES
- Requirements approved: YES
<!-- gov:requirements-approval:end -->
