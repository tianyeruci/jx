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
- Cleaning orchestration/persistence: existing cleaning workers and `CleaningDocument`/`DifyPublicationShard` state in `ai_management/workers/tasks/cleaning.py` and `ai_management/workers/tasks/model_cleaning.py`.
- Dify knowledge/RAG integration: `knowledge_cleaner/dify.py`; cleaning workers retain publication replacement, retry and compensation ownership, while the recent-activity strategy remains feature-owned.
- Tool examples and session configuration: `ai_management/services/tool_examples.py`, existing `ToolConversation`/`ToolMessage` temporary session state, and kind-scoped `AdminSetting` JSON.
- Business conversation orchestration/persistence: `ai_management/conversations.py`, `ai_management/context.py`, `ai_management/workers/tasks/summary.py`, and existing `BusinessConversation` / `BusinessMessage` rows.
- Tool message operations: `ai_management/services/tool_conversations.py`, `ai_management/api/routers/tools.py` and `ai_management/dify.py`; Dify retains tool正文、feedback and remote message history, while this service retains only the session mapping, task markers and exact cleanup targets.
- HTTP/SSE and test UI: `ai_management/api/routers/business.py`, `ai_management/api/routers/tools.py`, `ai_management/api/routers/entry.py` and `ai_management/test_ui.py`.
- Chatflow assets: existing business-advisor DSL plus `dify/dsl/moments_advisor.yml` and `dify/dsl/writing_advisor.yml`; focused contract tests protect prompt/model/Memory boundaries.
- Test surface: `tests/` covers cleaning/worker strategies, tool examples, API/SSE/Dify client and conversation/summary contracts; `Other/event_rag_cleaner` covers reused parsing/enrichment behavior; jx/Dify validation is release evidence.

## 5. Dependency Rules
### Allowed

- Strategies may depend on stable parsing/cleaning helpers and model clients, but not on HTTP routers or Streamlit.
- Workers own cleaning database transactions plus Dify Knowledge publication side effects, retries and compensation.
- Internal routers may coordinate the existing ORM/session, `DifyAppClient`, cache lock and SSE seams, but do not create a new generic service layer.
- `conversations.py` owns commercial replacement/persistence; `context.py` owns model-safe local context; the summary worker only advances a safe summary cursor.
- Tool routes use the existing tool-conversation mapping and `DifyAppClient`; the client encapsulates controlled HTTP calls but does not decide local permission or business state.
- Chatflows consume typed Start inputs and `sys.query`; they do not access PostgreSQL or Redis.

### Forbidden

- No Streamlit/UI direct SQL or Dify calls, and no Dify business-database mutation.
- No new generic conversation/message service, version table, repository/DI abstraction or local tool正文/feedback persistence.
- No secrets, raw source正文, source filenames, private provenance, query, inputs or real user IDs in Dify business metadata, logs, reports or operation events.

## 6. Boundaries
Only describe boundaries that actually exist.
- UI/transport: FastAPI exposes internal cleaning upload/inspection, tool-examples CRUD, business/tool message APIs and Streamlit test/admin UI over HTTP; no public internet API or new external service is introduced.
- application/orchestration: the existing FastAPI/Celery cleaning pipeline remains responsible for parsing, metadata projection, publication and compensation; business local context/summary and tool session mapping stay within the existing application modules.
- domain policy: activity parsing, source-status normalization, business status mapping, metadata allow-list and recent-activity formatting remain feature-owned policies.
- persistence/integration: existing cleaning rows, `BusinessMessage` history, temporary `ToolConversation`/`ToolMessage` session state, Dify Knowledge/Chatflow APIs and `AdminSetting` JSON are the established seams. Tool正文 and feedback are not copied to local storage.
- cross-module communication: `CleanedDocument` metadata and publication records cross the cleaning pipeline; tool Chatflows receive typed Start inputs and `sys.query`; business explicitly injects `conversation_summary`/`recent_history_text`/`profile_json`; tool regeneration retains the same opaque Dify `conversation_id` with Memory window 6.

## 7. Shared Code Policy
- Stable shared capabilities: `CleanedDocument`, strategy registry, Markdown/DOCX loading, chunking, Dify metadata projection/publication compensation, internal auth, Dify client, SSE emitter, cache locks, style settings and tool-conversation mapping.
- Ownership: existing cleaning/publishing modules retain cleaning ownership; business replacement stays in `conversations.py`; tool message operations stay in `tools.py`; remote HTTP payloads stay in `dify.py`.
- What must remain feature-owned: event field labels, status mapping, Qwen enrichment schema, registration-link policy, recent-activity formatting, commercial strict replacement and tool same-conversation replay.
- Rules preventing common/shared/utils dumping grounds: extract only behavior reused by at least two stable strategies or entries; commercial strict replacement and tool replay have different semantics, so remain local.

