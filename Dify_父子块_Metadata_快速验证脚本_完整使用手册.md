# Dify 父子块 + Metadata 快速验证脚本完整使用手册

> 适用对象：需要快速验证“个人商业咨询案例总结”清洗策略的人。
>
> 适用范围：把已经清洗好的 Markdown 文档，按照指定父子块分隔符上传到 Dify 测试知识库，写入文档级 metadata，检查实际分段结构，并运行知识库召回测试。
>
> 本手册对应脚本包：`Dify_父子块_Metadata_快速验证脚本.zip`。

---

## 1. 最终要达到什么目的

完成本手册后，应验证下面这条最小链路是否成立：

```text
清洗包 ZIP
  ↓
本地文件与 metadata 一致性检查
  ↓
调用 Dify Service API 上传 5 份 Markdown
  ↓
Dify 按父子块模式解析和建立索引
  ↓
为每份文档写入对应 metadata
  ↓
读取 Dify 实际父块、子块并与本地预期比较
  ↓
调用知识库 retrieve API 运行测试问题
  ↓
输出上传报告和召回测试结果
```

本次验证重点回答以下问题：

1. 清洗后的 5 份 Markdown 是否完整，是否都包含规定的父子分隔符。
2. 文档文件名是否与 `metadata_records.jsonl` 一一对应。
3. Dify 是否真正按 `hierarchical_model` 父子模式入库，而不只是上传接口返回 HTTP 200。
4. 实际父块数、子块数是否与本地清洗结果一致。
5. 父子分隔符是否已经被 Dify 正确移除，没有残留在检索正文里。
6. metadata 字段是否能自动创建，并绑定到正确的文档。
7. 测试问题是否能够召回预期案例，Top-1 是否大体正确。

该脚本只验证知识库层，不验证完整 AI 商业顾问 Chatflow 的最终回答质量。以下内容仍需后续接入 Chatflow 再验证：

- 用户信息不足时是否追问；
- 是否把个案建议错误泛化为通用规律；
- 是否混用不同案例的数据；
- 是否生成不存在的课程或学习路线；
- 最终回答是否有清晰依据、边界和行动建议。

---

## 2. 你需要准备的文件

服务器上需要同时存在两个 ZIP：

```text
Dify_父子块_Metadata_快速验证脚本.zip
Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip
```

两个文件的职责不同：

| 文件 | 用途 |
|---|---|
| `Dify_父子块_Metadata_快速验证脚本.zip` | 包含 Python 脚本、运行脚本、依赖清单和环境变量模板 |
| `Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip` | 包含 5 份清洗后的 Markdown、metadata schema、metadata 记录和检索测试集 |

清洗包不要提前手工解压。验证脚本会自动把它解压到自己的 `work/clean_package/` 临时目录，并根据 ZIP 的路径、大小和修改时间判断是否需要重新解压。

---

## 3. Dify 侧准备

### 3.1 新建一个空测试知识库

建议专门创建一个空知识库，例如：

```text
AI商业顾问-个人咨询案例-快速测试
```

不要第一次就在正式知识库中运行，原因是：

- 脚本会真实创建文档和 metadata 字段；
- 原版脚本不会自动阻止重复上传；
- 上传中途失败后重新运行，可能产生部分重复文档；
- 正式知识库中的旧文档会干扰召回测试；
- file 模式和 text 模式对比时，需要两个干净环境才能判断差异。

如果之前已经试跑过，最稳妥的方法是重新新建一个空测试知识库，而不是直接重复执行 `upload` 或 `all`。

### 3.2 确认 Embedding 模型可用

知识库必须能够正常建立索引。进入 Dify 的模型供应商配置，确认至少有一个可用的文本 Embedding 模型。

如果 Embedding 模型不可用，常见表现包括：

- 文档长时间停留在 indexing；
- `indexing_status=error`；
- worker 日志出现模型认证、额度、连接或超时错误；
- `completed_segments=0`。

### 3.3 创建知识库 Service API Key

进入目标知识库的 **Service API** 页面，创建一个知识库 API Key。

注意：这里必须使用知识库 Service API Key，不是 Chatflow/App API Key。

`.env` 中通常呈现为：

```bash
DIFY_API_KEY=dataset-xxxxxxxxxxxxxxxxxxxxxxxx
```

API Key 属于敏感凭据：

- 只保存在服务器 `.env`；
- 不要发到群聊、工单或代码仓库；
- 不要写进前端代码；
- 建议执行 `chmod 600 .env`。

### 3.4 获取知识库 UUID

需要的是目标知识库 ID，格式通常为 UUID：

```text
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

可从知识库页面地址、Service API 示例或 Dify 接口返回结果中确认。不要把应用 ID、工作流 ID 或文档 ID 填到这里。

### 3.5 确定 Dify Service API 基址

脚本要求 `DIFY_BASE_URL` 以 `/v1` 结尾。

常见示例：

```bash
# 脚本与 Dify 在同一台宿主机，Nginx 暴露 80 端口
DIFY_BASE_URL=http://127.0.0.1/v1

