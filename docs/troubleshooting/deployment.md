# 部署与回滚排障

## DEPLOY-001：jx 主机回环地址不代表 API 故障

- 适用：jx Compose 部署。
- 触发：在宿主机调用 `127.0.0.1:8000` 失败，但 API 容器健康。
- 根因：API 端口只在 Compose 网络或私网绑定，宿主机回环未开放。
- 解决：健康检查在 API 容器内执行；一次性 runner 使用 Compose 服务名 `api:8000`；不临时开放公网端口。
- 非解决：为了通过宿主机回环检查而临时暴露 API 公网端口。
- 验证：容器内健康检查和 Compose runner 通过，宿主机未新增端口映射。
- 预防：运行手册按调用位置固定健康检查地址。
- 证据：[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:36)。
- 最后验证：2026-07-30。

## DEPLOY-002：API、Worker、Scheduler 必须使用同一构建

- 适用：wheel/离线镜像发布。
- 触发：API 与 Worker 行为或迁移版本不一致。
- 根因：多个服务从不同 wheel、镜像或构建时点启动。
- 解决：从同一 wheel 构建核心镜像，使用不可变版本标签；发布前后核对镜像 ID、wheel SHA-256、迁移 head 和包内源码。
- 非解决：只重建 API，或在容器内热改代码。
- 验证：API、Worker、Scheduler 的镜像 ID、wheel SHA-256 和迁移 head 一致。
- 预防：一次发布只接受一个核心构建标识，并在切换前生成同源快照。
- 证据：[V2 部署回滚手册](../V2从开发到服务器部署与回滚手册.md:20)。
- 最后验证：2026-07-30。

## DEPLOY-003：发布前先做可恢复备份

- 适用：任何 jx 写入、迁移、清理或镜像切换。
- 触发：准备执行远端写入，但缺少可恢复备份、目标身份或回滚点证据。
- 根因：只有备份文件而未验证恢复，不能证明数据和配置可回滚。
- 解决：备份 AI 数据库、Dify Redis RDB、上传卷、环境配置副本、旧镜像和状态快照；数据库备份必须恢复到临时库验证表结构。
- 非解决：只依据备份命令退出码，或仅保存未验证的单一数据库文件。
- 停止条件：备份、恢复验证、目标主机指纹、配置隔离或回滚点任一缺失。
- 验证：临时恢复成功、关键表结构可读，且回滚镜像和配置快照可定位。
- 预防：远端发布清单把恢复演练作为写入前门禁。
- 证据：[V2 部署回滚手册](../V2从开发到服务器部署与回滚手册.md:155)。
- 最后验证：2026-07-30。

## DEPLOY-004：宿主机 pyproject 落后会让源码重打 wheel 丢失既有依赖

- 适用：服务器使用宿主机 `pyproject.toml` 将上传源码重新打 wheel，再从离线 wheelhouse 构建镜像。
- 触发：当前线上镜像能导入某依赖、wheelhouse 也已有对应 wheel，但新镜像的全模块导入报 `ModuleNotFoundError`。
- 根因：宿主机 `pyproject.toml` 比当前仓库/线上构建元数据旧；重打 wheel 后 `Requires-Dist` 丢失，Docker 只安装新 wheel 声明的依赖。
- 解决：保持服务不切换，核对“仓库声明、宿主机声明、当前镜像已安装包、wheelhouse 工件”四方证据。只有确认依赖已是当前线上组成且离线工件存在时，备份并同步宿主机 `pyproject.toml`，重新构建；否则停止快速流程并走完整依赖发布。
- 非解决：在运行容器内 `pip install`、跳过全模块导入，或只因 wheelhouse 有文件就假设新 wheel 会安装它。
- 验证：新核心/UI 镜像全模块导入通过，7 个服务同源切换，ready/页面/日志通过；旧服务在切换前始终健康。
- 预防：正式发布同步宿主机构建资产；快速部署先比较构建元数据与当前 wheel 依赖，并保留切换前导入门禁。
- 证据：[快速部署验证记录](../jx快速代码测试部署验证记录-2026-08-01.md)。
- 最后验证：2026-08-01。

