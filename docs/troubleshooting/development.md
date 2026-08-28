# 开发与本地工具排障

## DEV-001：测试配置必须在应用导入前隔离

- 适用：pytest、迁移测试、本地 SQLite/Redis 隔离。
- 触发：fixture 已设置，但应用模块先导入并缓存了生产配置。
- 根因：配置对象存在进程级缓存，导入顺序决定实际数据库和 Redis。
- 解决：测试环境变量和隔离路径在导入应用前就绪；迁移使用临时 SQLite；远端只在容器内加载服务器环境。
- 非解决：应用导入后再覆盖环境变量，或仅检查 fixture 声明值。
- 验证：检查实际连接类型、主机和 `APP_ENV`，不得输出值。
- 预防：新增会导入应用的测试时，先建立隔离配置再导入目标模块。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:8)。
- 最后验证：2026-07-30。

## DEV-002：wheel 与源码必须一起核对

- 适用：离线构建、Dockerfile.offline、jx 发布。
- 触发：源码已同步但容器仍执行旧逻辑。
- 根因：离线镜像从 `wheelhouse` 安装包，单独复制源码不会改变已安装包。
- 解决：先构建并替换目标 wheel，再构建镜像；发布前后比较 wheel SHA-256 和包内 Python 文件。
- 非解决：只把源码复制到服务器或容器内临时修改。
- 验证：API、Worker、Scheduler 的 wheel SHA-256、镜像 ID 和包内目标文件一致。
- 预防：发布清单固定记录 wheel、镜像和迁移 head 的同源关系。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:289)。
- 最后验证：2026-07-30。

## DEV-003：不要用工作树全量添加替代范围审计

- 适用：提交和发布前。
- 触发：构建产物、真实资料、临时报告和业务输入同时出现在工作树。
- 根因：全量暂存绕过变更范围审计，无法区分源码、产物和敏感输入。
- 解决：先按功能域查看状态和差异，按逻辑分组暂存；不执行 `git add .`，不把 `.env`、真实样本、缓存或报告加入提交。
- 非解决：先全量暂存，再依赖人工从索引中逐个排除。
- 验证：暂存差异只包含当前逻辑分组，且敏感与生成文件均未进入索引。
- 预防：提交前同时检查工作树状态和暂存差异。
- 证据：[Git 提交与推送避坑记录](../Git提交与推送避坑记录-2026-07-29.md:7)。
- 最后验证：2026-07-29。

## DEV-004：Windows Skill 工具要显式使用 UTF-8

- 适用：`skill-creator` 校验或生成含中文的 Skill 与 `agents/openai.yaml`。
- 触发：脚本按系统默认 GBK 读取 UTF-8 文件而报 `UnicodeDecodeError`，或生成的中文元数据被 UTF-8 消费者显示为乱码。
- 根因：Windows 进程默认代码页与仓库 UTF-8 文本约定不一致。
- 解决：使用 Python UTF-8 模式运行 Skill 工具，例如 `python -X utf8 quick_validate.py <skill-path>` 或 `python -X utf8 generate_openai_yaml.py <skill-path> ...`。
- 非解决：为适配本机默认代码页而把仓库 Markdown 或 YAML 转成 GBK。
- 验证：校验器返回 `Skill is valid!`，并用 UTF-8 读取 `openai.yaml` 确认无替换字符。
- 预防：Windows 上处理含中文的仓库级 Skill 时固定使用 `python -X utf8`。
- 证据：[项目文档契约测试](../../tests/test_project_documentation_contract.py)。
- 最后验证：2026-07-30。

## DEV-005：文档索引检查不能导入生产应用

- 适用：源码结构、接口或工程入口的本地文档检查。
- 触发：为生成接口清单而直接导入 `ai_management.main`，在未提供生产凭据的环境中失败。
- 根因：应用模块导入链会初始化配置与基础设施依赖；文档索引只需要已注册 Router 和声明式入口，不需要运行服务。
- 解决：静态解析 `main.py` 的已注册 Router、路由装饰器和限定入口文件；再在测试环境中把静态结果与实际 FastAPI 路由集对照。
- 非解决：为文档生成临时注入生产 Token、读取 `.env`，或依靠运行时服务启动后的 OpenAPI。
- 验证：`python scripts/generate_project_index.py --check` 在移除服务凭据的生产环境变量下通过；文档契约测试确认静态路由集等于测试配置下的应用路由集。
- 预防：新增可索引入口时优先保持声明静态可解析；索引器不得导入应用模块或输出环境配置。
- 证据：[项目文档契约测试](../../tests/test_project_documentation_contract.py)。
- 最后验证：2026-07-30。

