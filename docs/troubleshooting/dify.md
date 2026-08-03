# Dify 联调排障

## DIFY-001：只修改本地 DSL/Prompt 不会更新已发布 Workflow

- 适用：Dify 应用行为变更。
- 触发：本地文件已改，线上 Workflow 仍使用旧分支或旧提示词。
- 根因：仓库中的 DSL/Prompt 文件不会自动替换 Dify 已导入并发布的应用版本。
- 解决：导出线上旧版本，导入/发布目标隔离应用，发布后核对应用参数和三个入口真实调用；不输出 Key。
- 非解决：只修改或提交本地文件后宣称线上应用已更新。
- 验证：已发布应用参数、版本和三个入口真实调用均对应目标文件。
- 预防：DSL/Prompt 变更完成条件必须包含导入、发布和契约回归证据。
- 证据：[Dify 联调问题与修复记录](../V2-Dify联调问题与修复记录-2026-07-28.md:20)、[r3 排坑记录](../接口清洗发布排坑记录-2026-07-30.md:255)。
- 最后验证：2026-07-30。

## DIFY-002：Code 节点 401 先核对 Sandbox 密钥哈希

- 适用：含 Dify Code 节点的 Workflow。
- 触发：Code 节点调用 Sandbox 返回 401。
- 根因：Dify API 与 Sandbox 使用的密钥配置不一致。
- 解决：仅比较 API 与 Sandbox 密钥哈希是否一致；不输出密钥，不操作 Dify 业务数据库或持久卷。
- 非解决：打印明文密钥、直接改业务数据库或删除持久卷。
- 验证：哈希一致后 Code 节点调用通过，日志和报告中无明文密钥。
- 预防：配置核验只保存脱敏哈希与服务身份。
- 证据：[Dify 联调问题与修复记录](../V2-Dify联调问题与修复记录-2026-07-28.md:45)。
- 最后验证：2026-07-28。

## DIFY-003：创建文档不等于索引成功

- 适用：知识库清洗和 RAG。
- 触发：创建接口返回成功和文档 ID，但索引、分块或回读未完成。
- 根因：文档创建与异步索引是不同阶段，后续处理仍可能失败。
- 解决：同时核对创建响应、索引终态、父子块数量、允许的自定义 Metadata 和回读结果；失败项阻止源文件完成。
- 非解决：只断言 HTTP 200 或文档 ID 存在。
- 验证：索引达到成功终态，父子块、Metadata 和回读结果均满足契约。
- 预防：发布和验收脚本统一等待明确索引终态。
- 证据：[r3 验收记录](../接口契约与清洗全链路验收记录-2026-07-30.md:74)。
- 最后验证：2026-07-30。

## DIFY-004：证据总预算判断不能让首条超长 Chunk 清空全部证据

- 适用：知识检索后通过 Code 节点压缩证据，再送入 LLM。
- 触发：第一条检索结果长度超过总预算，压缩器在加入任何结果前直接 `break`，最终把“有召回”错误变成“无证据”。
- 根因：只做总长度判断，没有先限制单条内容和 Metadata；第一条结果本身就超过预算。
- 解决：先按字段裁剪单条内容和 Metadata，再把裁剪后的块放入总预算；保留课程/路线 ID 等核验字段。
- 非解决：直接提高总预算、把原始检索数组全部送给 LLM，或在 Prompt 中让模型自行忽略噪声。
- 验证：使用首条内容和无关 Metadata 均远超预算的合成结果，输出仍为 `has_evidence=true`，总长度不超过预算，保留 `course_id`/`route_id` 且不包含无关 Metadata。
- 预防：证据压缩测试同时覆盖“首条超长”“多条累计超长”“关键 ID 保留”和“无关 Metadata 剔除”。
- 证据：[商业顾问 DSL 优化与隔离验收记录](../商业顾问DSL优化与隔离验收记录-2026-07-31.md:61)、`tests/test_business_advisor_optimized_dsl.py`。
- 最后验证：2026-07-31（本地代码节点回归）；隔离 Dify 运行态验证待完成。

## DIFY-005：共享 Docker 网络中的 `api` 别名会让 Dify 请求误路由

- 适用：jx 与 Dify 共用外部 Docker 网络的测试部署。
- 触发：Dify 控制台请求出现 502、重试或长期 pending；Nginx 日志显示连接到错误项目的 `api:5001`，或 `getent hosts api` 同时返回 jx 与 Dify 服务。
- 根因：Docker Compose 会把两个项目的服务名都暴露为 `api`；Dify Nginx 和内部配置访问 `http://api:5001` 时，DNS 结果混入 jx API（端口不匹配）或其他同网容器。
- 解决：给 Dify API 配置独占的 `dify-api` 网络别名，并把 Nginx、`PLUGIN_DIFY_INNER_API_URL`、`INTERNAL_FILES_URL` 统一切换到该别名；jx 测试页使用 `ai-management-api:8000`。同时给 API healthcheck 的 curl 增加客户端 `--max-time`，防止异常 Worker 时检查进程堆积。
- 非解决：只重启 Nginx、只刷新浏览器、只重启 jx API，或用 `--remove-orphans` 删除历史容器；这些不能消除同名 DNS 冲突且可能破坏回滚证据。
- 验证：`getent hosts dify-api` 只返回 Dify API 副本；两个 API/Worker healthy；Dify `/apps`、`/console/api/system-features`、`/v1/parameters` 连续 smoke 无超时；jx 测试页返回 200；登录态 Chatflow 预览页的 `advanced-chat/workflows/draft/run` 返回 HTTP 200 `text/event-stream` 并完成流式响应，浏览器无 Console 错误。
- 预防：共享网络变更后检查所有服务别名和 `proxy_pass`/内部 URL；禁止跨项目依赖通用服务名；健康检查必须同时设置服务端和客户端超时。
- 证据：`var/reports/20260803-dify-pending/issues.jsonl`、远端备份 `/opt/ai-management/backups/20260803-193603-dify-pending-root-cause-fix`。
- 最后验证：2026-08-03（jx 测试环境基础链路与登录态 Chatflow SSE 已验证）。