## DEPLOY-005：预检失败的 EXIT trap 必须在变量离开作用域后仍能恢复服务

- 适用：jx 上会先停止测试页或核心服务、再执行前置门禁的 Bash 运维脚本。
- 触发：门禁失败后出现 `services_may_need_restart: unbound variable`，测试页持续返回 502。
- 根因：恢复标记和临时目录只存在于函数局部作用域；`set -u` 下，shell 退出时 EXIT trap 已无法读取它们，服务恢复被二次异常中断。
- 解决：把 trap 依赖状态保存为脚本级变量，trap 先注销自身，再恢复服务、精确清理固定临时目录并保留原退出码；已经发生时只启动 `test-ui` 并验证 ready/UI。
- 非解决：恢复数据库、重跑清除、执行 `--remove-orphans`，或关闭 `set -u` 掩盖作用域缺陷。
- 验证：Bash 回归模拟退出码 `7`，恢复函数执行、临时目录删除、原退出码保留且无 `unbound variable`；脚本语法和契约测试通过。
- 预防：所有停服脚本必须覆盖“停服后首个门禁失败”的 EXIT trap 回归，恢复状态不得只保存在即将退出的局部作用域。
- 证据：[归零失败恢复与脚本修复记录](../jx文档清洗本地状态归零失败恢复与脚本修复记录-2026-08-02.md)。
- 最后验证：2026-08-02。

## DEPLOY-006：宿主机诊断目录属主必须匹配镜像运行用户

- 适用：以非 root 用户运行、并把宿主机目录绑定到容器内的 API/Worker 服务。
- 触发：新版本容器反复重启，日志显示在挂载目录执行 `chmod` 或写文件时 `PermissionError`，而目录在宿主机上存在且模式看似严格。
- 根因：发布脚本用 `root:root 0700` 创建目录，但镜像进程使用 UID/GID `10001:10001`；严格权限反而让应用无法访问。
- 解决：发布前从待发布核心镜像执行 `id -u`、`id -g`，校验结果为纯数字，再用该 UID/GID 和 `0700` 创建专用目录；不要把运行用户写死为当前镜像的偶然值。
- 非解决：把目录改成 `0777`、让应用改用 root 运行，或在容器临时层手工修补。
- 验证：API/Worker 挂载源一致且可读写，宿主机目录 UID/GID 与镜像运行用户一致；服务 ready，近期日志没有 `PermissionError`；实际生成文件为 `0600`。
- 预防：所有新增 bind mount 在切换服务前同时验证目标路径、读写模式、镜像运行 UID/GID 和最小目录权限。
- 证据：jx 发布 `20260802-conversion-md-be372c68d875` 及其发布报告；`scripts/jx_deploy_release.sh` 已改为从镜像派生 UID/GID。
- 最后验证：2026-08-02。

## DEPLOY-007：大包传输必须先通过主机资源门禁

- 适用：jx 测试服务器上的离线运行时传输、Docker 构建和发布前置操作。
- 触发：无速率限制地递归传输 OCR 离线运行时，或让 Docker 构建上下文包含归档、分片和无关 `var/` 内容。
- 根因：本次已验证为主机内存压力引发 Swap 换入换出，进一步造成磁盘 I/O 饥饿；SSH、Docker、Dify 和 ai-management 健康检查因此超时。新 OCR Worker 当时未运行；没有磁盘已满、文件系统硬件错误或 OOM-killer 证据。
- 解决：历史阶段先暂停 OCR；当前已按同一门禁恢复 RapidOCR。传输/构建前运行 `bash /opt/ai-management/scripts/jx_resource_guard.sh check`，使用单任务锁、断点续传和限速；构建使用对应的 `Dockerfile.*.dockerignore` 排除重复归档/分片；失败只暂停本次任务，不停止核心服务。
- 非解决：直接重启作为常规方案、无保护的 `scp -r`、并行构建、`docker system prune`、删除 Docker 持久卷或清空 Redis。
- 验证：上一启动周期的 journal/top/vmstat/iostat 记录了 Swap 压力、97% I/O wait 和健康检查超时；恢复后资源门禁通过：MemAvailable 约 26.52 GiB、Swap 0 MiB、I/O wait 0%、D 状态进程 0。
- 预防：可用内存、Swap、I/O wait、负载、D 状态进程、磁盘和 inode 任一越过门槛即 fail-closed；OCR 部署必须显式设置 `JX_OCR_DEPLOYMENT_ENABLED=true`，运行时仍由按需队列和 600 秒空闲策略控制。
- 证据：[OCR 部署故障脱敏记录](../../var/reports/ocr-deployment-20260803/issues.jsonl)、[资源防护脚本](../../scripts/jx_resource_guard.sh)。
- 最后验证：2026-08-05。

