# 活动 RAG 清洗器（Ubuntu / Python / Qwen / Dify）

## 1. 目标与边界

把老板提供的 Markdown 活动资料清洗为适合 Dify RAG 的结构：

- 一个源 Markdown 可以包含多个活动；
- 一个有效活动输出一个独立 Markdown / Dify Document；
- 必要事实缺失时记录并跳过当前活动，继续处理后续活动；
- “适合人群”缺失时，Qwen 依据活动内容生成；
- “活动类型”缺失时，Qwen 从受控枚举判断；
- “解决问题”缺失时，Qwen 可依据主要内容归纳；
- 时间、地点、价格、报名、链接、嘉宾、状态、名额等硬事实禁止 AI 编造；
- metadata 单独输出 JSON，并可通过 Dify Knowledge API 真正写入 Document Metadata；
- 支持实时 API、Batch Chat，以及真正的 JSONL Batch File 异步批处理；
- 单个活动失败不会中断整个文件/批次。

## 2. 输入目录

默认扫描：

```text
/opt/ai-management/tmp/活动/*.md
```

如果存在且非空，则优先扫描：

```text
/opt/ai-management/tmp/活动/input/**/*.md
```

原始 Markdown 永不修改。

## 3. 必要事实

硬性要求：

1. 活动名称；
2. 活动时间：至少明确年/月/日；
3. 活动形式；
4. 主要内容；
5. 活动状态；
6. 线下/混合活动必须有地点。

### 活动形式的确定性兼容

老板样本经常不单列“活动形式”，而写成 `地点：线上` / `地点：杭州`。脚本不让 AI 猜，而用规则归一：

- 明确线上 -> `online`；
- 明确线下/物理地点 -> `offline`；
- 明确线上 + 物理地点/线下 -> `hybrid`；
- `待定/待公布/另行通知/TBD` 等不能推出活动形式 -> 视为缺失并跳过。

### 活动状态

源状态必须能归一为：

```text
active
full
registration_closed
completed
cancelled
postponed_unknown
```

无法确定的自由文本不会交给 AI 猜，而是记录“活动状态无法标准化”并跳过。

`upcoming / ongoing / ended` 不长期写死；它们应由 Chatflow 根据当前时间与 `event_start_ts/event_end_ts` 动态计算。

## 4. AI 配置：只借鉴适合本场景的工程化约束

默认模型：

```text
qwen3.7-plus
```

默认：

```text
enable_thinking = false
```

### 生成侧

使用固定、复用的严格 JSON Schema：

```text
response_format.type = json_schema
strict = true
additionalProperties = false
```

Schema 中所有字段固定存在；`event_type/topic/industry/business_stage` 使用受控枚举；不再只依赖“请输出 JSON”的提示词。

### 程序侧

模型返回后再经过：

1. Pydantic 类型/枚举/长度校验；
2. 业务语义校验；
3. 如果业务校验失败，最多执行 `QWEN_SEMANTIC_REPAIR_RETRIES` 次定向修复；
4. 永久 HTTP 错误不盲目重试；429、5xx、网络超时等临时错误才退避重试。

业务语义校验会阻止 AI 在语义增强字段中新增原文没有的日期、时刻、价格、URL、手机号、常见城市等硬事实。

### 最小证据原则

语义增强请求只把必要内容交给模型：

- 活动名称；
- 主要内容；
- 原始解决问题（有则提供）；
- 原始适合人群（有则提供）；
- 原始活动类型（有则提供）。

日期、地点、价格、链接、嘉宾、状态等不需要模型处理的硬事实不会塞进语义增强 prompt，降低误改与上下文污染。

## 5. Qwen 三种模式

### realtime

少量文件、联调、日常增量：

```bash
python3 clean_events.py \
  --base-dir /opt/ai-management/tmp/活动 \
  --qwen-mode realtime
```

### batch_chat

Batch Chat 是“单请求同步排队端点”，调用方式仍是逐活动请求。它不是 JSONL 文件批处理：

```bash
python3 clean_events.py \
  --base-dir /opt/ai-management/tmp/活动 \
  --qwen-mode batch_chat
```

### batch_file（大量历史活动推荐）

真正批处理：先完成确定性拆分/必要事实校验，只把合格活动打成 JSONL，一次上传，创建异步 Batch，轮询完成后下载结果，再逐活动校验和输出：

```bash
python3 clean_events.py \
  --base-dir /opt/ai-management/tmp/活动 \
  --qwen-mode batch_file
```

Batch 子任务失败默认回退实时 API，可用：

```text
QWEN_BATCH_FALLBACK_REALTIME=false
```

关闭。

Batch 作业输入、最终状态、输出与失败详情保存在：

```text
logs/batch_jobs/<timestamp>/
```

## 6. AI 可以/不可以生成

### 可以

- 适合人群：仅缺失时根据内容生成；
- 活动类型：缺失时分类；
- 解决问题：缺失时归纳；
- `topic`；
- 有明确证据时的 `industry/business_stage`；
- 检索摘要；
- 用户可能检索的短语/问法。

