# Dify Chatflow：活动 Metadata 使用规则

## 结论

清洗后的 Markdown **不再嵌入工程 metadata**，避免 metadata 参与向量索引造成检索污染。审计值写在 `output/metadata/*.metadata.json`；Chatflow 真正能过滤的是通过 Knowledge API 写入的 Dify Document Metadata。

运行：

```bash
python3 clean_events.py --base-dir /opt/ai-management/tmp/活动 --upload-dify
```

脚本会创建 metadata 字段并调用 Dify Knowledge API 把值绑定到每个活动文档。

## 推荐字段与 Chatflow 用途

| 字段 | 类型 | Chatflow 主要用途 |
|---|---|---|
| `record_type` | string | 活动意图时 `is event` |
| `event_id` | string | 精确活动定位、去重/更新 |
| `event_type` | string | `is 训练营/沙龙/圆桌会...` |
| `topic` | string | 主题明确时用 `contains` |
| `industry` | string | 行业明确时用 `contains`；空值不要强行过滤 |
| `business_stage` | string | 企业阶段明确时用 `contains` |
| `delivery_mode` | string | `is online/offline/hybrid` |
| `city` | string | 明确城市时 `is 上海` 等 |
| `event_start_ts` | number | 查询时间窗口 |
| `event_end_ts` | number | 查询时间窗口 |
| `registration_deadline_ts` | number | 报名截止判断（有事实才存在） |
| `source_status` | string | active/full/registration_closed/completed/cancelled/postponed_unknown；无法标准化则活动跳过 |
| `source_title` | string | 精确来源/标题过滤与展示 |

## 为什么时间用 number

Chatflow/Workflow 的 metadata manual filter 支持 Number 的 `> / < / >= / <=`，且过滤值可以用变量；Time 类型过滤值只能使用常量。因此动态“当前时间 now_ts”判断优先使用 Unix 秒时间戳。

## 典型条件

### 1. “最近有什么可以参加的活动”

先在 Code 节点算：

```text
now_ts
future_90d_ts = now_ts + 90*86400
```

Knowledge Retrieval 手工 metadata filter：

```text
record_type is event
AND source_status is active
AND event_end_ts >= now_ts
AND event_start_ts <= future_90d_ts
```

### 2. “8月20日到25日有什么活动”

Code/参数解析阶段得到：

```text
query_start_ts
query_end_ts
```

条件必须判断“区间相交”，不是只看开始日期：

```text
record_type is event
AND event_start_ts <= query_end_ts
AND event_end_ts >= query_start_ts
```

### 3. “上海最近有什么活动”

在上述当前活动条件上追加：

```text
city is 上海
```

### 4. “有没有线上训练营”

追加：

```text
delivery_mode is online
AND event_type is 训练营
```

### 5. “以前有没有办过类似活动”

只做：

```text
record_type is event
```

不要限制 `active`，否则历史活动全部被排除；语义相关性由 Hybrid/Weighted Score 负责。

## 不建议作为硬过滤的字段

`适合人群` 不放 metadata。原因是“老板/企业主/创始人/CEO/管理层”等自然语言表达开放且容易枚举失控，应写进 child 检索内容，由向量 + 关键词混合检索召回。

## topic 多值

Dify 自定义 metadata 的核心值类型是 string/number/time。脚本把多个 topic 写成：

```text
AI应用|获客增长|私域运营
```

Chatflow 用：

```text
topic contains AI应用
```

不要用 `is` 去匹配其中一个子值。

## 当前状态不要写死为 upcoming/ongoing/ended

这些状态会随着时间变化。保留事实状态 `source_status`，时间生命周期由 `event_start_ts/event_end_ts + now_ts` 动态计算。

## Dify 1.13.3 验证建议

部署后至少用同一个 query + 同一个 metadata filter 连续跑 20 次，确认 Knowledge Retrieval 结果范围稳定；再分别验证：

- active 不召回 cancelled/completed；
- 时间窗口相交正确；
- city/event_type/topic 条件正确；
- 历史活动查询不误加 active 条件；
- 更新同一 `event_id` 后旧内容不残留。
