# 文档清洗容错、咨询方案方法与隔离测试修复 — Change Log

This is a semantic change log, not a copy of Git diff.

## 2026-09-12T06:05:23+08:00 — 在既有清洗链路内实现单元级容错和咨询案例方案/候选方法；内容模型失败仅按两次上限后失败，不再发布原文或创建新的 review_required；混合 PDF 保留可靠 native 页；过期/未成功远端关联不再阻挡重传；隔离测试环境对 general-only、DB/Redis/端口 fail-closed。

- **Purpose:** 在既有清洗链路内实现单元级容错和咨询案例方案/候选方法；内容模型失败仅按两次上限后失败，不再发布原文或创建新的 review_required；混合 PDF 保留可靠 native 页；过期/未成功远端关联不再阻挡重传；隔离测试环境对 general-only、DB/Redis/端口 fail-closed。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** final-regression-r3: 231 passed, 1 skipped; Ruff format/check; compileall; project index --write/--check
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-12T06:07:35+08:00 — cleaning-tolerance-methodology 已实施单元级容错、咨询方案/候选方法、两次上限 fail-closed、逐页 OCR、有效远端去重和 general-only 隔离保护；qwen-batch-cleaning 的 Batch 持续关闭，recent-activity 和 tool-examples 未发生功能语义、DSL 或 API 行为变更，仅同步共享路径触及的当前状态。

- **Purpose:** cleaning-tolerance-methodology 已实施单元级容错、咨询方案/候选方法、两次上限 fail-closed、逐页 OCR、有效远端去重和 general-only 隔离保护；qwen-batch-cleaning 的 Batch 持续关闭，recent-activity 和 tool-examples 未发生功能语义、DSL 或 API 行为变更，仅同步共享路径触及的当前状态。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** final-regression-r3: 231 passed, 1 skipped; Ruff format/check; compileall; project index --write/--check; 未执行外部发布。
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-12T19:21:50+08:00 — 普通重试改为发布阶段allowlist；历史OCR/source失败仅回源或410；每次provider reservation按实际transport计价；general-only拒绝Redis db query；咨询候选方法和PDF/去重边界保持既有最小实现；隔离UI不接管正式服务。

- **Purpose:** 普通重试改为发布阶段allowlist；历史OCR/source失败仅回源或410；每次provider reservation按实际transport计价；general-only拒绝Redis db query；咨询候选方法和PDF/去重边界保持既有最小实现；隔离UI不接管正式服务。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-cleaning-review-r2-20260912.xml: 241 passed, 0 failed/errors/skipped; independent review PASS; index --write/--check and focused static checks passed; gov quality has 16 unrelated current-DSL contract failures.
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-12T20:05:11+08:00 — 完成清洗容错、咨询方案/候选方法、混合 PDF OCR、有效远端去重和隔离环境 fail-closed；补齐当前权威商业顾问 DSL 的派生文件与行为契约测试，未改权威 DSL 或 recent_activity 清洗逻辑。

- **Purpose:** 完成清洗容错、咨询方案/候选方法、混合 PDF OCR、有效远端去重和隔离环境 fail-closed；补齐当前权威商业顾问 DSL 的派生文件与行为契约测试，未改权威 DSL 或 recent_activity 清洗逻辑。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 241 项清洗相关回归、14 项当前 DSL 契约、完整 gov quality、文档契约与接口索引均通过。
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-12T20:32:06+08:00 — 治理工具在内容指纹前排除 tmp 与 pytest 生成物，避免本地临时文件拖慢质量/同步；清洗、OCR、去重、咨询方案与候选方法的产品语义不变。

- **Purpose:** 治理工具在内容指纹前排除 tmp 与 pytest 生成物，避免本地临时文件拖慢质量/同步；清洗、OCR、去重、咨询方案与候选方法的产品语义不变。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** 生成物不再进入 implementation 路径，治理模块编译和差异检查通过；后续完整质量将复跑。
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T00:10:04+08:00 — 明确新创建且从未调用的 realtime 文档须显式 model_attempts=0；历史缺失或损坏计数仍拒绝调用，结果统计保留预算预留账本。

