# 验证记录

验证日期：2026-08-16

## 0. 2026-08-16 jx 真实 Qwen 回归

- 当前配置结论：Qwen 客户端只读取 `BAILIAN_API_KEY`；未记录或输出密钥值。仅设置 `DASHSCOPE_API_KEY` 时按缺少 `BAILIAN_API_KEY` 处理，且不会发起网络请求。
- 本地回归：`py_compile` 通过，完整 pytest 为 `45 passed`；远端隔离环境 `py_compile` 通过，`pytest -q` 退出码为 0。
- jx 通过既有 SSH 会话重新覆盖上传 7 个脚本文件，远端 SHA-256 与本地逐项一致；未部署、重启或修改 ai-management 服务。
- 单文件真实 `realtime`：返回码 0，`files/events_seen/events_cleaned/events_skipped/events_failed=1/3/3/0/0`，`qwen_calls=3`，`qwen_retries=0`；输入 SHA-256、size、mtime 前后一致。
- 全目录真实 `realtime`：返回码 0，`files/events_seen/events_cleaned/events_skipped/events_failed=1/3/3/0/0`，`qwen_calls=3`，`qwen_retries=0`；原始 Markdown 守恒为 true。
- 覆盖结果：`cleaned/metadata/manifest=3/3/3`，脱敏错误/跳过均为 0；未执行 `--upload-dify`，Dify 上传/更新均为 0，未重启服务、容器或 Compose。
- 凭据验证：脚本在仅设置 `BAILIAN_API_KEY` 后，直接通过项目 dotenv 自动加载并成功完成真实调用；密钥值未回显。

## 0.1 2026-08-14 本轮本地复核（历史基线）

- `python -m py_compile clean_events.py test_clean_events.py`：通过。
- 使用独立虚拟环境安装 `requirements.txt`：通过；补充 `tzdata` 以支持 Windows 下的 `Asia/Shanghai`。
- `pytest -q --basetemp .pytest-tmp-final2`：`43 passed`；首次复跑使用旧锁定临时目录时仅发生 pytest 环境清理权限错误，换全新临时目录后通过。
- 本轮修复：空字段解析不再跨行吞值；线下/混合活动拒绝纯占位地点；源活动类型优先于名称关键词；AI 语义门禁覆盖无年份日期、免费/价格和链接；重复运行不累积本次 manifest/错误/跳过日志；记录 Qwen 请求与重试计数；运行日志使用稳定 source 引用而不写原始文件名。
- jx 远端：通过既有 SSH 会话，在独立目录 `/opt/ai-management/event_rag_cleaner` 验证；7 个上传文件 SHA-256 与本地一致，未部署、重启或修改 ai-management 服务。
- 远端隔离环境 `py_compile` 通过，`pytest -q` 退出码为 0；本地同一版本为 `43 passed`；真实活动目录 `--mock-ai --qwen-mode realtime` 为 1 个文件、3 个活动、3 个清洗、0 跳过、0 失败、0 次 Qwen 调用。
- 原始活动 Markdown 的数量、路径集合摘要、内容 SHA-256、大小/修改时间摘要均不变；`source_unchanged=true`。远端未执行真实 Qwen：`DASHSCOPE_API_KEY` 不存在，按门禁停止。
- 隐私复测：3 个 metadata 和 manifest 中 `raw_text/source_file` 键均为 0，`source_ref` 均为哈希格式；原始正文不再写入审计 JSON。

## 1. 本轮根据“JSON 四层工程化防线”实际采用的内容

采用：

- 固定、收紧的 Schema；字段类型/必填/枚举/禁止额外字段；
- Qwen3.7-Plus 严格 `json_schema + strict=true`，不再只使用 JSON Object；
- Pydantic 结构校验 + 程序业务语义校验；
- 结构合法但业务语义失败时，有限次数定向修复；
- 429/5xx/网络故障退避重试，400/401/403 等永久错误不盲目重试；
- Schema version、失败阶段和运行模式进入审计日志。

未强行采用：

- 下单/退款/发消息类“业务动作幂等键”：活动清洗本身不触发这类不可逆业务动作；
- 额外再调用一个模型做校验：能用确定性代码校验的部分不增加模型调用；
- 无限/全量重试：会增加成本并掩盖配置错误。

## 2. 修复的问题