## DEPLOY-014：完整 wheel 发布后必须精确恢复并核验 OCR Worker

- 适用：同时修改 `ai_management`、`knowledge_cleaner`、PDF 转换和模型清洗链路的 jx 完整发布。
- 触发：发布脚本完成 API/Worker/UI 切换后，OCR Worker 仍是旧镜像或保持退出；旧发布还可能因资源门禁的瞬时 I/O 状态而在切换前失败。
- 根因：OCR Worker 由按需 autoscaler 管理，完整发布期间会被暂停；发布脚本成功路径和独立 Compose 服务状态不能只靠 API ready 推断。
- 解决：发布前执行资源门禁、数据库/Dify/上传备份和 wheel SHA 校验；成功切换后按精确服务名 `ai-ocr-worker` 强制重建，逐个核验 API、Worker、Scheduler、UI、OCR 镜像标签和运行状态。
- 非解决：使用旧 wheel、只重启 API、执行 `--remove-orphans` 清理、修改 `BAILIAN_BATCH_ENABLED`，或只看进程存在。
- 验证：最终发布报告和备份保留；资源门禁通过，核心/UI/OCR 同源最终 release，API readiness 为 `ready`，模块导入通过，`BAILIAN_BATCH_ENABLED=false` 保持不变。
- 预防：完整发布修改清洗策略时必须换 wheel；OCR 的恢复与核心健康检查分别记录，失败时依据备份回滚，不删除持久卷。
- 证据：`scripts/jx_deploy_release.sh`、`scripts/jx_prebaseline_backup.sh`、jx `/opt/ai-management/var/reports/v2/` 对应最终发布目录。
- 最后验证：2026-08-05。

## DEPLOY-009：按需 OCR 扩容器被可执行权限门禁阻断

- 适用：jx 发布后的 OCR Worker 按需扩容和资源守卫脚本安装。
- 触发：发布后 `ai_ocr` 队列有任务，但 `jx-ocr-autoscaler.service` 只记录资源守卫阻断。
- 根因：远端上传的 `jx_resource_guard.sh` 为 `0644`，脚本的安全前置检查要求 `test -x`，因此任务被正确留在队列。
- 解决：正式发布安装器对发布 helper 使用 `0755`，并核验远端脚本权限；不得用 `chmod` 以外的内容修改绕过门禁。
- 非解决：手工跳过资源守卫、放宽资源阈值或直接在容器内修改脚本。
- 验证：权限修正后 OCR Worker 自动启动、队列清空；最终 release 仍安装为 `0755`。
- 预防：发布后检查 helper 可执行权限、资源守卫结果和 OCR 队列状态；权限异常时停止发布并回滚到备份。
- 证据：`scripts/jx_install_ocr_autoscaler.sh`、`scripts/jx_deploy_release.sh` 及对应 jx 发布报告。
- 最后验证：2026-08-05。

## DEPLOY-010：cgroup v2 不接受 Compose 默认块设备限流

- 适用：jx 主机使用 cgroup v2 时的 Compose/OCR Worker 部署。
- 触发：重建 OCR Worker 报 `io.max` 设备不存在。
- 根因：Compose 中写死了另一台主机的块设备 major/minor 映射。
- 解决：移除默认 `blkio_config`，保留 CPU/内存/PID/临时目录限制；块设备限流只能由运维按当前主机显式覆盖。
- 非解决：猜测设备号、在 Compose 中保留失效映射或删除持久卷规避错误。
- 验证：jx OCR Worker 重新创建并完成扫描 PDF 任务，未影响核心服务。
- 预防：发布前检查当前主机 cgroup 版本和块设备映射；默认配置不写死主机专属 major/minor。
- 证据：jx OCR Worker 重建记录及 `docker-compose.ai.yml` 资源限制配置。
- 最后验证：2026-08-03。