# 使用域名和 HTTPS
DIFY_BASE_URL=https://dify.example.com/v1

# 使用非标准端口
DIFY_BASE_URL=http://127.0.0.1:8080/v1
```

不要填写 Dify 控制台页面地址，例如不要把 `/apps`、`/datasets` 等前端路径放进去。

---

## 4. 把文件上传到服务器

假设统一放在 `/opt`：

```bash
cd /opt
ls -lh
```

预期至少能看到：

```text
Dify_父子块_Metadata_快速验证脚本.zip
Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip
```

使用 SCP 上传的示例：

```bash
scp Dify_父子块_Metadata_快速验证脚本.zip root@服务器IP:/opt/
scp Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip root@服务器IP:/opt/
```

上传后可检查文件是否为空或明显不完整：

```bash
cd /opt
ls -lh Dify_*.zip
unzip -t Dify_父子块_Metadata_快速验证脚本.zip
unzip -t Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip
```

最后应看到类似：

```text
No errors detected in compressed data
```

---

## 5. 安装基础环境

以下命令适用于 Ubuntu 24.04。

```bash
apt-get update
apt-get install -y \
  unzip \
  python3 \
  python3-venv \
  python3-pip \
  ca-certificates
```

确认版本：

```bash
python3 --version
unzip -v | head
```

Ubuntu 24.04 通常自带 Python 3.12，能够运行该脚本。

### 5.1 `ensurepip is not available` 的处理

如果执行 `python3 -m venv .venv` 时出现：

```text
The virtual environment was not created successfully because ensurepip is not available
```

说明系统没有安装对应的 venv 软件包。执行：

```bash
apt-get update
apt-get install -y python3-venv python3-pip
rm -rf .venv
python3 -m venv .venv
```

部分环境也可以显式安装当前 Python 版本：

```bash
apt-get install -y python3.12-venv
```

必须先删除失败后留下的不完整 `.venv`，再重新创建。

不要在虚拟环境创建失败后继续执行：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

否则 `activate` 文件不存在，后面的 pip 可能落到系统 Python，并触发 Ubuntu 的 `externally-managed-environment` 保护。

不要使用下面方式强行破坏系统 Python：

```bash
pip install --break-system-packages ...
```

---

## 6. 解压脚本包

为了防止混入旧文件，先清理旧测试目录：

```bash
cd /opt
rm -rf dify_quick_validation_kit
```

解压：

```bash
unzip Dify_父子块_Metadata_快速验证脚本.zip
cd dify_quick_validation_kit
```

查看目录：

```bash
ls -la
```

应看到：

```text
.env.example
README.md
dify_quick_test.py
requirements.txt
run.sh
```

将清洗包移动到脚本目录：

```bash
mv ../Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip ./
```

检查：

```bash
ls -lh
```

现在目录中还应看到：

```text
Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip
```

也可以不移动 ZIP，而是在 `.env` 的 `CLEAN_PACKAGE_ZIP` 中填写绝对路径，例如：

```bash
CLEAN_PACKAGE_ZIP=/opt/Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip
```

两种方式任选一种，不要同时写错路径。

---

## 7. 创建 Python 虚拟环境并安装依赖

在脚本目录执行：

```bash
cd /opt/dify_quick_validation_kit
python3 -m venv .venv
source .venv/bin/activate
```

确认当前 Python 来自 `.venv`：

```bash
which python3
which pip
```

预期路径包含：

```text
/opt/dify_quick_validation_kit/.venv/
```

安装依赖：

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

脚本只需要 `requests`，依赖约束为：

```text
requests>=2.31,<3
```

确认安装成功：

```bash
python3 -c "import requests; print(requests.__version__)"
```

如果服务器访问 PyPI 较慢，可临时使用可用的 Python 包镜像源，例如：

```bash
python3 -m pip install \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  -r requirements.txt
```

镜像源能否使用取决于当前服务器网络，不要在生产配置中长期关闭证书校验。

---

## 8. 创建并填写 `.env`

复制模板：

```bash
cp .env.example .env
```

编辑：

```bash
nano .env
```

推荐测试配置示例：

```bash
# 清洗包ZIP：与脚本同目录时可直接写文件名
CLEAN_PACKAGE_ZIP=Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip

# Dify Service API 基址，必须包含 /v1
DIFY_BASE_URL=http://127.0.0.1/v1

# 目标知识库的 Service API Key
DIFY_API_KEY=dataset-xxxxxxxxxxxxxxxxxxxxxxxx

# 空测试知识库 UUID
DIFY_DATASET_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 第一次优先测试 create-by-file
DIFY_UPLOAD_MODE=file

# 默认快速测试前12条；0代表全部
TEST_LIMIT=12

# 每3秒查询一次索引状态
POLL_INTERVAL=3

# 单份文档最长等待索引600秒
POLL_TIMEOUT=600

# 单次HTTP请求超时60秒
REQUEST_TIMEOUT=60