## DEV-006：PDF 版式结构必须在脚本层显式恢复

- 适用：jx 离线 `pdf_markdown_refine.py` 的文本 PDF/OCR PDF 版式调试。
- 触发：跨页表格断开、打印目录被拼成一段、长文标题层级压平，或 RapidOCR 输出页脚/句内断行。
- 根因：`pdfplumber` 表格提取按页返回且没有跨页/正文阅读顺序语义；RapidOCR 只返回行级框、文本和置信度，不提供段落、标题或页脚语义。
- 解决：文本分支用相邻页边界、列数和首列空值做表格状态机；用文档相对字号/粗体/编号映射标题；用点线和页码恢复目录列表；OCR 先收集全页，再按底部区域和归一化重复值过滤页脚，并对未结束句做续行。
- 非解决：直接启用 `pdfplumber` `text/text` 表格策略，或把 RapidOCR 行框结果直接当作段落/标题；前者已验证会把普通双栏正文误报为表格。
- 验证：JX 运行 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260804-234210/`：5/5 成功、3 个 `pdfplumber`、2 个 `RapidOCR`；目录 25 项，跨页几何表格 1 块，OCR 页脚模式/代码围栏/`None` 均为 0，输出权限均为 `600`。
- 预防：新增 PDF 样本必须同时检查目录项数、标题层级、表格块数、页脚模式、代码围栏、`None` 和非空/权限；无边框复杂表格、多栏阅读顺序和 OCR 表格结构列为人工复核限制。
- 证据：[版式评审与落地建议](../执行计划合集/文字型 PDF 转 Markdown 评审与落地建议.md)、[脱敏问题记录](../../var/reports/20260804-pdf-markdown-refine/issues.jsonl)。
- 最后验证：2026-08-04。

## DEV-007：打印目录应按结构信号平铺，不得按样本标题白名单

- 适用：服务器离线 `pdf_markdown_refine.py` 的打印目录回退解析。
- 触发：目录输出混入子目录、页码或嵌套列表，且编号/标题与正文不一致。
- 根因：打印目录不是 PDF 语义对象；点线、编号和页码可能被 `pdfplumber` 拆行或拼行，底层库不会替业务判断父子层级。把样本条目写成固定规则会在另一种目录格式下失效。
- 解决：保留原始编号/标点和标题；按编号层级、子项标点、目录行 `x0`/缩进及同级序列选择顶层；删除点线和页码；统一输出无嵌套的 `- 标题`。解析到候选但无法判定时不凭标题白名单强行改写，保留可解释的边界。
- 非解决：固定保留某几个标题、把所有数字行都当一级目录、或直接把点线页整页放进代码围栏。
- 验证：JX 运行 `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260805-010112/`：5/5 成功；长目录 25 项、嵌套 0、页码 0；3 个 `pdfplumber`、2 个 `RapidOCR`，输出权限均为 `600`。
- 预防：新增样本至少核对一级条目数、正文标题前缀一致性、嵌套列表数和页码残留；相同编号/标点且无缩进的父子关系列为人工复核限制。
- 证据：[版式评审与落地建议](../执行计划合集/文字型 PDF 转 Markdown 评审与落地建议.md)、[脱敏问题记录](../../var/reports/20260804-pdf-markdown-refine/issues.jsonl)。
- 最后验证：2026-08-05。

## DEV-008：标题语义不能由粗体单独决定，换行应先于标题分类

- 适用：`pdf_markdown_refine.py` 的文字型 PDF 与 RapidOCR 标题/段落恢复。
- 触发：粗体正文被输出成 H2/H3；有目录的正文出现多个 H1；一个跨行标题被拆成两个标题；OCR 连续句被误拆或多个段落粘连。
- 根因：PDF/OCR 只提供视觉属性和行坐标，不提供“标题/正文”语义；旧逻辑把粗体行直接映射为标题，并在行合并前分类。
- 解决：目录标题匹配、相对字号、编号、独立行和上下间距联合判定；粗体且正文同字号默认不升级；有目录时 `# 目录` 为唯一 H1，正文标题从 H2 开始；相同标题信号的相邻行先合并；段落用间距、句末标点、字段标签和未结束句续行恢复；清理文本中原始 `# `，避免伪 Markdown 标题。
- 非解决：把所有粗体、所有编号行或 `layout=True` 的近似结果直接视为标题；把 RapidOCR 行结果直接视为段落。
- 验证：JX `/opt/ai-management/tmp/pdf-markdown-refine/runs/20260805-024839/`：5/5 成功，3 个 `pdfplumber`、2 个 `RapidOCR`；目录文档 H1 仅为目录、长标题跨行合并、粗体正文误判 0、OCR 页脚/代码围栏/`None`/错误标记均为 0。
- 预防：新增样本同时检查 H1 数量、粗体正文误判、标题跨行重复、段落合并和 OCR 页脚；同字号、无目录匹配且无明显间距的编号行保守按正文/列表处理。
- 证据：[版式评审与落地建议](../执行计划合集/文字型 PDF 转 Markdown 评审与落地建议.md)、[脱敏问题记录](../../var/reports/20260804-pdf-markdown-refine/issues.jsonl)。
- 最后验证：2026-08-05。