- **Purpose:** 明确新创建且从未调用的 realtime 文档须显式 model_attempts=0；历史缺失或损坏计数仍拒绝调用，结果统计保留预算预留账本。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u06-fixture-r2-20260913.xml; var/reports/W-20260912-041700/pytest-model-workflow-r3-20260913.xml
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T00:15:19+08:00 — 仅格式化已验证的 U06 fixture 与账本断言；行为边界、实现范围和远端门禁不变。

- **Purpose:** 仅格式化已验证的 U06 fixture 与账本断言；行为边界、实现范围和远端门禁不变。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u06-fixture-r3-20260913.xml
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T00:26:53+08:00 — 记录 U06 测试合同已验证、canonical quality 通过及实施再批准 fail-closed 阻塞；不改变产品语义或远端边界。

- **Purpose:** 记录 U06 测试合同已验证、canonical quality 通过及实施再批准 fail-closed 阻塞；不改变产品语义或远端边界。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
  - `contract-docs — 计划与验证文档`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u06-fixture-r3-20260913.xml; var/reports/W-20260912-041700/pytest-model-workflow-r3-20260913.xml; .codex/state/quality.json
- **Changed paths:**
  - `AI管理服务当前状态与后续测试交接.md`
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T00:36:50+08:00 — 治理指纹仅排除根级 var/reports 与指定动态交接，保留其他 var 源码；新增行为测试并纳入 canonical 覆盖。

- **Purpose:** 治理指纹仅排除根级 var/reports 与指定动态交接，保留其他 var 源码；新增行为测试并纳入 canonical 覆盖。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** var/reports/W-20260912-041700/pytest-u11-report-scope-20260913.xml; var/reports/W-20260912-041700/pytest-u11-dynamic-scope-20260913.xml
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T02:02:34+08:00 — 每个新 provider reservation 记录稳定 call ID、文档、实际 transport 和定价快照；realtime usage 仅结算匹配调用，未知 usage 保留 reservation，旧账本 fail-closed。活动契约测试补充 analysis/case 负向路由及有界补查，不改权威 DSL。

- **Purpose:** 每个新 provider reservation 记录稳定 call ID、文档、实际 transport 和定价快照；realtime usage 仅结算匹配调用，未知 usage 保留 reservation，旧账本 fail-closed。活动契约测试补充 analysis/case 负向路由及有界补查，不改权威 DSL。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-model-workflow-round6-20260913.xml=101 passed; pytest-dify-activity-round3-20260913.xml=9 passed; pytest-feature-round7-20260913.xml=233 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T02:54:16+08:00 — 咨询 advice grounding 对同案例未出现的服务费、费用、佣金、订金、定金、支付、购买、续费等新付费条件 fail-closed，方案/方法字段剔除而 case 保留，无额外模型调用；U02/U04/U07/U08/U09/U11 当前行为反例复验通过，仍未进行远端验收。

- **Purpose:** 咨询 advice grounding 对同案例未出现的服务费、费用、佣金、订金、定金、支付、购买、续费等新付费条件 fail-closed，方案/方法字段剔除而 case 保留，无额外模型调用；U02/U04/U07/U08/U09/U11 当前行为反例复验通过，仍未进行远端验收。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-consultation-after-20260913.xml: 37 passed; pytest-u08-u11-after-20260913.xml: 31 passed; pytest-u02-u04-u07-u09-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T03:20:03+08:00 — 隔离 manifest future timestamp 行为反例在执行时构造，确认短时效清单拒绝未来时间；未改变 Compose、Worker、远端或费用行为。

- **Purpose:** 隔离 manifest future timestamp 行为反例在执行时构造，确认短时效清单拒绝未来时间；未改变 Compose、Worker、远端或费用行为。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-time-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T03:21:33+08:00 — future manifest 时间在用例执行时构造并被 fail-closed 拒绝；本次仅按仓库 formatter 做等价排版，无远端、Worker、模型或费用操作。