1. 旧版仅使用 `response_format=json_object`；改为严格 JSON Schema。
2. 旧版把完整 `EventFacts`（包括时间、地点、链接、嘉宾、状态等）塞给语义增强模型；改为最小语义证据。
3. 旧版永久 HTTP 错误也可能进入通用重试；改为异常分类。
4. 旧版 Pydantic 校验失败直接整次失败；改为有限次定向修复。
5. 旧版 `batch_chat` 被描述成“批处理”；现区分 Batch Chat 与真正 JSONL Batch File，并实现 `batch_file`。
6. Markdown 标题形式 `# 活动名称：` / `## 活动名称：` 的拆分兼容问题已修复。
7. 日期型报名截止原来取 `00:00:00`；改为该日 `23:59:59`。
8. 无法标准化的活动状态原来可能以 `unknown` 进入 metadata；现在按无效必要事实跳过。
9. 旧版把 metadata 审计块写入待索引 Markdown；现移至 `.metadata.json`，避免 RAG 内容污染。
10. 活动形式推断新增“待公布/另行通知不猜”以及“线上 + 明确物理地点 -> hybrid”测试。

## 3. 静态检查

```text
python3 -m py_compile clean_events.py
结果：通过
```

## 4. 自动测试

```text
pytest -q
45 passed
```

覆盖：

- 一个 Markdown 多活动拆分；
- Markdown `# / ## 活动名称` 标题边界；
- 必填字段与失败隔离；
- 活动形式确定性归一；
- 中文时间区间；
- 日期型报名截止日末；
- 无法标准化状态拒绝；
- 稳定 event_id；
- 严格 JSON Schema payload；
- 最小模型证据；
- AI 新增硬事实拦截；
- Markdown 不再包含 metadata 审计块；
- Batch File JSONL 请求结构；
- Batch File 输出解析；
- Batch File 上传/创建/查询/下载完整控制流程（HTTP mock）；
- HTTP 400 不重试；
- HTTP 429 退避后重试成功；
- Dify 创建文档后 metadata 写入失败时，`document_id` 已先落盘，重跑不会按新活动再次创建；
- 非 `stop` 的模型结束原因被分类为异常而不是当正常 JSON 使用。
- 三活动独立输出、坏活动隔离、空名称/空内容、线下占位地点、源字段优先、时间逆序、重复运行幂等、401 不重试、Dify metadata 数值类型和 Qwen 调用计数。
- 审计 JSON/manifest 不持久化 `raw_text` 或 `source_file`，仅保存哈希化 `source_ref`。
- 仅 `BAILIAN_API_KEY` 作为 Qwen 凭据变量；仅有 `DASHSCOPE_API_KEY` 时缺少 `BAILIAN_API_KEY`，且有不触网回归测试。

## 5. 用户附件2端到端回归（--mock-ai）

实际附件包含 3 个活动，结果：

```json
{
  "files": 1,
  "events_seen": 3,
  "events_cleaned": 3,
  "events_skipped": 0,
  "events_failed": 0
}
```

输出：3 个独立 Markdown + 3 个 metadata JSON；每个 Markdown 有 5 个 `<<<DIFY_CHILD>>>`，且不再包含 `Metadata（审计副本）`。

jx 复测统计：`qwen_calls=0`、`qwen_retries=0`、`dify_uploaded=0`、`dify_updated=0`；`dify_state.json` 未生成。

状态归一结果：

- 菜籽油·7天私域IP训练营 -> `active`；
- Workbuddy无痛日更写作实战营 -> `active`；
- 觉醒新商学GPT专场圆桌会议 -> `completed`。

## 6. “中间坏活动不影响后续”活动回归

构造：有效 A -> 缺时间 B -> 有效 C。

结果：

```json
{
  "events_seen": 3,
  "events_cleaned": 2,
  "events_skipped": 1,
  "events_failed": 0
}
```

B 被写入 `skipped_events.jsonl`，原因为 `活动时间`；C 正常输出。

## 7. 2026-08-14 本轮未做的真实网络联调（历史记录）

本轮远端环境没有阿里云百炼 API Key，Dify 配置三项也不完整，因此没有实际提交：

- qwen3.7-plus 真实推理；
- Batch Chat 真实请求；
- Batch File 真实异步任务；
- Dify 创建/更新文档；
- Dify Document Metadata 写入。

因此当日结论是：**本地与 jx 隔离环境的业务逻辑、请求契约、异常路径和附件端到端结构已通过；当日真实 Qwen 鉴权/额度/网络联调及 Dify 写入未执行。**