# HTTPS证书校验
VERIFY_SSL=true
```

保存后收紧权限：

```bash
chmod 600 .env
```

检查是否仍然保留示例占位符：

```bash
grep -E 'xxxxxxxx|你的|example' .env
```

如果输出了 API Key 或 UUID 的占位符，必须修改后再运行。

### 8.1 `.env` 参数解释

| 参数 | 必填 | 作用 |
|---|---:|---|
| `CLEAN_PACKAGE_ZIP` | 是 | 清洗包 ZIP 路径；支持相对脚本目录或绝对路径 |
| `CLEAN_PACKAGE_DIR` | 否 | 已手工解压清洗包时可直接指定目录；设置后优先于 ZIP |
| `DIFY_BASE_URL` | 是 | Dify Service API 基址，通常以 `/v1` 结尾 |
| `DIFY_API_KEY` | 是 | 目标知识库 Service API Key |
| `DIFY_DATASET_ID` | 是 | 目标空测试知识库 UUID |
| `DIFY_UPLOAD_MODE` | 是 | `file` 或 `text` |
| `TEST_LIMIT` | 否 | 默认测试条数；`12` 为快速测试，`0` 为全部 |
| `POLL_INTERVAL` | 否 | 索引状态轮询间隔，单位秒 |
| `POLL_TIMEOUT` | 否 | 每份文档索引等待上限，单位秒 |
| `REQUEST_TIMEOUT` | 否 | 单次 API 请求超时，单位秒 |
| `VERIFY_SSL` | 否 | 是否验证 HTTPS 证书 |

系统环境变量的同名值会优先于 `.env`。脚本读取 `.env` 时使用的是“不覆盖已有环境变量”的逻辑。如果发现修改 `.env` 后仍使用旧值，可执行：

```bash
unset DIFY_BASE_URL DIFY_API_KEY DIFY_DATASET_ID DIFY_UPLOAD_MODE
```

然后重新运行。

---

## 9. 脚本实际使用的父子块参数

脚本上传时发送：

```text
doc_form = hierarchical_model
indexing_technique = high_quality
process_rule.mode = hierarchical
parent_mode = paragraph
doc_language = Chinese
```

父块设置：

```text
分隔符：\n<<<DIFY_PARENT>>>\n
上限：1400
```

子块设置：

```text
分隔符：\n<<<DIFY_CHILD>>>\n
上限：420
```

预处理设置：

```text
remove_extra_spaces = false
remove_urls_emails = false
```

关闭额外空格和 URL/邮箱自动清洗，是为了尽量保持已经清洗好的 Markdown 不被 Dify 再次改写，尤其不能影响显式父子分隔符。

---

## 10. 推荐的执行顺序

第一次不要直接运行 `./run.sh`。推荐按以下顺序分层验证。

### 第一步：只检查本地清洗包

```bash
cd /opt/dify_quick_validation_kit
source .venv/bin/activate
python3 dify_quick_test.py check
```

这个命令不会访问 Dify，也不会上传文档。

它会检查：

- 能否找到清洗包 ZIP；
- ZIP 是否能正常解压；
- 能否唯一定位清洗后的 Markdown 目录；
- 能否找到 `metadata_records.jsonl`；
- 能否找到 `metadata_schema.json`；
- 能否找到 `retrieval_test_set_v2.csv`；
- Markdown 文件集合是否与 metadata 文件名完全一致；
- 每份 Markdown 是否包含父子分隔符；
- 本地预期父块数和子块数。

正常输出应类似：

```text
本地清洗包校验通过：
  case_xxx.md  父块=4 子块=14
  ...
```

只要 `check` 不通过，不要继续上传。

### 第二步：先确认 Dify API 连通性

原版脚本没有独立的 `doctor` 命令，可以使用 curl 做最小连通检查：

```bash
set -a
source .env
set +a

curl -sS \
  -H "Authorization: Bearer ${DIFY_API_KEY}" \
  "${DIFY_BASE_URL}/datasets/${DIFY_DATASET_ID}"
```

正常应返回目标知识库信息，而不是 HTML 登录页。

常见判断：

- 返回知识库 JSON：基址、Key 和知识库 ID 基本正确；
- `401`：API Key 错误或不是知识库 Service API Key；
- `403`：Key 权限或工作区权限不正确；
- `404`：知识库 ID、API 基址或反向代理路径错误；
- 返回 HTML：通常是填写了前端地址或反向代理没有把 `/v1` 转到 Dify API；
- Connection refused：端口、Nginx 或网络路径不通。

检查完可清除当前 Shell 中的敏感变量：

```bash
unset DIFY_API_KEY
```

### 第三步：上传、等待索引、写 metadata、检查父子块

确认目标知识库为空后执行：

```bash
source .venv/bin/activate
python3 dify_quick_test.py upload
```

脚本对每份文档依次执行：

1. 查询知识库信息；
2. 查询现有 metadata 字段；
3. 按 `metadata_schema.json` 自动创建缺失的 string/time 字段；
4. 使用 `create-by-file` 或 `create-by-text` 创建文档；
5. 获取 `document.id` 和 `batch`；
6. 轮询 indexing-status；
7. 确认索引状态为 `completed`；
8. 确认 `completed_segments > 0`；
9. 把 `YYYY-MM-DD` 时间转换成 Unix 秒时间戳；
10. 按 metadata field ID 写入每份文档；
11. 读取实际父块；
12. 逐个读取父块的子块；
13. 比较实际数量与本地预期；
14. 检查分隔符是否残留；
15. 写入上传报告。

正常输出类似：

```text
上传 case_xxx.md（file）
    状态=indexing 分段=0/4
    状态=completed 分段=4/4
    父块 4/4，子块 14/14，验证=pass
