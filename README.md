# jx-now AI 管理服务

这是一个面向当前小程序业务的模块化单体服务。小程序后端继续负责登录、会员和调用权限；本服务负责 Dify 调用、本地商业会话、摘要画像、审核课程推荐、路线进度、点击统计、知识库清洗编排和后台运维日志。

首版只有三个用户入口：

- `business_advisor`：有状态商业顾问，包含知识问答、课程和路线推荐。
- `moments_advisor`：无状态朋友圈顾问。
- `writing_advisor`：无状态日常写作。

内部另有 `summary_profile` Workflow，不是用户入口。

## 当前交付

- FastAPI API、SSE、内部 Token、匿名用户 HMAC。
- PostgreSQL 模型和 Alembic 初始迁移。
- Redis 缓存、会话锁、课程快照和点击计数；Redis 不作为业务事实唯一存储。
- Celery Worker 和凌晨 03:30 定时维护。
- 商业会话、本地历史、每日 Dify 会话段、摘要游标和画像证据保护。
- 课程/路线 Markdown 导入 CLI、候选 ID 校验、签名跳转和路线进度。
- 朋友圈、写作 Workflow 代理。
- 代码内置清洗策略、批量上传、逐文件状态、自动/手动重试和 Dify 残留补偿。
- 四份 Dify 1.13.3 DSL 和独立 Prompt。
- 测试课程、路线、通用知识和 40 条 RAG 黄金问题。
- Streamlit 测试与运维页，可查看课程每日 PV/UV、必要操作和错误日志。

## V2 实施基线

本次改造采用“保留现有实现、先冻结需求和原型、再模块化收敛”的方式，不需要推倒重写：

- [V2 需求规格说明](docs/V2需求规格说明.md)
- [V2 产品流程与边界](docs/V2产品流程与边界.md)
- [项目目录结构](docs/项目目录结构.md)
- [服务开发与数据设计](docs/V2服务开发与数据设计.md)
- [日志与点击说明](docs/日志与点击说明.md)
- [测试与验收规范](docs/V2测试与验收规范.md)
- [服务器原地部署、测试与回滚运行手册](docs/服务器原地部署测试回滚运行手册.md)
- [静态 Demo](demo/README.md)

点击统计口径：每次通过有效签名课程链接跳转，Redis 中的当日 PV 加 1；匿名 UV 去重。定时任务将累计绝对值幂等写入每日汇总表，不创建逐次点击事件表。

## 本地快速启动

1. 创建虚拟环境并安装：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[test]"
   ```

2. 复制配置：

   ```powershell
   Copy-Item .env.example .env
   ```

3. 按 [Dify 创建与绑定指南](docs/Dify创建导入绑定与契约测试.md) 创建两个知识库和四个应用，把 Key 和 Dataset ID 写入 `.env`。旧的测试 Key 不应继续使用。

4. 初始化数据库：

   ```powershell
   alembic upgrade head
   ```

5. 启动三个进程：

   ```powershell
   uvicorn ai_management.main:app --host 0.0.0.0 --port 8000
   celery -A ai_management.tasks.celery_app worker -l INFO -Q default,cleaning --concurrency=2
   celery -A ai_management.tasks.celery_app beat -l INFO
   ```

6. 可选启动测试页：

   ```powershell
   $env:AI_MANAGEMENT_BASE_URL="http://127.0.0.1:8000"
   $env:AI_MANAGEMENT_ADMIN_TOKEN="<与 ADMIN_API_TOKEN 一致>"
   streamlit run ai_management/test_ui.py
   ```

默认开发数据库是本地 SQLite，只用于快速验证。生产必须配置独立 PostgreSQL；Redis 可复用实例但应使用独立 DB 或统一 `ai:` 前缀。

## 先跑通的顺序

```powershell
python -m pytest -q tests
python -m ai_management.cli test_data/courses/测试课程与路线.md --dry-run
```

然后按 Dify 指南导入 DSL、绑定知识库并进行真实 API 调用。Dify 契约未通过前，不要把“本地自动测试通过”当作完整上线验收。

## 目录

- [ai_management](ai_management)：服务、Worker、导入和评测代码。
- [dify/dsl](dify/dsl)：四份 DSL。
- [dify/prompts](dify/prompts)：四份 Prompt 文档。
- [test_data](test_data)：课程、路线、知识库和 RAG 测试资料。
- [docs](docs)：架构、API、Dify、清洗、测试与部署说明。
- [本地到线上部署联调实战手册](docs/本地到线上部署联调实战手册.md)：本地构建、服务器离线发布、配置、测试、排坑和回滚清单。
- [migrations](migrations)：数据库迁移。