## DEPLOY-011：测试页冷启动的瞬时 502 不应立即触发回滚

- 适用：`scripts/jx_quick_deploy.sh` 重建 `test-ui` 后的页面门禁。
- 触发：新 API/UI 容器已 ready，但发布脚本对 `/ai-management-test/` 只请求一次，Nginx 在 UI 尚未完成启动时返回 HTTP 502，发布因此回滚。
- 根因：Streamlit/UI 容器重建与 Nginx 代理就绪存在短暂时间差；单次 HTTP 检查把可恢复的冷启动窗口误判为版本失败。
- 解决：测试页门禁在 60 秒内每 2 秒重试，最终仍非 200 才触发既有回滚；不要手工删锁、删卷或重复并发部署。
- 验证：jx 发布 `quick-20260805-014840-8408d2e634e4` 首次暴露 502；安装重试脚本后发布 `quick-20260805-020249-034af4811733` 完成，测试页 HTTP 200。
- 预防：发布报告保留最终 `test-page-http-status.txt`，并把 UI 冷启动重试作为部署契约测试的一部分。
- 证据：`scripts/jx_quick_deploy.sh`、`tests/test_quick_deploy_contract.py`、`/opt/ai-management/var/reports/v2/quick-20260805-020249-034af4811733-quick-deploy`。
- 非解决：因一次冷启动 502 直接回滚、重复并发部署或删除锁/卷绕过门禁。
- 最后验证：2026-08-05。

## DEPLOY-012：OCR Worker 恢复不能依赖 systemd 或单一 Compose 文件

- 适用：快速部署暂停 OCR Worker、随后成功切换或失败回滚的路径。
- 触发：服务器没有 `jx-ocr-autoscaler.timer`/systemd，或 OCR 服务只在合并 Compose 配置中可见；发布后 OCR Worker 保持退出，未恢复原运行状态。
- 根因：旧脚本在没有 systemd 时提前返回，且暂停/恢复检查曾只使用 `docker-compose.ai.yml`，没有记录实际运行的 `ai-ocr-worker`；后续又确认成功发布路径在 `trap - EXIT` 前直接退出，未执行仅挂在 EXIT trap 上的恢复函数。
- 解决：systemd 只负责定时器时仍可选；OCR Worker 状态始终用完整 Compose 配置读取、停止和恢复。成功路径在关闭 EXIT trap 前显式调用恢复函数并清零恢复标记；当前脚本仅对发布进程启用 `JX_OCR_DEPLOYMENT_ENABLED=true`，不把 Token 或开关写入报告。
- 验证：最终远端脚本已按精确备份更新；release `quick-20260805-142049-ddc348e62453` 发布前 OCR running，发布后仍为 running，API ready、测试页 200。契约测试覆盖成功路径恢复顺序，Bash 语法检查通过。
- 预防：失败恢复测试必须覆盖“无 systemd、Worker 发布前 running”和“测试页冷启动”组合；禁止用 `--remove-orphans` 代替状态恢复。
- 证据：`scripts/jx_quick_deploy.sh`、`tests/test_quick_deploy_contract.py`、`/opt/ai-management/backups/quick-20260805-020249-034af4811733-predeploy`。
- 非解决：只重启 systemd、只读单个 Compose 文件，或用 `--remove-orphans` 清理恢复问题。
- 最后验证：2026-08-05。

## DEPLOY-013：修改 Chatflow `.env` 后必须强制重建读取配置的服务