```

5 份全部通过后，会生成：

```text
output/upload_report.json
```

上传过程中还会持续写：

```text
output/upload_report.partial.json
```

如果第 3 份失败，可以从 partial 文件判断前两份是否已经创建，避免盲目重复上传。

### 第四步：在 Dify 页面人工抽查

进入测试知识库，抽查至少两份文档：

- 文档状态是否可用；
- 是否是父子分段模式；
- 父块主题是否完整；
- 子块是否具有明确主语；
- `[来源事实]`、`[原咨询诊断]`、`[原咨询建议]`、`[待验证]` 是否保留；
- `<<<DIFY_PARENT>>>` 和 `<<<DIFY_CHILD>>>` 是否没有进入正文；
- metadata 是否能看到 `case_id`、`business_stage`、`bottleneck_primary` 等字段；
- 每份文档的 `case_id` 是否不同。

### 第五步：运行快速召回测试

```bash
source .venv/bin/activate
python3 dify_quick_test.py test
```

默认读取 `.env` 的：

```bash
TEST_LIMIT=12
```

脚本会运行前 12 条问题，只测试知识库召回，不调用 LLM 生成最终回答。

输出示例：

```text
[01] T001 hit=True top1=True case_xxx:0.78 | case_yyy:0.61
```

字段含义：

- `hit=True`：返回结果中至少出现一个预期案例；
- `top1=True`：第一条结果就是预期案例；
- 后面列出前 3 条结果的 case_id 和分数。

### 第六步：运行完整 32 条测试

```bash
python3 dify_quick_test.py test --all-tests
```

也可以把 `.env` 修改为：

```bash
TEST_LIMIT=0
```

然后执行普通 `test`。

完整测试包含：

- 自然语言改写；
- 阶段判断；
- 数字边界；
- 多跳问题；
- 跨案例污染；
- 错误前提；
- 信息不足；
- 课程事实缺失等。

需要特别注意：当前脚本会读取测试集中的 `metadata_filter_json` 字段，但 `retrieve` 请求只发送 `query`，没有把 metadata 过滤条件传入 Dify。因此当前版本验证的是：

- metadata 是否写入；
- 检索结果是否能返回正确 `case_id`；
- 当前知识库默认检索是否能找到预期案例。

它还没有真正验证“按 metadata 条件过滤后再检索”的效果。metadata 条件过滤需要后续在脚本或 Chatflow 检索节点中单独测试。

---

## 11. 快捷命令的真实行为

### 11.1 `./run.sh`

```bash
./run.sh
```

实际执行：

```bash
python3 dify_quick_test.py check
python3 dify_quick_test.py all
```

而 `all` 又会：

```text
本地 check
→ upload
→ 默认前12条 test
```

因此 `./run.sh` 会重复做一次本地检查，但不影响结果。

它适合：

- 环境已经确认；
- `.env` 已配置正确；
- 知识库确定为空；
- 已经理解脚本会创建真实文档。

它不适合首次排错，因为某个阶段失败时不如分步执行清楚。

### 11.2 一次上传并运行快速测试

```bash
python3 dify_quick_test.py all
```

执行：

```text
upload + 前 TEST_LIMIT 条检索测试
```

### 11.3 一次上传并运行全部测试

```bash
python3 dify_quick_test.py all --all-tests
```

只应在全新的空测试知识库中运行一次。

---

## 12. file 模式和 text 模式

### 12.1 默认 file 模式

`.env`：

```bash
DIFY_UPLOAD_MODE=file
```

调用：

```text
POST /datasets/{dataset_id}/document/create-by-file
```

优点：

- 与生产中的文件上传方式更接近；
- Dify 中保留上传文件的数据来源类型；
- 能直接验证 Markdown 文件上传链路。

### 12.2 text 模式

`.env`：

```bash
DIFY_UPLOAD_MODE=text
```

调用：

```text
POST /datasets/{dataset_id}/document/create-by-text
```

它读取相同 Markdown 文本、使用相同父子分隔符和 metadata，只改变文档创建入口。

### 12.3 什么时候切换 text

如果 file 模式出现以下情况：

- HTTP 创建成功，但 `completed_segments=0`；
- `doc_form` 不是 `hierarchical_model`；
- 实际父块数或子块数与预期不一致；
- 分隔符进入正文；
- file 模式稳定失败，但文本内容本身通过本地检查。

不要在原知识库中直接切换模式重跑。推荐：

1. 新建第二个空测试知识库；
2. 修改 `DIFY_DATASET_ID`；
3. 设置 `DIFY_UPLOAD_MODE=text`；
4. 重新运行 `upload`；
5. 对比两个 `upload_report.json`。

这样才能判断问题来自清洗内容，还是 `create-by-file` 入口行为。

---

## 13. 输出文件说明

运行后在：

```text
/opt/dify_quick_validation_kit/output/
```

### 13.1 `upload_report.partial.json`

每成功处理一份文档就更新一次。

用途：

- 上传中途失败时查看已完成文档；
- 获取 document ID 和 batch；
- 判断是否已经产生部分数据；
- 避免直接重复执行造成重复文档。

### 13.2 `upload_report.json`

只有全部文档上传、索引、metadata 和父子块检查通过后才生成。

重点查看：

```json
{
  "status": "pass",
  "upload_mode": "file",
  "documents": [
    {
      "filename": "...",
      "document_id": "...",
      "batch": "...",
      "chunk_validation": {
        "status": "pass",
        "doc_form": "hierarchical_model",
        "expected_parents": 4,
        "actual_parents": 4,
        "expected_children": 14,
        "actual_children": 14,
        "delimiter_leaks": []
      }
    }
  ]
}
```

最低要求：

```text
status = pass
doc_form = hierarchical_model
expected_parents = actual_parents
expected_children = actual_children
delimiter_leaks = []
```

### 13.3 `retrieval_results.csv`

适合用表格查看每条测试：

- 测试 ID；
- 查询问题；
- 问题类型；
- 预期案例；
- 实际返回案例；
- 是否命中；
- Top-1 是否正确；
- Top-3 结果；
- 是否属于应拒答问题。

### 13.4 `retrieval_results_raw.json`

保存 Dify `/retrieve` 的原始响应，包括：

- score；
- segment content；
- document 信息；
- doc_metadata；
- 脚本解析后的 case_id。

当 CSV 显示命中异常时，应从该文件检查：

- 是否召回了错误父块；
- metadata 是否没有随文档返回；
- 分数是否非常接近；
- 返回文档名称是否与 case_id 对应；
- 是否出现跨案例内容。

### 13.5 `retrieval_summary.json`

示例：

```json
{
  "tested": 12,
  "scored_tests": 12,
  "expected_case_hit_rate": 1.0,
  "top1_accuracy": 0.8333,
  "note": "拒答、建议质量和课程防幻觉需要接入AI商业顾问Chatflow后测试。"
}
```

字段解释：

- `tested`：实际运行的总测试数；
- `scored_tests`：具有明确预期案例、被纳入命中率统计的测试数；
- `expected_case_hit_rate`：返回结果中出现预期案例的比例；
- `top1_accuracy`：第一条结果就是预期案例的比例。

完整 32 条中有一些问题故意没有指定预期案例，用于观察拒答或信息不足。这些问题不会进入当前脚本的 case 命中率分母，所以 `scored_tests` 可能小于 `tested`。

---

## 14. 建议的快速验收标准

下面是测试阶段的建议门槛，不是行业统一标准。

### 14.1 必须全部通过

```text
5份文档全部 indexing_status=completed
每份 completed_segments > 0
5份 chunk_validation.status 全部为 pass
doc_form 全部为 hierarchical_model
实际父块数等于本地预期
实际子块数等于本地预期
delimiter_leaks 为空
metadata 写入接口无报错
```

### 14.2 召回测试建议

快速前 12 条：

```text
expected_case_hit_rate 建议达到 0.90 以上
top1_accuracy 建议达到 0.80 以上
```

如果低于该水平，先不要急着修改清洗文档，依次检查：

1. Embedding 模型是否适合中文；
2. 知识库当前默认检索配置；
3. 是否启用混合检索和 Rerank；
4. Top K 是否太小；
5. 是否有重复上传或旧文档；
6. 父块是否过大或子块是否缺少关键主语；
7. 返回 metadata 是否正确。

完整测试还要人工检查：

- 数字是否被错误跨案例拼接；
- 个案建议是否被当作普遍结论；
- 没有课程信息时是否承认当前资料不足；
- 信息不足时是否提示需要补充用户画像。

---

## 15. 常见故障和处理

### 15.1 找不到清洗包

报错：

```text
找不到清洗包：...
```

检查：

```bash
pwd
ls -lh
cat .env | grep CLEAN_PACKAGE
```

处理方式一：把 ZIP 放进当前脚本目录。

```bash
mv /opt/Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip \
  /opt/dify_quick_validation_kit/