- **Purpose:** future manifest 时间在用例执行时构造并被 fail-closed 拒绝；本次仅按仓库 formatter 做等价排版，无远端、Worker、模型或费用操作。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-time-after-20260913.xml: 11 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T03:33:06+08:00 — manifest 正负例使用工作区短生命周期临时目录并清理；默认 pytest 临时根仍可运行，隔离脚本语义不变。

- **Purpose:** manifest 正负例使用工作区短生命周期临时目录并清理；默认 pytest 临时根仍可运行，隔离脚本语义不变。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u08-manifest-default-temp-after-20260913.xml: 12 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T03:56:49+08:00 — 咨询派生字段在当前 case advice 未出现明确角色时 fail closed；隔离启动 manifest 绑定并在启动前比对 API/UI/OCR Docker image ID，保持现有费用、队列、Redis 与正式健康门禁。

- **Purpose:** 咨询派生字段在当前 case advice 未出现明确角色时 fail closed；隔离启动 manifest 绑定并在启动前比对 API/UI/OCR Docker image ID，保持现有费用、队列、Redis 与正式健康门禁。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-u08-boundary-full-after-20260913.xml: 58 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T03:59:21+08:00 — 仅格式化当前隔离 manifest 测试夹具；咨询主体 fail-closed 与 image ID 比对行为不变。

- **Purpose:** 仅格式化当前隔离 manifest 测试夹具；咨询主体 fail-closed 与 image ID 比对行为不变。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-u03-u08-boundary-full-after-20260913.xml: 58 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T04:51:48+08:00 — 收紧远端正文见证、咨询行动锚定、OCR/预算/lease fail-closed 与隔离镜像工作树及 wheel 同源门禁；recent_activity 保持既有运行发布链路。

- **Purpose:** 收紧远端正文见证、咨询行动锚定、OCR/预算/lease fail-closed 与隔离镜像工作树及 wheel 同源门禁；recent_activity 保持既有运行发布链路。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T05:47:23+08:00 — 核心咨询字段按来源职责锚定；可检索父子块 witness 版本化；本地 shard 不再作为终态去重证明；delivery lease 与 provider 两次上限分离并 token-fence 副作用。

- **Purpose:** 核心咨询字段按来源职责锚定；可检索父子块 witness 版本化；本地 shard 不再作为终态去重证明；delivery lease 与 provider 两次上限分离并 token-fence 副作用。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review5-failure-collection-20260913.xml: 7 expected failures; pytest-review5-focused-pass-20260913.xml: 7 passed; pytest-review5-affected-full-r3-20260913.xml: 153 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T06:21:59+08:00 — Realtime 在条目和限流后以当前 token 续租，lease 丢失透传；post shard 以受控 metadata 与 v2 parent/child witness 验证远端复用，并在新发布后持久保存 witness。

- **Purpose:** Realtime 在条目和限流后以当前 token 续租，lease 丢失透传；post shard 以受控 metadata 与 v2 parent/child witness 验证远端复用，并在新发布后持久保存 witness。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T07:17:47+08:00 — 活动保持既有发布行为；分片远端验证区分确定性失效与读取不确定，分页和审计写入受当前围栏约束。

- **Purpose:** 活动保持既有发布行为；分片远端验证区分确定性失效与读取不确定，分页和审计写入受当前围栏约束。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review7-affected-full-20260913.xml: 173 passed; pytest-review7-doc-contract-r2-20260913.xml: 13 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T08:07:32+08:00 — Nested Dify pagination/accounting, strict per-document provider ledger reconciliation, specific consultation evidence anchors, and PROFILE-aligned Quality coverage.

