# Architecture Profile

> Status: CURRENT  
> 当前架构事实文件；只描述已存在的项目边界和本 Feature 允许的增量。

## 1. Evidence
- Repository facts: `.codex/governance/repo-facts.json`, `pyproject.toml`, `docs/项目目录结构.md`
- Primary framework/runtime references: FastAPI、Celery/Redis、SQLAlchemy/PostgreSQL、Streamlit、Dify 1.13.3
- Relevant constraints: `AGENTS.md`、`docs/Dify创建导入绑定与契约测试.md`、`docs/troubleshooting/dify.md`、`docs/troubleshooting/data-cleaning.md`

## 2. Stack / Application Shape
- Language: Python 3.11+
- Runtime: Windows-native development; Linux/Docker deployment on jx
- Framework: FastAPI, Celery, SQLAlchemy, Streamlit
- Application type: modular monolith with asynchronous cleaning/publication workers and external Dify Chatflow/RAG
- Deployment shape: Docker Compose services for API, workers, scheduler and test UI; Dify is an external integration
- Data stores/integrations: PostgreSQL for cleaning/publication state, Redis for queue/cache/locks, Dify Knowledge API and Chatflow API

## 3. Complexity Level
- Level: L2
- Why this is the minimum sufficient level: existing strategy registry, worker pipeline, Dify adapters, FastAPI routers, the focused tool-session service and `AdminSetting` JSON configuration provide the required seams; no new service or bounded context is needed.
- Rejected ceremony: no new repository/service/DI layer, no query-time activity database, no separate activity API, no standalone cleaner runtime dependency, and no separate tool-session service process.
- Upgrade triggers: activity query volume or result cardinality exceeds the verified Dify retrieval capacity, or activity lifecycle needs transactional operations beyond document publication.

## 4. Module Topology and Ownership

- Cleaning policy: `ai_management/cleaning.py` and the feature-owned activity parser/module.
- Orchestration/persistence: existing cleaning workers and `CleaningDocument`/`DifyPublicationShard` state in `ai_management/workers/tasks/cleaning.py` and `ai_management/workers/tasks/model_cleaning.py`.
- Dify integration: existing `knowledge_cleaner/dify.py`; the feature only supplies strategy metadata and DSL configuration.
- Chatflow: `dify/商业顾问（当前使用版本）.yml` plus focused DSL contract tests.
- Test surface: `tests/` for service/worker contracts and `Other/event_rag_cleaner` tests for reused parsing/enrichment behavior.

## 5. Dependency Rules
### Allowed

- Strategies may depend on stable parsing/cleaning helpers and model clients, but not on HTTP routers or Streamlit.
- Workers own database transactions and Dify publication side effects.
- Chatflow consumes Dify document content/metadata; it does not call PostgreSQL or Redis.

### Forbidden

- No router/UI direct SQL or Dify calls.
- No Dify business-database mutation.
- No secrets, raw source正文, source filenames or private provenance in Dify business metadata.

## 6. Boundaries
Only describe boundaries that actually exist.
- UI/transport: FastAPI routers expose the internal tool-examples catalog API; the Streamlit test/admin UI consumes it over HTTP; no public internet API or new external service.
- application/orchestration: existing FastAPI/Celery cleaning pipeline.
- domain policy: activity parsing, source-status normalization, business status mapping and metadata allow-list.
- persistence/integration: existing cleaning rows, focused `ToolConversation`/`ToolMessage` tables for temporary tool-session state, Dify Knowledge/Chatflow APIs and `AdminSetting` JSON for optional admin CRUD.
- cross-module communication: `CleanedDocument` metadata and publication records; tool Chatflows receive typed Start inputs and `sys.query`, while the service owns opaque session mapping and retention.

## 7. Shared Code Policy
- Stable shared capabilities: `CleanedDocument`, strategy registry, Markdown/DOCX loading, chunking, Dify metadata projection and publication compensation.
- Ownership: existing cleaning/publishing modules remain owners.
- What must remain feature-owned: event field labels, status mapping, Qwen enrichment schema, registration-link policy and recent-activity Chatflow formatting.
- Rules preventing common/shared/utils dumping grounds: extract only behavior reused by at least two stable strategies; otherwise keep it beside the activity strategy.