```

处理方式二：在 `.env` 写绝对路径。

### 15.2 `No module named requests`

说明当前执行脚本的 Python 不是安装依赖的虚拟环境。

```bash
cd /opt/dify_quick_validation_kit
source .venv/bin/activate
which python3
python3 -m pip install -r requirements.txt
python3 -c "import requests"
```

### 15.3 `externally-managed-environment`

说明 pip 正在尝试写入 Ubuntu 管理的系统 Python。

不要使用 `--break-system-packages`。重新创建并激活 venv：

```bash
apt-get install -y python3-venv
cd /opt/dify_quick_validation_kit
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

### 15.4 HTTP 401

常见原因：

- API Key 复制不完整；
- 使用了 App API Key；
- Key 已被撤销；
- `.env` 中仍是示例值；
- Shell 环境变量覆盖了 `.env`。

检查：

```bash
grep '^DIFY_API_KEY=' .env
```

不要把完整 Key 输出到共享终端日志。

### 15.5 HTTP 404

常见原因：

- `DIFY_DATASET_ID` 不是知识库 UUID；
- `DIFY_BASE_URL` 没有 `/v1`；
- 域名反向代理没有代理 Service API；
- API Key 与知识库不属于同一工作区。

### 15.6 无法连接或 Connection refused

检查 Dify 容器：