### 不可以

- 日期/时间；
- 地点/城市；
- 报名截止时间；
- 价格；
- 报名方式/链接；
- 嘉宾身份；
- 状态/取消/改期；
- 名额。

老板已经提供的“适合人群 / 活动类型 / 解决问题”在最终输出阶段覆盖 AI 结果，源事实优先。

## 7. Dify metadata

真正供 Chatflow 使用的字段：

```text
record_type              string
event_id                 string
event_type               string
topic                    string
industry                 string
business_stage           string
delivery_mode            string
city                     string
event_start_ts           number
event_end_ts             number
registration_deadline_ts number   # 仅明确提供且可解析时存在
source_status            string
source_title             string
```

多值 `topic/industry/business_stage` 使用 `|` 拼接，便于 Chatflow `contains`。

“适合人群”不做 metadata filter，而保留在正文 child 中参与 Hybrid/语义检索。

**注意：** cleaned Markdown 不再写 `Metadata（审计副本）`。工程 metadata 放在 `.metadata.json`，真正的 Dify metadata 由 `--upload-dify` 写入，避免工程字段进入 embedding/检索正文。

## 8. 时间规则

- 只有日期：开始为 `00:00:00`，结束为 `23:59:59`；
- 单个明确开始时刻、未给结束时刻：`event_end_ts = event_start_ts`，不猜时长；
- 日期区间：按实际范围解析；
- 报名截止只给日期：按该日 `23:59:59`；
- 时间不含明确年/月/日：必要字段不可用，跳过；
- 结束时间早于开始时间：跳过。

## 9. 安装

```bash
cd /opt/ai-management
unzip event_rag_cleaner.zip
cd event_rag_cleaner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
vim .env
```

至少填写：

```text
BAILIAN_API_KEY=...
```

## 10. 只清洗 / 上传 Dify

只清洗：

```bash
python3 clean_events.py --base-dir /opt/ai-management/tmp/活动
```

清洗后直接写 Dify，需要 `.env`：

```text
DIFY_API_BASE=http://你的Dify地址/v1
DIFY_API_KEY=dataset-xxx
DIFY_DATASET_ID=xxx
```

执行：

```bash
python3 clean_events.py \
  --base-dir /opt/ai-management/tmp/活动 \
  --upload-dify
```

脚本会检查/创建 metadata 字段、创建或更新父子块文档、写入 Document Metadata，并把 `event_id -> document_id` 保存到 `output/dify_state.json`。拿到 Dify `document_id` 后会先落本地状态，再写 metadata；如果 metadata 阶段临时失败，下次重跑会更新同一文档并继续补写，降低重复创建风险。

## 11. 输出

```text
/opt/ai-management/tmp/活动/
├── output/
│   ├── cleaned/            # 一个活动一个 Markdown
│   ├── metadata/           # facts + AI enrichment + Dify metadata 审计 JSON
│   ├── manifest.jsonl
│   └── dify_state.json     # --upload-dify 后产生
└── logs/
    ├── run.log
    ├── skipped_events.jsonl
    ├── errors.jsonl
    ├── run_summary.json
    └── batch_jobs/         # batch_file 模式
```

## 12. 父子块

一个短活动通常作为一个父语义单元，正文插入多个：

```text
<<<DIFY_CHILD>>>
```

用于活动检索摘要、活动事实、主要内容/问题、适合人群、用户可能问法。

上传 Dify 时继续使用：

```text
doc_form = hierarchical_model
parent separator = <<<DIFY_PARENT>>>
child separator = <<<DIFY_CHILD>>>
```

短活动正文无需为了“必须出现 parent 分隔符”而强行拆多个父块。

## 13. Chatflow 推荐过滤

“最近有什么可以参加的活动”：

```text
record_type = event
AND source_status = active
AND event_end_ts >= now_ts
AND event_start_ts <= future_90d_ts
```

“8月20日到25日有什么活动”：

```text
record_type = event
AND event_start_ts <= query_end_ts
AND event_end_ts >= query_start_ts
```

“以前有没有办过类似活动”：

```text
record_type = event
```

不加当前有效期限制。

## 14. event_id

优先使用老板提供的稳定 `活动编号/活动ID`。没有时退化为：

```text
规范化活动名称 + 开始日期 -> SHA-256 截断
```

如果同一活动改期且没有稳定业务活动 ID，会生成新的 `event_id`；脚本不能仅凭文本可靠判断“改期”还是“同名下一期”。

## 15. 测试

静态检查：

```bash
python3 -m py_compile clean_events.py
```

单元/契约测试：

```bash
pytest -q
```

离线端到端结构测试：

```bash
python3 clean_events.py \
  --base-dir /opt/ai-management/tmp/活动 \
  --mock-ai
```

`--mock-ai` 只验证拆分、事实校验、时间、输出、metadata 和失败隔离，不代表真实 Qwen 语义质量。真实 Qwen / Dify 联调需要你自己的 API Key。
