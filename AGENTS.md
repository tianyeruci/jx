# jx-now 项目指令

本文件只保存每次任务都需要的稳定规则和导航。当前版本、测试结果、服务器地址、临时计划与完整故障过程放在专项文档，不复制到这里。

## 开始任务

- 先从 `docs/README.md` 和 `docs/功能与接口索引.md` 定位当前权威文档、相关源码、测试和更新触发器；不要默认全量扫描 `docs/` 或源码树。
- 根目录业务来源材料仅按 `docs/README.md`“需求来源与追踪”在命中场景时读取；它们不是每次必读，也不直接构成需求基线。
- 当前实际结构、入口、职责和数据流见 `docs/项目目录结构.md`；未来目标见 `docs/V2目标架构.md`，不能把目标结构当成已完成事实。
- 开发、测试、部署、Dify 联调或数据清理任务使用项目 Skill `maintain-project-troubleshooting`，只读取对应领域的排障文档；跨域任务才读取多份。
- 需要判断当前线上事实时，只读 `AI管理服务当前状态与后续测试交接.md` 顶部最新状态区；该文件不是需求或设计基线。
- `AI管理服务当前状态与后续测试交接.md` 是给后续协作者使用的滚动交接，可在任务范围内直接修改；每次修改必须在文档结尾追加记录，写明修改原因、修改前和修改后。
- 写入前一次性确认会实质影响范围、安全、权限、成本或不可逆结果的歧义；次要歧义采用安全、可逆默认值并说明假设。
- 同一任务内已读取且未变化的文件不机械重读。仅在跨域、索引失真、结构变化或文档冲突时扩大搜索。

## 事实与权威

- 以仓库证据、可复核结果和最终任务效果为准；区分事实、推论、建议和不确定信息。
- 用户前提与证据冲突时直接指出，并基于更可靠的前提继续。
- 文档冲突按以下顺序裁决：
  1. 用户当前明确确认的规则，以及已写入 `docs/V2决策记录.md` 的最新 ADR。
  2. `docs/V2决策记录.md`、V2 需求、产品边界和设计。
  3. 当前专项接口、清洗、测试与部署规范。
  4. 当前状态文档顶部的最新运行事实。
  5. 日期化验收、发布和排坑记录。
  6. 已标记为历史的旧计划、交接和实测快照。
- 代码和运行结果是核验材料，不能单独、隐式地改写已确认需求。

## 修改与验证

- 保留用户改动和无关工作树变化；不清理、覆盖或提交无关文件。
- 缺陷有合适测试面时先写失败测试，再修复；只运行仓库已有且与改动风险相称的检查。
- 高风险发布按当前验收方案完成基线、统一修复、本地回归、备份、远端回归和精确清理。基线阶段不得中途修改服务行为。
- 测试工具阻塞时保留原始脱敏证据，完成可执行部分后统一修正并从头重跑。
- 任何“已完成、已部署、已清理、可回滚”结论都必须有可定位证据，不能只依据退出码或口头状态。

## 文档同步

每次修改在完成前按影响检查并只更新受影响资料：

| 变更域 | 至少核对 |
|---|---|
| API、SSE、鉴权、错误结构 | 接口交互版本、正式接口标准、API/SSE 说明、契约测试 |
| 清洗、隐私、命名、Metadata、原子性 | 清洗规范、清洗接入说明、验收测试 |
| 会话、课程、路线、点击、任务 | V2 需求/产品/设计、对应接口与测试 |
| 数据库、Redis、迁移 | 数据设计、迁移、备份回滚与测试 |
| Dify DSL、Prompt、知识库绑定 | Dify 指南、相关 Prompt/DSL、契约与 RAG 验证 |
| 部署、配置、回滚 | 当前运行手册、状态交接、日期化验收/排坑记录 |
| 目录、入口、职责变化 | `docs/项目目录结构.md` 与 `docs/README.md` |

- 动态结果写当前状态和日期化记录；稳定产品或架构决定写 ADR；原始脱敏报告写 `var/reports/` 且不提交。
- HTTP 接口或工程入口（CLI、Worker、Compose、Dify、发布/RAG 脚本）变化时，更新 `docs/功能与接口索引.md` 的自动区并通过其本地检查；功能语义、测试或规范映射变化时同步其人工区。
- 业务来源材料变化时先更新来源追踪并比较语义增量；已确认增量同步到对应 ADR、需求/专项规范和测试，未确认增量不得指导实现。
- 可复用故障经验只有在根因和解决方案验证后，才由 `maintain-project-troubleshooting` 更新对应长期文档；未验证项只留脱敏证据。
- 详细分层和更新机制见 `docs/Codex项目上下文与文档维护规则.md`。