```bash
cd /opt/dify/docker
docker compose ps
```

检查 Nginx 和 API 日志：

```bash
docker compose logs --tail=200 nginx
docker compose logs --tail=200 api
```

如果脚本在 Dify 宿主机运行，优先确认：

```bash
curl -I http://127.0.0.1/
```

若 Dify 实际映射到其他端口，应在 `DIFY_BASE_URL` 中填写该端口。

### 15.7 HTTPS 证书错误

优先修复证书链和域名配置。

只在内部临时测试自签名证书时，可设置：

```bash
VERIFY_SSL=false
```

这会关闭 TLS 证书验证，不应作为长期生产配置。

### 15.8 metadata 字段类型冲突

脚本发现已有同名字段，但类型不同，会停止。例如脚本需要 `source_date=time`，知识库中却已有 `source_date=string`。

最稳妥的测试处理是：

- 新建空知识库；或
- 删除错误字段后重新创建。

不要让脚本自动覆盖已有字段类型，因为可能影响旧文档。

### 15.9 索引长时间不完成

先适当增加：

```bash
POLL_TIMEOUT=1200
REQUEST_TIMEOUT=120
```

再检查：

```bash
cd /opt/dify/docker
docker compose logs --tail=300 worker
docker compose logs --tail=300 api
```

重点排查：

- Embedding 模型不可用；
- 网络超时；
- worker 不健康；
- Redis 或数据库连接异常；
- 向量数据库写入失败；
- 文档解析任务积压。

### 15.10 completed 但分段为 0

脚本会主动判定失败：

```text
索引显示 completed，但 completed_segments=0
```

处理：

1. 不要把这次测试视为成功；
2. 新建空知识库；
3. 修改 `.env`：

```bash
DIFY_UPLOAD_MODE=text
```

4. 重新执行 `upload`；
5. 对比 file 与 text 结果；
6. 同时检查 worker 和 api 日志。

### 15.11 父块或子块数量不一致

脚本会输出：

```text
父块 实际/预期，子块 实际/预期，验证=fail
```

可能原因：

- 当前 Dify 版本没有按请求参数处理父子模式；
- 分隔符被额外预处理；
- API 字段在当前版本中的语义不同；
- 文档被重复或异常截断；
- file 解析与 text 创建入口行为不同。

不要只调整数字让脚本“通过”。先在 Dify 页面查看实际分段内容，再决定是 API 兼容问题还是清洗规则问题。

### 15.12 重复运行后出现重复文档

原版脚本没有幂等删除和非空知识库保护。

如果 `upload` 或 `all` 已经创建了部分文档，不要直接再次运行。推荐：

- 新建一个空测试知识库；
- 更新 `.env` 的 `DIFY_DATASET_ID`；
- 重新完整测试。

这比人工判断并删除部分文档更可靠。

### 15.13 检索结果为空

检查：

- 文档是否已启用；
- indexing_status 是否 completed；
- 向量索引是否正常；
- Embedding 模型是否更换后没有重建索引；
- 当前知识库默认检索配置是否过严；
- Score Threshold 是否过高；
- Top K 是否太小。

当前脚本调用 `/retrieve` 时只发送 query，没有显式传入 `retrieval_model`，因此它验证的是目标知识库当前默认的检索行为。

### 15.14 2026-07-22 实测踩坑复盘

本节记录在 Dify 知识库“商业顾问”上的一次完整实测。以后再次部署时，建议先阅读本节。

#### 问题一：用户提供的 `dataset-...` 不是知识库 UUID

Service API Key 通常以 `dataset-` 开头，但 `DIFY_DATASET_ID` 必须是 UUID，两者不能混用。可以通过 API Key 查询知识库列表，再按名称确认 UUID：

```bash
curl -sS \
  -H "Authorization: Bearer ${DIFY_API_KEY}" \
  "${DIFY_BASE_URL}/datasets?page=1&limit=100"
```