- 适用：jx Compose 部署，轮换或修正 `DIFY_BUSINESS_APP_API_KEY` 等 Dify Chatflow App Key。
- 触发：服务器 `.env` 已修改，但 Chatflow 仍使用旧 Key；执行 `docker compose restart` 后行为没有变化。
- 根因：Compose 的 `env_file` 值在容器创建时写入容器配置；普通 restart 不会重新读取 `.env`。
- 解决：先备份 `.env`，再执行 `docker compose -f docker-compose.ai.yml up -d --no-deps --force-recreate api worker scheduler`，最后检查 `/health/ready` 与一次真实 Chatflow 调用。
- 非解决：只执行 `docker compose restart`、在运行容器内临时修改环境、打印完整 `.env` 或执行 `docker compose down -v`。
- 验证：历史 jx Key 轮换记录中，备份 `.env` 后重建 `api`、`worker`、`scheduler`，容器配置哈希、Dify `/parameters`、AI 服务 `ready` 和服务状态均通过；Key 明文未写入报告。[当前状态记录](../AI管理服务当前状态与后续测试交接.md:1015)
- 预防：把配置变更与代码发布分开记录；Chatflow App Key 只需重建核心三服务，基地址或知识库配置变化则按实际消费者扩大范围；始终只检查键名、哈希或健康结果，不输出密钥值。
- 证据：`scripts/jx_quick_deploy.sh`、`tests/test_quick_deploy_contract.py`、[当前状态记录](../AI管理服务当前状态与后续测试交接.md:1015)。
- 最后验证：2026-08-05。

## DEPLOY-015：完整发布在切换前失败也必须恢复发布前运行的 OCR Worker

- 适用：完整 wheel 发布在资源门禁、依赖安装或镜像构建阶段失败的路径。
- 触发：发布脚本已经暂停 OCR Worker，但尚未设置 `SWITCHED=1` 就失败；核心服务已回滚健康，OCR Worker 却保持 stopped/exited。
- 根因：旧回滚逻辑只在 `SWITCHED=1` 分支内恢复 OCR Worker，且恢复函数被 `systemctl` 是否存在提前返回短路；因此切换前失败没有执行完整恢复。
- 解决：把 OCR Worker 恢复放入独立的失败恢复函数；systemd timer 恢复与 Compose 服务恢复分别判断，不互相短路。恢复使用精确服务名 `ai-ocr-worker`，不使用 `--remove-orphans`、不删除卷。
- 非解决：手动删除锁、整套 `docker compose down`、清理持久卷、跳过资源门禁，或只检查 API ready。
- 验证：本轮 jx 发布首次因 stage 文件名契约错误在切换前中止；API/Core 服务回滚健康，手动恢复 OCR 后确认问题。随后本地发布契约测试通过，修复后的完整 release `20260805-cleaning-unify-0d1125af5d56` 成功发布，OCR timer active，服务状态正常。
- 预防：发布前后分别记录 OCR autoscaler、OCR Worker、Core/UI 镜像和 API ready；测试必须覆盖切换前失败和切换后失败两条 trap 路径。
- 证据：`scripts/jx_deploy_release.sh`、`tests/test_cleaning_release_contract.py`、jx 发布报告 `/opt/ai-management/var/reports/v2/20260805-cleaning-unify-0d1125af5d56-deploy`。
- 最后验证：2026-08-05。

## DEPLOY-016：快速发布不能替代包含共享清洗模块的完整 wheel 发布

- 适用：同时修改 `ai_management` 和 `knowledge_cleaner` 的清洗策略、PDF 或 Markdown 共享核心后部署到 jx。
- 触发：快速发布只同步 `ai_management`，服务器构建阶段导入 `knowledge_cleaner.markdown_table_cleaner` 失败；若只看 API 代码包，容易误判为快速发布可覆盖本轮变更。
- 根因：快速发布路径的上传范围不包含共享清洗包；服务器旧构建资产没有当前仓库新增模块，构建导入门禁正确阻断。
- 解决：保留快速发布失败现场并自动回滚；执行发布前备份后构建完整 wheel，核对 wheel SHA-256 和包内共享模块，再走完整 release 发布。发布后在 API 容器内做模块导入、路由发现、ready、Dify 可达和测试页检查。
- 非解决：在运行容器内临时 `pip install`、只上传 `ai_management`、跳过全模块导入门禁，或用快速发布覆盖跨包变更。
- 验证：完整 release `20260806-065957-full-bd1c4762d48d` 首次验证了 `knowledge_cleaner.markdown_table_cleaner` 导入和失败条目路由；后续 release `20260806-111200-full-8f59f14169c9` 再次通过共享模块导入、API/Worker/UI 同源、ready 和测试页 HTTP 200。release `20260806-190738-business-next-step` 再次通过 wheel SHA-256、共享模块导入、迁移 head、Dify ready、同源服务与测试页检查，且未发布 Dify App。
- 预防：发布前按变更域判断快速/完整路径；凡涉及 `knowledge_cleaner`、共享脚本或构建元数据即使用完整 wheel，保留备份和失败报告。
- 证据：jx 发布报告 `/opt/ai-management/var/reports/v2/20260806-111200-full-8f59f14169c9-deploy`、备份 `/opt/ai-management/backups/20260806-review-switch-fix-predeploy`、本地脱敏记录 `var/reports/20260806-review-switch/issues.jsonl`。
- 最后验证：2026-08-06。