- **Purpose:** Nested Dify pagination/accounting, strict per-document provider ledger reconciliation, specific consultation evidence anchors, and PROFILE-aligned Quality coverage.
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T09:17:39+08:00 — Review8 remediation rejects generic four-character and single ASCII anchors while preserving advice-only faithful shortening; fail-closes foreign-batch reservation entries; carries cross-file shard remote IDs only for Worker live verification; classifies duplicate verification uncertainty as publication-only retry; and records local regressions.

- **Purpose:** Review8 remediation rejects generic four-character and single ASCII anchors while preserving advice-only faithful shortening; fail-closes foreign-batch reservation entries; carries cross-file shard remote IDs only for Worker live verification; classifies duplicate verification uncertainty as publication-only retry; and records local regressions.
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review8-new-findings-focused-r7-after-20260913.xml; pytest-review8-consultation-full-r6-after-20260913.xml; pytest-review8-model-full-r5-after-20260913.xml
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T09:31:50+08:00 — Cross-file shard candidate selection and provider cost aggregation now fail closed by batch ownership; local regressions pass while Quality, review, approval and remote acceptance remain pending.

- **Purpose:** Cross-file shard candidate selection and provider cost aggregation now fail closed by batch ownership; local regressions pass while Quality, review, approval and remote acceptance remain pending.
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** Pending
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T10:39:00+08:00 — 固定Dify parent/child分页total；以task-wide ledger阻断历史漏账的新provider reservation；共享/候选远端ID禁止成为错误replacement删除目标且延迟删除复查live引用；咨询按单条advice方向校验并要求独立非重叠证据锚点，同时保留忠实缩写。

- **Purpose:** 固定Dify parent/child分页total；以task-wide ledger阻断历史漏账的新provider reservation；共享/候选远端ID禁止成为错误replacement删除目标且延迟删除复查live引用；咨询按单条advice方向校验并要求独立非重叠证据锚点，同时保留忠实缩写。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** N/A
- **After:** N/A
- **Impact / Notes:** None
- **Validation:** pytest-review10-focused-r5-after-20260913.xml; pytest-review10-regression-r1-20260913.xml; pytest-review10-affected-full-r2-20260913.xml (182 passed)
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`

## 2026-09-13T11:22:22+08:00 — 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。

- **Purpose:** 将跨来源远端删除保护限定为 live publication shard 引用，避免 recent_activity 历史 replacement 行阻断既有精确 outbox cleanup；保留 shared shard 最后引用删除和延迟任务复查。
- **Affected modules:**
  - `semantic-contract — 模型语义与合同`
  - `cleaning-workers — 清洗 Worker 与去重`
  - `pdf-ocr-route — PDF 混合页 OCR 路由`
  - `isolated-test-deploy — 隔离测试部署`
- **Before:** 共享引用 guard 将历史 CleaningDocument replacement 行误判为 shared live reference，活动旧远端删除被跳过。
- **After:** guard 只按 live DifyPublicationShard 判断跨来源共享；活动 replacement 继续由既有精确 outbox cleanup 删除。
- **Impact / Notes:** None
- **Validation:** pytest-review10-activity-cleanup-r1 4 passed；pytest-review10-affected-full-r3 202 passed
- **Changed paths:**
  - `ai_management/api/routers/knowledge_admin.py`
  - `ai_management/cleaning.py`
  - `ai_management/config.py`
  - `ai_management/model_cleaning.py`
  - `ai_management/workers/tasks/cleaning.py`
  - `ai_management/workers/tasks/model_cleaning.py`
  - `docker-compose.cleaning-test-ui.yml`
  - `docker-compose.cleaning-test.yml`
  - `knowledge_cleaner/consultation_rag.py`
  - `knowledge_cleaner/pdf_convert.py`
  - `knowledge_cleaner/rapidocr_backend.py`
  - `scripts/jx_cleaning_test_switch.sh`
  - `scripts/jx_consultation_model_cleaning.py`
  - `tests/test_ai_management.py`
  - `tests/test_consultation_rag_integration.py`
  - `tests/test_model_cleaning_workflow.py`
  - `tests/test_pdf_ocr_quality.py`