## DEV-009：jx 百炼脚本只使用 BAILIAN_API_KEY

- 适用：`Other/event_rag_cleaner` 在 jx 上执行 Qwen realtime。
- 触发：运行 `Other/event_rag_cleaner` 的 Qwen 模式时未配置 `BAILIAN_API_KEY`。
- 根因：当前脚本凭据契约只有 `BAILIAN_API_KEY`；`DASHSCOPE_API_KEY` 不再作为 fallback 或兼容入口。
- 解决：通过进程环境或项目 dotenv 提供 `BAILIAN_API_KEY`；缺失时只提示 `BAILIAN_API_KEY`。仅设置 `DASHSCOPE_API_KEY` 时应在本地凭据检查阶段失败，不发起网络请求。
- 非解决：把密钥值写入脚本、测试报告或日志，或把 `DASHSCOPE_API_KEY` 重新作为 fallback。
- 验证：本地 `py_compile` 与完整 `pytest`；测试覆盖 BAILIAN_API_KEY 成功读取，以及仅有 DASHSCOPE_API_KEY 时缺少 BAILIAN_API_KEY 且不触网。
- 预防：新增真实测试前只核对变量是否存在；测试报告只保留变量名、计数和脱敏摘要，不输出值。
- 证据：[验证记录](../../Other/event_rag_cleaner/VALIDATION.md:5)。
- 最后验证：2026-08-16。

## DEV-010：Windows 升级后若 `code_mode_host` 被重新打开，会让 Desktop 新任务回到脆弱路由

- 适用：Windows 上的 Codex Desktop / 本地 CLI 共用 `C:\Users\Free\.codex\config.toml`，且升级后新任务通过 `functions.exec` 进入工具层。
- 触发：升级后所有新任务在真正启动 PowerShell、`apply_patch` 或网页检索前，就统一报 `code-mode host is disabled`；而 `mcp__codex_app` 这类直连任务管理工具仍可用。
- 根因：Windows 侧稳定执行通道依赖 `[features].unified_exec = true`；若升级把 `[features].code_mode_host` 重新置回 `true`，Desktop 会优先暴露/协商 Code Mode 主机路由，新任务因此偏离历史上已验证的 Windows 稳定组合，并再次触发 host 路由兼容故障。
- 解决：全局配置保持以下组合作为 Windows 基线，并在修改后完整退出再重开 Desktop：
  ```toml
  [features]
  unified_exec = true
  code_mode_host = false

  [features.code_mode]
  enabled = false
  ```
- 非解决：只重建 runtime 缓存、只重装 PowerShell、只新建任务，或只切换模型；这些操作都不能纠正已经偏离的全局路由配置。
- 验证：`codex features list` 中应同时看到 `unified_exec true`、`code_mode_host false`、`code_mode false`；随后新会话需要再次成功执行最小本地命令，例如 `Get-Location`。
- 预防：每次 Desktop 升级后优先检查 `config.toml` 是否仍保留上述 3 个开关组合；若发现 `code_mode_host = true`，先恢复基线再做业务任务。
- 证据：[Codex 会话工具故障：完整链路复盘与修复（2026-07-28）](../Codex会话工具报错复盘与修复-2026-07-28.md)、[OpenAI Codex 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[ChatGPT & Codex changelog](https://learn.chatgpt.com/docs/changelog)。
- 最后验证：2026-08-27。