本次知识库名称为“商业顾问”，查询到的 UUID 为：

```text
a05a3c77-eeb7-40c7-a8b2-260c5f4e0da2
```

拿到 UUID 后，必须先确认知识库为空：

```bash
curl -sS \
  -H "Authorization: Bearer ${DIFY_API_KEY}" \
  "${DIFY_BASE_URL}/datasets/${DIFY_DATASET_ID}/documents?page=1&limit=100"
```

只有返回 `total=0` 时才执行首次上传。

#### 问题二：现有 `.venv` 目录存在，但里面没有 pip

仅检查 `.venv` 目录是否存在不够。本次环境执行 `.venv/bin/python -m pip` 时出现：

```text
No module named pip
```

应显式验证：

```bash
test -x .venv/bin/python
.venv/bin/python -m pip --version
.venv/bin/python -c "import requests; print(requests.__version__)"
```

若失败，先安装系统 venv 组件，并保留旧目录后重建：

```bash
apt-get update
apt-get install -y python3-venv python3-pip ca-certificates unzip

cd /opt/dify_quick_validation_kit
mv .venv .venv.incomplete-$(date +%Y%m%d%H%M%S)
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

不要继续使用缺少 pip 的虚拟环境，也不要使用 `--break-system-packages`。

#### 问题三：脚本误把包根目录识别为第二个 Markdown 上传目录

本次 `check` 报错：

```text
无法唯一定位清洗后的 Markdown 目录，候选数=2
```

原因是包根目录的说明文档也包含 `<<<DIFY_PARENT>>>` 示例。旧逻辑仅判断一个目录中的 Markdown 是否包含父分隔符，因此把说明目录也当成候选目录。

正确判断方式是：候选目录中的 Markdown 文件名集合必须与 `metadata_records.jsonl` 的 `filename` 集合完全相同。核心修复如下：

```python
expected_names = {
    str(json.loads(line)["filename"])
    for line in metadata_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
}

markdown = list(directory.glob("*.md"))
if (
    {path.name for path in markdown} == expected_names
    and all("<<<DIFY_PARENT>>>" in path.read_text(encoding="utf-8") for path in markdown)
):
    docs_candidates.append(directory)
```

不要通过删除说明文档来绕过问题，否则清洗包的完整性和校验和会被破坏。

#### 问题四：file 模式索引完成，但子块数量只有 `5/14`

本次 `create-by-file` 的实际结果为：

```text
状态=completed 分段=4/4
父块 4/4，子块 5/14，验证=fail
```

这说明 HTTP 200 和索引完成不等于父子块解析正确。处理顺序：

1. 立即停止，不继续上传剩余文档；
2. 查询知识库文档列表，取得这份试验文档的 ID；
3. 删除该失败文档，重新确认 `total=0`；
4. 保留已创建的 metadata 字段；
5. 将 `.env` 改为 `DIFY_UPLOAD_MODE=text`；
6. 从空库重新执行完整上传。

不要直接在非空知识库中切换模式重跑，否则会产生重复文档并污染召回结果。

#### 问题五：text 模式数量完全匹配，但旧脚本仍报告分隔符残留

text 模式实际生成了 `4/4` 个父块和 `14/14` 个子块，但旧验证逻辑检查父块管理接口的 `segment.content`，因此报告 4 个父块全部存在分隔符。

Dify 当前版本的行为是：

- 父块管理接口可能保留用于生成子块的原始子分隔符；
- 实际子块正文已经移除分隔符；
- `/retrieve` 返回的召回正文也没有分隔符。

因此，“分隔符是否泄漏到检索正文”应检查 `child_chunks[].content`，不能只检查父块管理接口。核心修复如下：

```python
children = self.call(
    "GET",
    f"/datasets/{self.dataset_id}/documents/{document_id}/segments/{segment['id']}/child_chunks",
    params={"page": 1, "limit": 100},
)
child_rows = children.get("data", [])
actual_children += int(children.get("total", len(child_rows)) or 0)

for child in child_rows:
    content = str(child.get("content", ""))
    if "<<<DIFY_PARENT>>>" in content or "<<<DIFY_CHILD>>>" in content:
        leaks.append(child.get("id"))