## DEPLOY-017：完整发布必须在任何副作用前保存 `.env`

- 适用：jx `scripts/jx_deploy_release.sh` 的完整 wheel 发布与切换后自动回滚。
- 触发：发布成功或切换后失败时，备份目录缺少 `env.backup`，而回滚路径会恢复该文件。
- 根因：旧脚本备份了 wheel、Compose 和迁移文件，却没有在暂停 OCR、安装 wheel 或改写 `.env` 前复制 `.env`。
- 解决：在注册回滚 trap 后、任何发布副作用前执行 `cp -a .env "$BACKUP_DIR/env.backup"`；以发布契约测试锁定该前置条件。
- 非解决：切换后才复制当前 `.env`、在运行容器内临时改环境，或假定历史备份可代表本次配置。
- 验证：`tests/test_cleaning_release_contract.py` 新增契约断言和 `bash -n scripts/jx_deploy_release.sh` 均通过；修订脚本已校验 SHA-256 后同步至 jx。当前服务不因该脚本同步而重启。
- 预防：完整发布报告或发布前门禁必须确认本次 `env.backup` 已存在；发布脚本的每个 rollback 输入都应由同一脚本显式创建。
- 证据：本地脱敏记录 `var/reports/20260806-business-next-step-release/issues.jsonl`，jx 发布 `20260806-190738-business-next-step` 的备份目录。
- 最后验证：2026-08-06。

## DEPLOY-018：复用 release ID 必须清理旧失败标记，资源门禁瞬时失败可安全重试

- 适用：完整 wheel 发布脚本重复使用同一 release ID，或 OCR 构建阶段遇到瞬时主机 I/O 门禁失败。
- 触发：第一次 `20260807-1345-route-activefix` 尝试在 OCR 构建前被资源守卫判定存在不可中断 I/O 等待；脚本未切换服务，但旧报告目录的 `rollback-status.txt` 会让后续成功尝试看起来像失败。
- 根因：失败标记按 release 目录保留，重试前没有删除上一轮标记；资源守卫正确 fail-closed，但报告状态没有区分旧尝试和当前尝试。
- 解决：脚本在创建报告目录后删除本 release 的旧 `rollback-status.txt`；资源守卫恢复为通过后重试同一备份和 wheel。成功报告不再生成失败标记，服务未做并发部署。
- 非解决：跳过资源守卫、删除锁文件、并发启动第二个发布、使用 `--remove-orphans` 或清理 Docker 持久卷。
- 验证：首次尝试未切换旧服务且 OCR 自动恢复；第二次发布成功，报告 `/opt/ai-management/var/reports/v2/20260807-1345-route-activefix-deploy` 无失败标记，Core/UI/OCR 构建、ready、迁移和同源容器检查通过。
- 预防：每个 release ID 尽量只使用一次；必须重试时保留同一备份、重新运行资源门禁并检查报告目录时间与内容，不能只看退出码。
- 证据：`scripts/jx_deploy_release.sh`、`tests/test_cleaning_release_contract.py`、jx 备份 `/opt/ai-management/backups/20260807-1345-route-activefix-predeploy`。
- 最后验证：2026-08-07。
