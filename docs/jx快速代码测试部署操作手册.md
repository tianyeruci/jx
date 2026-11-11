# jx 快速代码测试部署操作手册

> 用途：修改 `ai_management` 后，压缩、上传、在服务器运行脚本，然后直接打开测试页看效果。  
> 默认方式：在确认项目归属后直接替换 `ys` 上本项目；已确认属于本项目且允许丢弃的旧 test 数据可按边界清理，除非本次明确选择保留。
> 适用环境：本地 Windows PowerShell；默认目标为本项目 `ys` test 服务器（Linux x86_64、Bash、Docker/Compose，实际账号和权限以现场只读回读为准）。明确 production 时不适用本手册的 test 清理语义，改按正式发布规则执行。

> **环境边界（2026-11-04）**：默认 test 直接替换服务器上的本项目；可删除的数据必须先证明属于本项目且允许丢弃。不要求普通 test 部署备份、恢复演练、临时数据库/schema、隔离栈、并行版本或旧数据兼容。脚本为破坏性清理、production 或具体回滚不确定性保留的备份材料，不得被解释为普通 test 的通用门禁；已有 scope 授权覆盖范围内的替换、重启、验证和必要修复，不逐条重新审批。

> **JX 历史示例警告**：本手册下文的 `jx` SSH 别名、`/opt/ai-management`、`root`、固定脚本路径、Compose 服务名、数据库/Redis DB、`8000/8501` 端口、备份目录和命令均是旧 JX 环境的历史示例，不是 YS 的实时事实。不得把这些值机械替换主机名后执行；YS 必须先现场只读回读实际 SSH 入口、项目根、运行账号、脚本位置、Compose 文件、服务、端口、数据库/Redis 目标和数据归属，再据回读结果生成命令。已知 `YS` 项目根 `/home/admin/ai-management-ys` 仅作历史定位线索，不确认其当前存在、归属或可写。

## 1. JX 历史示例位置（YS 不得直接复用）

| 内容 | 固定位置 | 是否每次上传 |
|---|---|---|
| 本地要压缩的代码目录 | `D:\work\projectc\jx-now\ai_management` | 每次修改代码后重新压缩 |
| JX 历史部署脚本示例 | `/opt/ai-management/scripts/jx_quick_deploy.sh` | 不代表 YS 当前存在或已适配 |
| JX 历史代码压缩包示例 | `/opt/ai-management/incoming/ai_management.zip` | YS 必须现场确认实际接收路径 |

最重要的结论：**不要把本地整个 `scripts` 目录上传或覆盖到服务器。** 日常只上传 `ai_management.zip`。上表脚本和目录仅为 JX 历史示例；旧脚本实现不构成当前 test 的必需条件，必须先核对其是否属于目标项目、是否符合 2026-11-04 policy，再决定是否适配后采用。不得假定脚本已在 YS 长期安装，也不得原样执行旧命令。