## 8. Public Contracts
- APIs/SDK/events: existing admin cleaning upload/inspection APIs and strategy discovery gain one strategy entry; internal tool-examples user/admin CRUD APIs are additive; no new SSE event.
- Compatibility/versioning: existing strategies and metadata remain unchanged; event metadata is additive and namespaced by `record_type=event`.

## 9. Data / Consistency
- Persistence ownership: existing cleaning tables and Dify document IDs.
- Transaction boundaries: local cleaning state is committed before/around external publication according to the existing compensating workflow; Dify metadata is verified before success.
- Migration rules: tool-session tables are added only through the Alembic migration; old tool-example settings are deleted by exact key during that migration; new Dify metadata fields remain idempotent and type conflicts fail closed.
- Tool-examples configuration remains transactionally stored in `AdminSetting` under a kind-scoped key; moments/writing use advanced-chat DSLs with typed Start variables and Dify `conversation_id`, while business DSL and tables remain unchanged.
- Concurrency/idempotency: one activity document per cleaned output; same-name updates replace the previous Dify document through existing publication replacement; source deletion remains explicit.

## 10. Errors / Observability / Sensitive Data

- Unknown mandatory facts skip only that activity; a model failure does not block later activities in the same file.
- Dify publication errors retain retry/compensation state and are recorded in the dated test report.
- Logs and reports contain counts, codes and hashes only; never API keys, raw activity正文 or protected identifiers.

## 11. Test Architecture
- Unit seam: activity parser, status/metadata projection, Chatflow evidence formatter, tool-examples catalog service and tool-session service.
- Integration seam: existing cleaning worker with mocked Dify Knowledge API and strategy discovery/upload contract; FastAPI tool-session/CRUD and auth contracts with mocked Dify Chatflow SSE.
- Contract/system/e2e: jx isolated Dify import/publish/run, followed by formal Chatflow release and test-page full chain.
- Test data: synthetic status/link cases plus `Other/活动.md` only in isolated validation; sample is not seeded into the formal Dataset.

## 12. Canonical Quality Commands

- Format: `ruff format --check ai_management knowledge_cleaner tests Other/event_rag_cleaner`
- Lint/static: `ruff check ai_management knowledge_cleaner tests Other/event_rag_cleaner`
- Typecheck/compile: `python -m compileall -q ai_management knowledge_cleaner Other/event_rag_cleaner`
- Build: not configured; Docker build is release validation only.
- Unit: `python -m pytest -q tests` and `python -m pytest -q Other/event_rag_cleaner`
- Integration: feature-focused pytest selectors configured by the Feature record.
- E2E/system: jx/Dify test-page runbook evidence, not a local mutation command.

## 13. Forbidden Patterns

- Query-time PostgreSQL/Redis lookup solely to implement “最近活动”.
- Relying on Dify similarity order as chronological order.
- Writing `DASHSCOPE_API_KEY` compatibility logic or exposing `BAILIAN_API_KEY`.
- Publishing the provided sample into the formal Dataset.

## 14. Representative Modules
Point to 2–4 modules new code should emulate.

- `ai_management/cleaning.py` strategy registry and `CleanedDocument` contract.
- `ai_management/workers/tasks/cleaning.py` metadata projection/publication compensation.
- `Other/event_rag_cleaner/clean_events.py` event parsing and realtime Qwen contract.
- `dify/商业顾问（当前使用版本）.yml` existing commercial-advisor routing/prompt conventions.
- `ai_management/api/routers/tools.py` and `ai_management/api/routers/admin_management.py` existing internal API/auth conventions.
- `ai_management/services/tool_examples.py` kind-scoped JSON catalog ownership.

## 15. Architecture Decisions / ADR links

- Feature contracts: `docs/features/recent-activity/SPEC.md` and `IMPLEMENTATION.md`; `docs/features/tool-examples/SPEC.md` and `IMPLEMENTATION.md`; `docs/features/tool-conversation-generation/SPEC.md` and `IMPLEMENTATION.md`.