## 8. Public Contracts
- APIs/SDK/events: existing admin cleaning upload/inspection and strategy discovery contracts remain; internal tool-examples user/admin CRUD remains additive. This Feature adds `/internal/v1/business/messages/{message_id}/regenerate`, `/internal/v1/tools/{kind}/messages/{message_id}/feedback` and `/internal/v1/tools/{kind}/messages/{message_id}/regenerate`, while reusing the existing stop routes with terminal metadata.
- Compatibility/versioning: existing strategies, metadata and `/internal/v1` authentication remain unchanged. Event metadata remains additive and namespaced by `record_type=event`; new `can_regenerate`, feature flags and SSE `task_id/message_id/conversation_id/status` fields are ignorable by old clients.

## 9. Data / Consistency
- Persistence ownership: existing cleaning tables and Dify document IDs remain the source for cleaning/publication state. `BusinessMessage` is the commercial replaceable-answer row; tools retain session/task state without writing `ToolMessage.content`.
- Transaction boundaries: local cleaning state commits before/around external publication according to the existing compensating workflow, and Dify metadata is verified before success. Commercial regeneration commits invalidation in a conversation lock before streaming replacement; tool calls use a kind/user lock and Redis task mapping.
- Migration rules: existing tool-session tables were introduced only through Alembic; tool-example settings use a kind-scoped `AdminSetting` key and existing Dify metadata updates stay idempotent with type conflicts failing closed. `advisor-message-actions` introduces no database migration.
- Tool-examples configuration remains transactionally stored in `AdminSetting` under a kind-scoped key; moments/writing use advanced-chat DSLs with typed Start variables and Dify `conversation_id`.
- Concurrency/idempotency: one activity document per cleaned output; same-name updates replace the prior Dify document through existing publication replacement and source deletion remains explicit. Separately, one commercial/tool conversation may generate, regenerate or reset at a time; stop only accepts its bound anonymous user/entry/task mapping.
- Commercial regeneration strictly replaces one local assistant row while excluding its paired user/assistant from context. Tool regeneration rereads and resends within the same Dify conversation; summary retains the latest complete pair for one additional turn. Redis owns task mappings, user-bound stopping/stopped markers and locks.

## 10. Errors / Observability / Sensitive Data

- Unknown mandatory facts skip only that activity; a model failure does not block later activities in the same file.
- Dify publication errors retain retry/compensation state and are recorded in the dated test report.
- Logs and reports contain counts, codes and hashes only; never API keys, raw activity正文 or protected identifiers.
- Tool SSE exposes `task_started` and terminal `conversation_id/message_id/task_id/status`; tool Memory targets window 6 while the commercial primary LLM remains Memory-off.

## 11. Test Architecture
- Unit seam: activity parser, status/metadata projection, Chatflow evidence formatter, tool-examples catalog/session services, context exclusion, commercial replacement, summary pair selection, Dify `/messages` validation, tool stop markers and input allow-lists.
- Integration seam: existing cleaning workers with mocked Dify Knowledge API and strategy discovery/upload contracts; FastAPI auth/CRUD/message routes with mocked Dify Chatflow SSE/REST cover normal, cross-user, expired-session, concurrent and terminal contracts.
- Contract/system/e2e: DSL static Memory contracts, isolated jx Dify import/publish/run and then complete wheel backup/deploy verification of real Dify SSE, Streamlit and the actual mini-program backend host when reachable.
- Test data: synthetic status/link and anonymous conversation/message/task cases; `Other/活动.md` only in isolated validation. All test records are cleaned by exact IDs; no name-regex or whole-Redis cleanup.

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
Point to the relevant established module for the change domain.

- `ai_management/cleaning.py` strategy registry and `CleanedDocument` contract; `ai_management/workers/tasks/cleaning.py` plus `model_cleaning.py` metadata projection/publication compensation.
- `Other/event_rag_cleaner/clean_events.py` event parsing and realtime Qwen contract.
- `ai_management/api/routers/tools.py`, `ai_management/api/routers/admin_management.py` and `ai_management/services/tool_examples.py` for internal API/auth and kind-scoped `AdminSetting` catalog ownership.
- `ai_management/conversations.py`, `ai_management/context.py`, `ai_management/api/routers/business.py` and `ai_management/dify.py` for commercial replacement, Dify error mapping and streaming behavior.

## 15. Architecture Decisions / ADR links

- Feature contracts: `docs/features/advisor-message-actions/SPEC.md` and `IMPLEMENTATION.md`; `docs/features/tool-examples/SPEC.md` and `IMPLEMENTATION.md`; `docs/features/recent-activity/SPEC.md` and `IMPLEMENTATION.md`.