```

修正后仍应额外调用一次 `/retrieve` 抽查返回正文，不能为了让报告通过而直接删除分隔符校验。

#### 问题六：短超时会中断 SSH 前台上传命令

上传需要等待 Dify worker 完成解析和向量索引。若调用端只给 1 秒超时，SSH 进程会被终止。虽然本次中断发生在创建文档之前，但任何超时后都不能直接重跑。

正确做法：

1. 检查 `output/upload_report.partial.json`；
2. 调用 documents API 检查知识库当前文档数；
3. 确认没有文档后才能重跑；
4. 前台执行至少预留 180 秒，或使用可监控的后台任务。

#### 问题七：Windows PowerShell 调用远程 SSH 时会提前解释特殊字符

从 Windows PowerShell 拼接远程 Bash 命令时，以下内容容易在本地被提前解析：

- `$VAR` 和 `$(pwd)`；
- URL 中的 `&`；
- `<<<DIFY_CHILD>>>` 中的 `<`；
- 多层 JSON、jq 和引号。

常见表现包括 Authorization Header 丢失、`curl` 把 `Bearer` 当主机名、`&limit=100` 被当成 PowerShell 后台任务。

建议：

- 简单请求尽量避免远程临时变量，或把远程脚本保存为文件后执行；
- URL 始终完整引用；
- 复杂 JSON/jq 逻辑改为服务器端脚本；
- 每次写操作前先用只读请求验证最终参数；
- 不要在共享日志中输出完整 API Key。

#### 本次最终实测结果

```text
上传模式：text
文档数：5
每份父块：4/4
每份子块：14/14
文档状态：全部 completed、enabled
metadata：每份文档均已写入
分隔符泄漏：实际子块正文中为 0
完整测试：32 条
预期案例命中率：96.55%
Top-1 准确率：75.86%
```

命中率达到建议标准，Top-1 略低于建议的 80%。后续应优先评估混合检索、Rerank、Top K 和 Embedding 模型，不要先修改已经通过结构验收的清洗文档。

---

## 16. 测试完成后的处理

### 16.1 保存结果

建议把整个 output 目录打包保存：

```bash
cd /opt/dify_quick_validation_kit
tar -czf dify_validation_output_$(date +%Y%m%d_%H%M%S).tar.gz output/
```

保存这些文件有助于后续比较：

- 不同 Embedding 模型；
- file 与 text 上传方式；
- 不同 Top K 或 Rerank；
- 不同清洗版本；
- 不同父子块大小。

### 16.2 退出虚拟环境

```bash
deactivate
```

### 16.3 保护或删除 `.env`

如果测试服务器会共享给其他人，完成后可：

```bash
chmod 600 .env
```

不再使用时删除 API Key，并在 Dify 中撤销对应 Service API Key。

### 16.4 测试知识库的处理

测试知识库建议保留到完成结果复盘。确认策略后：

- 可删除测试知识库；或
- 明确标记为测试用途，避免被正式 Chatflow 引用。

---

## 17. 最短可执行流程

已确认系统安装了 `python3-venv` 的情况下，可按下面顺序执行：

```bash
cd /opt

apt-get update
apt-get install -y unzip python3 python3-venv python3-pip ca-certificates

rm -rf dify_quick_validation_kit
unzip Dify_父子块_Metadata_快速验证脚本.zip
cd dify_quick_validation_kit

mv ../Dify_AI商业顾问_样本文档清洗包_v2_修复版.zip ./

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

cp .env.example .env
nano .env
chmod 600 .env

python3 dify_quick_test.py check
python3 dify_quick_test.py upload
python3 dify_quick_test.py test --all-tests

ls -lh output/
cat output/retrieval_summary.json
```

---

## 18. 最终成功判定

只有同时满足以下条件，才说明这次“清洗文档 + Dify 父子块 + metadata + 基础召回”验证链路成功：

```text
[本地清洗包]
5份Markdown均被识别
文件名与metadata一一对应
父子分隔符存在
本地父子数量可计算

[Dify入库]
5份文档全部索引完成
每份completed_segments > 0
doc_form = hierarchical_model
实际父块数 = 预期父块数
实际子块数 = 预期子块数
分隔符无残留
metadata写入成功

[召回测试]
测试问题能够召回预期案例
Top-1整体表现可接受
没有明显跨案例数据污染
没有因重复文档造成结果失真
```

完成这些验证后，可以确认：

> 这套“个人商业咨询案例总结”清洗产物能够通过 Dify Service API，按照指定父子块规则入库，文档级 metadata 能被写入，知识库能够根据测试问题召回相应案例。

但仍不能直接确认：

> AI 商业顾问的最终回答已经达到生产级高质量。

下一阶段应把测试知识库接入 AI 商业顾问 Chatflow，针对真实用户画像继续验证问题理解、案例引用、事实边界、追问策略和回答质量。

---

## 19. 命令速查

```bash
# 激活环境
cd /opt/dify_quick_validation_kit
source .venv/bin/activate

# 只检查本地清洗包
python3 dify_quick_test.py check

# 上传、索引、metadata和父子块验证
python3 dify_quick_test.py upload

# 快速测试前12条
python3 dify_quick_test.py test

# 完整测试32条
python3 dify_quick_test.py test --all-tests

# 上传并快速测试
python3 dify_quick_test.py all

# 上传并完整测试
python3 dify_quick_test.py all --all-tests

# 快捷执行：check + all
./run.sh

# 查看结果
ls -lh output/
cat output/upload_report.json
cat output/retrieval_summary.json

# 查看Dify容器状态
cd /opt/dify/docker
docker compose ps

# 查看索引相关日志
docker compose logs --tail=300 worker
docker compose logs --tail=300 api
```