## 数据与发布红线

- 不输出、提交或写入报告：`.env` 密钥、Token、正文、真实用户 ID、原始敏感文件名、未脱敏样本和受保护映射。
- 正式课程、路线、配置和业务文档默认保留。删除 Dify 数据必须能由精确 document ID、测试标记或本服务关联记录证明属于测试；仅凭名称正则不得删除。
- 整库清 Redis 前必须证明目标 DB 为本服务独占且已有可恢复备份；否则只删除精确键。
- 真实样本、生产数据、远端部署和 Dify 发布只在任务明确授权的范围内操作；发布前后都要快照、核对和精确清理。
- 不直接修改 Dify 业务数据库，不在容器内热改代码，不删除持久卷，不执行 Git push，除非用户另行明确授权。

## 维护本文件

- 只在规则会重复影响后续任务、同类错误反复出现或文档路由发生稳定变化时更新本文件。
- 一次性要求、版本号、测试数字、主机信息、长命令和故障流水不得加入本文件。
- 复杂可复用流程优先做成 Skill 或脚本；可机械执行的规则优先交给测试、lint、schema、hook 或 CI。
- 仅当某个子目录形成长期独立规则时才增加嵌套 `AGENTS.md`；当前不预先创建。

<!-- codex-repo-governance:begin -->
# Codex Repository Governance

## Sources of truth
- Current project status: `docs/PROJECT_STATUS.md` (generated).
- Architecture baseline: `docs/architecture/PROFILE.md`.
- Active feature requirements: `docs/features/<id>/SPEC.md`.
- Active implementation contract: `docs/features/<id>/IMPLEMENTATION.md`.
- Machine state/config: `.codex/governance/project.json`.
- Local active-work state under `.codex/state/` is generated and must not be committed.

## Workflow
- Route substantive development through the user-level `dev-workflow` skill.
- Preserve existing architecture unless an approved Implementation/Engineering Change authorizes structural migration.
- For Standard/High feature/engineering work, use `gov approve-requirements` and `gov approve-implementation` only after the explicit human gates; `gov set-phase implementation` verifies their content fingerprints and refuses stale approval.
- Before the Implementation Gate, do not edit production/project source. Requirements/architecture/governance documents and approved disposable prototypes are the only pre-implementation write targets.
- Bug fixes use evidence-driven diagnosis and do not require a full feature-doc chain when expected behavior is already clear.

## Functional module ownership
- Standard/High Feature contracts register module path/glob ownership before implementation approval.
- A shared implementation path may affect multiple active Features; all related current Feature docs must be synchronized before completion.
- Module status is derived from implementation/Quality/completion; do not manually claim completion to bypass validation.
- Retired Features remain historical and no longer participate in current path ownership.

## Quality
Use the canonical commands in `.codex/governance/project.json`.
Before completion, run:
- Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File .codex/governance/gov.ps1 quality`
- WSL/Linux: `python3 .codex/governance/gov.py quality`

## Documentation
- One current SPEC and IMPLEMENTATION file per feature; do not create versioned duplicates.
- Implementation writes automatically mark the bound feature docs stale and regenerate `docs/PROJECT_STATUS.md`; do not hand-edit that generated file.
- For Standard/High bound work, clear stale docs with `gov sync-docs --reason "..." --summary "..."` only after semantic docs match the code. The CLI appends the semantic before/after CHANGELOG entry and refuses a no-summary bypass.
- Update SPEC/IMPLEMENTATION/Architecture Profile only when their actual semantics changed, because such edits intentionally invalidate the corresponding approval.
- Create ops/incident/ADR docs only when triggered by a real need.

## Safety
- No silent production push/merge/deploy, irreversible migration, destructive resource change, permission/secret change, or unapproved production dependency.
- Never commit secrets.
- Use one write owner per checkout; use worktrees for parallel write tasks.
- Common remote/production Bash actions are protected by repo hooks and require an exact recorded release approval after the user explicitly approves the command.
<!-- codex-repo-governance:end -->
