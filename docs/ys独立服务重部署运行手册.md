# ys 独立 AI 管理服务重部署运行手册

> 适用：开发环境代码修改后，在已存在的 ys 专用部署上发布新候选。首次部署的现场证据见 [2026-09-28 验收记录](../var/reports/W-20260924-151708/ys-acceptance-20260928.md)。该记录在本地忽略目录，不随 Git 分发。本页整理已验证的构建、隔离与检查动作；**尚未实际演练第二个候选的完整切换和回滚**，切换前须按当次候选重新评审并取得发布授权。

## 1. 每次重新核对

1. 从当前源码生成 wheel 和候选包；分别记录源码版本、wheel SHA-256、包 SHA-256、候选 ID。检查包内 `Dockerfile.ys`、`docker-compose.ys.yml`、迁移、OCR 启动脚本和离线模型 manifest 与这次源码匹配。不要复用上次候选 ID、哈希或包名。先跑仓库当前 Quality、review 与 release gate；保护命令按治理工具记录本次用户批准。
2. 在 ys 只读检查 `current` 指向、七个专用容器及共享 `docker-api-1`、`docker-worker-1`、`docker-db_postgres-1`、`docker-redis-1` 的运行状态；记录专用库 `ai_ys` 迁移 head、Redis DB 6 键数和专用卷。现有数据库、Redis DB 6、卷与容器**不可按首次部署脚本的“必须为空”条件重建或清空**。
3. `admin` 对 `/opt` 无写权限，候选目录用 `/home/admin/ai-management-ys/releases/<candidate>`。确认该账号、Docker 权限、磁盘空间、目标路径和回滚所需旧候选仍在。只处理 `ai-management-ys` 项目资源，不对共享 Dify/PostgreSQL/Redis 执行重建、清库、卷删除或 `docker system prune`。
4. 候选包包含凭据时，仅在权限 700 的 `/home/admin/.ssh` 暂存；远端解包执行 `umask 077`，运行配置 `.env.ys` 权限 600。传输前后比较包 SHA-256，检查 wheel 标签与 wheel SHA-256。不得在日志、聊天、报告、命令回显中展示 Key、密码或完整 `.env`。

## 2. 构建与传输模板

本地构建前预检 ys 对 PyPI、Debian 下载源的可达性和速度。本次默认源构建过慢，实际使用阿里云 PyPI 镜像；OCR 镜像补构建还使用阿里云 Debian 镜像。按当次网络状态选择可信源，并固定于候选 Dockerfile；不要在运行容器中临时装包。已验证的 OCR 镜像需要 `libxcb1`、`libgl1`、`libglib2.0-0`，以及 LF 行尾的 `ocr_worker_entrypoint.sh`；构建中执行 `import cv2, rapidocr`。Windows 生成的密码文件也要检查 CRLF，避免末尾 `\r` 破坏长度校验。

以下模板中的尖括号均由当次发布记录填写；`deploy.sh` 是**首次部署脚本**，它要求 `current`、`ai_ys` 库/角色和 Redis DB 6 不存在，不能在现有 ys 安装上原样重跑。

```powershell
Get-FileHash -Algorithm SHA256 '<local-candidate.tar.gz>'
scp '<local-candidate.tar.gz>' 'ys:/home/admin/.ssh/<candidate>.tar.gz'
ssh ys 'sha256sum /home/admin/.ssh/<candidate>.tar.gz'
```

核对哈希相同后，先在私有候选目录解包并检查内容。**已有部署的切换命令必须由当次 release 方案给出**：沿用专用 `ai_ys` 数据库及卷、先验证迁移兼容和回滚，再从同一 wheel 构建不可变镜像，迁移并切换全部七个默认服务；不能照搬首次部署的 `deploy.sh`。本次实际验证了同一镜像重建七服务并核对 image ID，但未验证跨候选的旧版本回滚。

## 3. 配置与隔离检查

- Compose 外部网络是共享 `docker_default`；服务通过 `docker-db_postgres-1`、`docker-redis-1` 等实际网络别名访问。检查别名解析，别把宿主机 `localhost` 当作容器内数据库地址。
- PostgreSQL 只用专用 `ai_ys` 角色/库；Redis URL 指向 DB 6。API/UI 只绑定 ys 回环 `127.0.0.1:18080` 和 `127.0.0.1:18501`。`docker-compose.ys.yml` 默认七服务，Batch Worker 位于 `batch` profile 且保持关闭。
- 四个 App Key 分别只读请求 Dify `/v1/parameters`，`general` Knowledge Key 只读请求其文档 API，确认 HTTP 200；401 不能以 `/health/ready` 代替验证。课程 Dataset 开关 `false` 且 ID 为空；不要启用课程库或改变现有文档状态。
- OCR 离线模型目录只读挂载，`manifest.json` 与三份模型文件须存在、校验且可由容器 `appuser` 读取；非密钥模型目录/文件本次用 755/644。OCR Worker 日志需出现模型校验成功和 Celery ready。`BAILIAN_API_KEY=666` 仍为占位值，禁止真实模型调用；预算和 Batch 边界按当次配置复核。

## 4. 切换后验收与收尾

以下检查命令已在 ys 使用；UI 的正确路径是 `/ai-management-test/`，根路径 404 不代表 UI 失败。

```bash
docker ps --filter name=ai-management-ys --format '{{.Names}} {{.Status}} {{.Image}}'
curl -fsS http://127.0.0.1:18080/health/ready
curl -s -o /dev/null -w 'UI_HTTP=%{http_code}\n' http://127.0.0.1:18501/ai-management-test/
```

逐一确认七容器 Up、共用候选镜像 ID、迁移 head、OCR Worker ready、共享四容器与部署前状态一致。失败时只停止/恢复本轮 `ai-management-ys` 专用服务，保留数据库和卷供排查；不要影响共享 Dify。完成后精确删除私有暂存包、候选中的凭据准备副本和本机临时转存副本；先确认活动 `.env.ys` 仍存在且为 600，再复核 ready/UI。若执行策略拦截清理，记录具体文件和未清理状态，不宣称清理完成。

## 5. 本次验收边界

2026-09-28 首次部署已通过 ready、UI、四 App 与 `general` 只读权限、课程隔离、离线 OCR 与同源镜像检查。`general` 唯一现有文档处于禁用状态，正向 RAG 命中未验收；占位模型 Key 下，真实模型清洗、费用、上传到 Dify 写入全链路均未运行。最终 Quality 通过，但治理 `W-20260928-173015` 的 `gate-status ready=false`（实施合同指纹及 review 豁免过期），不能将它写成完整 release gate 通过。上述状态是本次快照，下次均须重新读取。
