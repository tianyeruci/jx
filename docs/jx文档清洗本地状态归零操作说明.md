# jx 文档清洗本地状态归零操作说明

> 状态：当前有效  
> 适用环境：jx 测试服务器 `/opt/ai-management`  
> 脚本：`scripts/jx_reset_cleaning_local_state.sh`  
> 最后核对：2026-08-01

## 1. 用途与边界

当测试页反复上传文件、需要重新模拟“首次文档清洗”时，使用本脚本归零 AI 服务的本地清洗状态。

脚本会处理：

- PostgreSQL 中的 `cleaning_batch`，以及数据库外键级联删除的文件、条目、模型任务和发布分片；
- Redis 中经过格式校验的 `ai:cleaning:*` 精确键；
- `ai-management_ai_uploads` 卷中由当前清洗批次 ID 精确证明归属的目录；
- Streamlit 测试页进程，执行后重启以清空页面会话缓存。

脚本不会处理：

- Dify 知识库文档，不调用 Dify 删除接口，也不停用文档；
- 课程、学习路线、会话、消息、画像、系统事件日志；
- 与文档清洗无关的 `external_deletion_task`；
- 整个 Redis DB、Docker 卷或 Dify 数据库。

因此，如果 Dify 知识库中仍有旧文档，本脚本执行后它们仍然存在。需要完全模拟空知识库时，应先由管理员在 Dify 中确认并处理知识库内容；不要把本脚本误认为 Dify 清理工具。

## 2. 使用命令

登录 jx 后进入项目目录：

```bash
cd /opt/ai-management
```

先查看只读计划：

```bash
bash scripts/jx_reset_cleaning_local_state.sh plan
```

计划会显示清洗表记录数、批次状态、清洗 Redis 键数量、上传卷条目数量、是否存在待执行的 Dify 文档删除任务，以及当前能否安全执行。计划不删除数据。

确认范围后执行：

```bash
bash scripts/jx_reset_cleaning_local_state.sh execute RESET_LOCAL_CLEANING_STATE
```

确认词必须完全一致。脚本不提供跳过备份或强制忽略门禁的参数。

## 3. 执行门禁

出现以下任一情况，脚本会停止且不进入删除步骤：

- 批次状态不属于候选终态 `completed`、`partially_completed`、`failed`、`review_required`；
- 任一文件、条目、模型任务或发布分片仍处于活动状态；`partially_completed` 本身不能证明终态，仍有 `shard_pending`、`shard_indexing` 或运行中模型任务时必须拒绝；
- 存在待处理的 `dify_document` 外部删除任务；
- `ai:cleaning:*` 键不满足已知精确格式；
- 上传卷存在不能由当前清洗批次 ID 证明归属的目录；
- 备份、校验或临时数据库恢复验证失败；
- 备份前后批次、Redis 键、Dify 关联 ID 或上传目录发生变化；
- 服务重启后 `/health/ready` 不为 `ready`；
- 清洗表、Redis 键或上传目录仍有残留。

执行阶段会短暂停止测试页、API、清洗 Worker、模型 Worker、发布 Worker 和 Scheduler；退出 trap 使用脚本级状态保存恢复边界，预检失败也会恢复这些服务。

### 3.1 预检失败后测试页出现 502

如果旧版本脚本在停止 `test-ui` 后预检失败，并出现 `services_may_need_restart: unbound variable`，删除步骤尚未开始，不要恢复数据库，也不要再次运行清除命令。只恢复测试页：

```bash
cd /opt/ai-management
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
  start test-ui
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
  ps test-ui
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())"
curl -fsS -o /dev/null -w 'test_ui_direct_http=%{http_code}\n' \
  http://127.0.0.1:8501/ai-management-test/
```

预期 `test-ui` 为 `Up`、ready 返回 `status=ready`、直连测试页返回 HTTP `200`。不要使用 `--remove-orphans`；孤儿容器警告不是本次 502 的原因。

## 4. 备份与证据

每次实际执行会先调用 `scripts/jx_prebaseline_backup.sh`，生成：

- AI 数据库和 Dify 数据库备份；
- 经过临时库恢复验证的 AI 数据库备份；
- Redis RDB；
- 上传卷归档；
- Compose、镜像、迁移和健康状态快照；
- 清理前后安全摘要与 SHA-256 校验文件；
- 仅保存在服务器私有备份目录中的原清洗记录关联 Dify 文档 ID 清单。

成功后终端会打印本次备份目录：

```text
/opt/ai-management/backups/<时间>-cleaning-local-reset
```

需要恢复时不要直接在运行中的正式库执行 `pg_restore`。先停止写入并按[部署、备份、回滚与常见故障](部署备份回滚与故障.md)及[V2 从开发到服务器部署与回滚手册](V2从开发到服务器部署与回滚手册.md)核对恢复范围。

## 5. 发布与首次安装

脚本已加入 `scripts/jx_deploy_release.sh` 的发布资产列表。完成包含该变更的正常 jx 发布后，脚本会位于：

```text
/opt/ai-management/scripts/jx_reset_cleaning_local_state.sh
```

在该版本尚未发布前，不要手工把本地脚本覆盖到服务器正式目录；可以继续使用经过人工核对的一次性流程，或先完成正常发布。

## 6. 验证口径

成功输出至少应满足：

- `cleaning_batch`、`cleaning_file`、`cleaning_document`、`model_cleaning_task`、`dify_publication_shard` 均为 `0`；
- `ai:cleaning:*` Redis 键为 `0`；
- 上传卷顶层无清洗批次目录；
- `/health/ready` 为 `ready`；
- `dify_delete_operations` 为 `0`；
- 清理前后的非目标 `external_deletion_task` 数量一致。

完成后刷新 `/ai-management-test/`，即可从空的本地清洗状态开始下一轮上传。

初始验证范围见 [jx 文档清洗本地状态归零验证记录-2026-08-01](jx文档清洗本地状态归零验证记录-2026-08-01.md)，失败恢复与脚本修复见 [jx 文档清洗本地状态归零失败恢复与脚本修复记录-2026-08-02](jx文档清洗本地状态归零失败恢复与脚本修复记录-2026-08-02.md)。