如果本次只修改服务器 `.env` 中的 Dify Chatflow 访问 Key，不要制作或上传 `ai_management.zip`，也不要运行本快速代码部署流程；直接按 [V2 部署回滚手册第 0.1 节](V2从开发到服务器部署与回滚手册.md#01-仅修改-chatflow-访问配置不改代码) 备份 `.env` 并强制重建核心服务。

## 2. 日常部署：照着做即可

### 第 1 步：在本地压缩正确目录

打开 Windows 资源管理器，进入：

```text
D:\work\projectc\jx-now
```

建议使用下面的 PowerShell/`uv` 命令生成 ZIP。不要直接使用 Windows `Compress-Archive` 或资源管理器压缩：某些服务器 Python 版本会将其条目解析为反斜杠路径，触发服务器 ZIP 安全校验。最终文件是：

```text
D:\work\projectc\jx-now\ai_management.zip
```

```powershell
Set-Location D:\work\projectc\jx-now
@'
from pathlib import Path
import zipfile

source = Path('ai_management')
destination = Path('ai_management.zip')
with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(source.rglob('*'), key=lambda item: item.as_posix()):
        if path.is_dir() or '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        archive.write(path, path.as_posix())
print(destination)
'@ | uv run python -
```

ZIP 内正确结构：

```text
ai_management/
├─ __init__.py
├─ main.py
├─ models.py
├─ test_ui.py
├─ api/
├─ services/
└─ workers/
```

错误方式：

- 只选中 `ai_management` 里面的文件，导致 ZIP 顶层直接出现 `main.py`；
- 压缩整个 `jx-now`，导致 ZIP 顶层是 `jx-now/`；
- 把 `.env`、整个 `scripts/`、Dify 配置或真实测试资料一起打包。

本地 PowerShell 可选检查：

```powershell
Set-Location D:\work\projectc\jx-now
$zip = 'D:\work\projectc\jx-now\ai_management.zip'
if (-not (Test-Path -LiteralPath $zip)) { throw 'ai_management.zip 不存在' }
$entries = tar -tf $zip
$invalid = $entries | Where-Object { $_ -notmatch '^ai_management(/|$)' }
if ($invalid) { throw 'ZIP 中存在 ai_management/ 之外的路径' }
@'
from pathlib import Path
import zipfile

with zipfile.ZipFile(Path('ai_management.zip')) as archive:
    invalid = [name for name in archive.namelist() if not name or '\\' in name]
if invalid:
    raise SystemExit('ZIP 中存在反斜杠或空路径，必须重建')
'@ | uv run python -
$entries | Select-Object -First 20
Get-FileHash -LiteralPath $zip -Algorithm SHA256
```

### 第 2 步：从 Windows 上传 ZIP

以下假设 SSH 别名是 `jx`。打开 PowerShell：

```powershell
Set-Location D:\work\projectc\jx-now
ssh jx "mkdir -p /opt/ai-management/incoming"
scp .\ai_management.zip jx:/opt/ai-management/incoming/ai_management.zip
ssh jx "unzip -tq /opt/ai-management/incoming/ai_management.zip"
```

预期：最后一条命令正常退出，不出现 CRC 错误。重复上传会覆盖尚未部署的同名 ZIP，但不会直接覆盖运行中的服务。

### 第 3 步：登录服务器

仍在 Windows PowerShell：

```powershell
ssh jx
```

后面的命令全部在服务器 Bash 中运行。先进入固定目录：

```bash
cd /opt/ai-management
pwd
whoami
```

预期：

```text
/opt/ai-management
root
```

如果不是这个目录或不是 root，停止，不要继续部署。

### 第 4 步：检查脚本和 ZIP

```bash
cd /opt/ai-management
test -x scripts/jx_quick_deploy.sh && echo "部署脚本：正常"
test -f incoming/ai_management.zip && echo "代码压缩包：正常"
bash -n scripts/jx_quick_deploy.sh && echo "脚本语法：正常"
```

三行都应显示“正常”。如果没有输出或命令报错，先检查前面的压缩、上传和目录步骤，不要跳过检查。

### 第 5 步：先运行只读计划

```bash
cd /opt/ai-management
bash scripts/jx_quick_deploy.sh --plan
```

`--plan` 不构建镜像、不切换服务、不清理数据。它检查：

- 当前 API 是否健康；
- 磁盘是否至少有 10 GiB 可用空间；
- ZIP 是否只有一个 `ai_management/` 顶层目录；
- 路径、大小、CRC 和关键文件是否安全；
- `models.py` 是否与当前版本一致。

正常输出示例：

```text
release_id=quick-...
source_sha256=...
current_core_image=jx-now-ai-management:...
current_ui_image=jx-now-ai-management-ui:...
data_mode=not-selected
```

只要出现 `ERROR:` 就停止。不要绕过检查，也不要直接改运行容器。

### 第 6 步：正式部署，默认保留测试数据

日常使用这一条：

```bash
cd /opt/ai-management
bash scripts/jx_quick_deploy.sh --keep-data
```

执行过程中通常需要 2～5 分钟，构建阶段可能暂时没有终端输出。不要重复执行，不要关闭 SSH，也不要手工重启容器。

此模式不会主动清理：

- AI PostgreSQL 业务数据；
- Redis 业务键和缓存；
- AI 上传卷；
- Dify App、会话、Dataset 或知识库文档。

成功时最后会看到：

```text
deployment_status=completed
release_id=quick-...
report_dir=/opt/ai-management/var/reports/v2/...
backup_dir=/opt/ai-management/backups/...
```

保存这四行。`report_dir` 是部署检查证据，`backup_dir` 是本次发布前备份。

成功后 `/opt/ai-management/incoming/ai_management.zip` 会被自动删除。这是正常行为；下次部署重新上传新的 ZIP 即可，脚本不用重新上传。

### 第 7 步：服务器上复核

```bash
cd /opt/ai-management
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())"
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps
curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1/ai-management-test/
```

预期：

- ready JSON 中 `status` 是 `ready`；
- 数据库、Redis、Dify 配置与可达性均为 `true`；
- API、Worker、Scheduler 和 `test-ui` 均为运行状态；
- 测试页 HTTP 状态为 `200`。

### 第 8 步：打开测试页

浏览器访问：

```text
http://112.74.37.111/ai-management-test/
```

先进入“诊断”，点击“就绪检查”；正常后再测试本次修改涉及的页面。上传文件或向 Dify 发问时，只使用合成测试内容，不使用真实用户文件或个人信息。

## 3. 只有需要空环境时才清理数据

日常不要使用本节。只有明确需要重新从空测试环境开始时，在服务器执行：

```bash
cd /opt/ai-management
bash scripts/jx_quick_deploy.sh --clean-data
```

脚本会要求输入：

```text
RESET_JX_TEST_ENVIRONMENT
```

清理范围：AI 业务表、AI Redis 精确键、AI 上传卷，以及当前 `.env` 配置的通用/课程两个 Dify Dataset 内按精确 document ID 枚举出的文档。

不会删除：Dify App、Chatflow/Workflow、应用发布版本、应用会话、Dataset 本身、Dify 容器、Dify 持久卷、Dify 镜像、Dify PostgreSQL 数据库或其他 Redis DB。

旧 JX 脚本实现可能在清理前生成并验证数据库 dump、Redis RDB、上传卷、Dify 存储、配置和镜像备份；这只是旧实现行为，不构成当前 test 的必需门禁。当前 test 仅按项目归属和可丢弃边界清理，production 或具体回滚不确定性才按正式 backup 规则处理。已确认清掉的数据不会在部署失败时自动恢复；不得因此把旧脚本原样搬到 YS，需先完成现场回读和 policy 适配。

## 4. 脚本自动做了什么

一次成功执行会自动：

1. 安全检查和解压 ZIP，忽略 `__pycache__`、`.pyc`、`.pyo`；
2. 在服务器重建 wheel，编译并导入检查全部核心/UI 模块；
3. 构建不可变的核心镜像和 UI 镜像；
4. 创建并实际恢复验证发布前备份；
5. 根据参数保留或清理测试数据；
6. 只更新 `.env` 的 `AI_IMAGE`、`AI_UI_IMAGE`、`BUILD_VERSION`；
7. 同步重建 API、Worker、Scheduler 和测试页；
8. 检查健康、迁移、容器镜像、测试页和严重日志；
9. 自动失败回滚；
10. 成功后保留新版本和上一版本项目镜像标签，清理更旧标签，不执行全局 prune，不删除卷或 Dify 镜像。

## 5. 哪些修改不能走快速流程

下列情况会停止，或必须使用完整发布流程：

- 修改了 `ai_management/models.py`；
- 修改 `pyproject.toml` 或增加新的 Python 依赖；
- 修改 `knowledge_cleaner/`、`dify_client.py`、`migrations/`；
- 修改 Dockerfile、Compose、Nginx；
- OCR 相关修改（RapidOCR 离线依赖/模型、`Dockerfile.ocr-offline`、`ai-ocr-worker` 或 systemd 按需启停）仍不得走快速流程；当前 jx 已部署 `rapidocr_onnxruntime`，任何后续变更必须按完整发布流程、备份和 `/opt/ai-management/scripts/jx_resource_guard.sh` 资源门禁验收；
- 修改 Dify DSL、Prompt、App、Workflow、Dataset 绑定或 API Key；
- 需要数据库迁移或基础设施调整。

快速 ZIP 只包含 `ai_management/`，不会自动发布 Dify DSL/Prompt。快速脚本默认拒绝 OCR 发布（需显式设置 `JX_OCR_DEPLOYMENT_ENABLED=true`，且必须经过重新确认）；不得用快速脚本上传 OCR 离线包、构建 OCR 镜像或改动 `ai-ocr-worker`/systemd timer。

## 6. 常见报错怎么处理

| 报错或现象 | 含义 | 处理方式 |
|---|---|---|
| `upload ai_management.zip` | 服务器没找到 ZIP | 重新执行第 2 步上传 |
| `ZIP must contain only one top-level ai_management directory` | 压缩层级错误 | 重新选中 `ai_management` 文件夹本身压缩 |
| `models.py changed` | 涉及模型/迁移风险 | 停止快速流程，改走完整发布 |
| `ModuleNotFoundError` | 新代码需要当前构建元数据未声明的依赖 | 不在容器内安装；核对依赖并走完整发布或修复已验证的服务器构建元数据 |
| `at least 10 GiB` | 磁盘空间不足 | 停止，先做只读空间分析和精确清理 |
| `another quick deployment is already running` | 已有部署持有独占锁 | 确认现有任务，不删除锁文件、不并发执行 |
| 测试页首次返回 `502` | `test-ui` 刚重建，Nginx 代理早于 Streamlit 就绪 | 当前脚本会在 60 秒内重试；先查看发布报告，只有 60 秒后仍失败才按回滚报告排查 |
| 部署切换后验证失败 | 新服务未通过门禁 | 脚本自动恢复旧 `.env`、wheel、源码和服务；保留报告排查 |

同一个失败最多在根因修正后重试一次；不要连续重复运行有副作用的部署命令。

## 7. 首次安装或更新部署脚本（非日常步骤）

只有服务器脚本不存在，或本地脚本本身更新时才做。本地只上传这一个文件，不上传整个 `scripts` 目录。

Windows PowerShell：

```powershell
Set-Location D:\work\projectc\jx-now
$localScript = 'D:\work\projectc\jx-now\scripts\jx_quick_deploy.sh'
$localHash = (Get-FileHash -LiteralPath $localScript -Algorithm SHA256).Hash.ToLower()
scp $localScript jx:/tmp/jx_quick_deploy.sh
ssh jx "sha256sum /tmp/jx_quick_deploy.sh"
"local_sha256=$localHash"
```

确认两边 SHA256 完全一致后，登录服务器执行：

```bash
set -euo pipefail
cd /opt/ai-management
test "$PWD" = "/opt/ai-management"
mkdir -p scripts incoming
install -m 0700 /tmp/jx_quick_deploy.sh scripts/jx_quick_deploy.sh
rm -f /tmp/jx_quick_deploy.sh
bash -n scripts/jx_quick_deploy.sh
stat -c 'mode=%a path=%n' scripts/jx_quick_deploy.sh
```

预期权限为 `700`。以后回到第 2 节，只上传代码 ZIP。

## 8. 已知限制与安全提醒

- 不执行 `docker compose down -v`、`docker system prune -a`、整库 Redis 清理或直接修改 Dify 数据库；
- 不在运行容器内热改代码或安装依赖；
- 不把 `.env`、Token、密码、真实用户数据或完整私有报告复制到聊天、Git 或公开文档；
- Streamlit 当前版本可能发送匿名使用统计，包含页面 URL、运行版本和匿名机器/会话标识；如禁止外发，应单独设置 `browser.gatherUsageStats=false` 并重建 UI，这不是代码 ZIP 自动修改项。

完整迁移、依赖、Dify 或基础设施发布见 [V2 从开发到服务器部署与回滚手册](V2从开发到服务器部署与回滚手册.md)。
