# Development Log

## 2026-07-28

### M2.3b-2a bounded live-smoke harness

- Added an explicit DeepSeek live-smoke module whose default behavior is a
  zero-network dry run.
- Fixed the live boundary to short synthetic material, `deepseek-v4-flash`,
  two calls, one attempt per Task, one in-flight request, 800 output tokens per
  call, and a longer Worker deadline.
- Applied the normal Alembic migrations to a dedicated ignored
  `data/deepseek-live-smoke.db` before execution so the real call trace remains
  inspectable without touching the normal development database.
- Kept programmatic Alembic logging from disabling existing application
  loggers; the full suite exposed and now guards this cross-tool interaction.
- Limited terminal output to Run/Task/ModelCall IDs, status, tokens, latency,
  estimated cost, artifact kinds, and stable error codes.
- Added safety tests proving dry-run does not create a database or enable
  network access, the preflight cannot accept or print a key value, the
  dedicated database reaches the current Alembic head, the workflow is bounded
  to two calls, a first failure cancels the second call before Provider entry,
  and the summary excludes source and Artifact content.
- Verified the dry-run locally. No API key was present, so no external request
  or paid usage occurred in this slice.

### M2.3b-1 DeepSeek Provider, zero-network validation

- Added a direct OpenAI-compatible DeepSeek V4 adapter using runtime `httpx`.
- Added separate Timeline and Theme prompts that treat source text as untrusted
  data and require source-grounded JSON.
- Added explicit Fake/DeepSeek configuration with a redacted `SecretStr` API
  key; Fake remains the default.
- Added HTTP, authentication, balance, rate-limit, server, overload, network,
  timeout, finish-reason, protocol, model, and usage error handling.
- Kept retry ownership in the Worker so every HTTP request has its own durable
  Task attempt and `ModelCall`.
- Corrected HTTP client timeouts to persist as `timed_out`.
- Preserved tokens and estimated cost for paid HTTP 200 responses whose content
  is truncated, filtered, overloaded, or otherwise unusable.
- Restricted the first adapter to the official HTTPS host and added source
  character and output Token bounds.
- Added Provider/model/call/usage fields to JSON operational logs without
  logging source content, prompts, model responses, error bodies, or keys.
- Added MockTransport Provider unit tests and full dual-Researcher runtime
  integration tests. No live request or API cost was used in this slice.
- No migration was needed; the existing `model_calls` schema remains sufficient.
- Ruff lint/format, all 68 tests, Alembic current/check, and diff whitespace
  validation pass.

## 2026-07-27

### M2.3a zero-network model call trace

- Added durable `model_calls` with one record per Task attempt.
- Moved `model_call_count` to call reservation so failed and timed-out attempts
  are counted instead of only successful Tasks.
- Added provider/model, input/output tokens, latency, estimated cost, currency,
  status, and error code to the Run trace.
- Added an atomic single-process budget boundary before Provider execution,
  controlled by `EPIPHANY_MODEL_MAX_CALLS_PER_RUN`.
- Added retry and timeout accounting plus abandoned-call handling during lease
  recovery.
- Kept source text, prompts, model responses, and API keys out of Events and
  operational logs.
- Added Alembic revision `0003_model_call_trace`.
- Added Fake Provider tests for successful usage, retries, timeouts, and call
  limit rejection before an extra invocation.
- Ruff checks, formatting, all 32 tests, Alembic upgrade/check, and migration
  downgrade/upgrade pass without any network or paid model call.
- Split M2.3 into M2.3a accounting infrastructure and M2.3b live DeepSeek
  integration so the first paid smoke test has an existing budget and trace.

## 2026-07-24

### Learning practice guide

- Added a chapter-based `docs/learning/` guide so the project remains useful as
  a learning record rather than only an AI-authored codebase.
- Separated the guide into an index, beginner glossary, local
  run/test/debugging guide, milestone chapters, and a reusable entry template
  to avoid one oversized document.
- Backfilled M0, M1, observability, M2.1, and M2.2 with plain-language
  motivation, analogies, module maps, technical concepts, tests, manual
  verification, debugging paths, limitations, and commit references.
- Added fan-out/fan-in explanations that distinguish task branching from actual
  concurrency and deterministic result merging from an additional model call.
- Updated repository guidance so every future implementation slice must update
  its learning chapter in the same commit.

## 2026-07-23

### Project direction

- Working name: **Epiphany Studio**.
- The product begins as a personal tool for journals, podcast material, and
  reflective writing.
- The engineering goal is to learn production-minded agent orchestration,
  backend state, reliability, and deployment through a real workflow.

### Decisions

- Start local-first and single-user.
- Use a fixed, one-level parent/child workflow.
- Run at most two read-only child tasks in parallel.
- Use Python, FastAPI, SQLite, the official OpenAI SDK, an in-process durable
  worker loop, and SSE.
- Do not introduce LangGraph, Temporal, Redis, PostgreSQL, a vector database,
  or containerized agents in the MVP.
- Keep API/model access behind a provider interface.
- ChatGPT subscription credits are not treated as application API credits.
- Personal source material, local databases, recordings, and API keys stay out
  of Git.
- Prioritize the documented MVP sequence over an intermediate UI or platform
  detour. The product UI remains M5.
- Every verified slice updates its affected documentation in the same focused
  commit before the next slice starts.
- Durable database Events are the product execution trace. Structured JSON
  stdout logs are the operational trace, correlated by request, Run, Task, and
  attempt IDs without logging personal source content.

### Next

Begin M2.3 with the official OpenAI Provider behind the existing Task contract.
Keep the Fake Provider as the deterministic orchestration test double. Do not
change fan-out/fan-in semantics while adding model calls; add token, latency,
call-limit, and cost records before using personal source material.

### M1 implementation

- Added a Python 3.12 FastAPI backend with SQLite in WAL mode.
- Added persistent `runs`, `tasks`, `events`, and `artifacts` plus an Alembic
  initial migration.
- Added explicit Run/Task transition validation.
- Added a deterministic three-step Fake Workflow behind a provider interface.
- Added a database-backed Worker with leases, bounded retry, idempotent Artifact
  commits, cancellation fencing, and expired-lease recovery.
- Added HTTP endpoints for create/read/cancel and event replay.
- Added tests for state transitions, end-to-end persistence, restart, retry,
  cancellation, recovery, fencing, and API behavior.
- First external verification exposed a missing SQLAlchemy async extra
  (`greenlet`); the dependency declaration was corrected to
  `sqlalchemy[asyncio]`.
- Alembic upgrade, Ruff checks, formatting checks, and all 11 tests pass.
- An end-to-end FastAPI lifespan demo completed all three Tasks, persisted three
  Artifacts and twelve Events, and ended with `run.succeeded`.
- A fresh application instance queried the same completed Run from SQLite,
  verifying the M1 restart requirement.

### Observability baseline

- Added JSON stdout logging without a new logging framework.
- Added `X-Request-ID` generation, propagation, and response headers.
- Added stable events for HTTP completion/failure, Run creation/cancellation,
  database readiness, and Worker start/claim/complete/retry/fail/recover/stop.
- Kept private inputs and generated content out of operational logs.
- Added formatter/context and API correlation tests; all 12 tests pass.
- Manually traced one request from `req_manual_demo` through Run creation and
  all three Worker Tasks.

### M2.1 source contract

- Added Alembic revision `0002_source_contract` for local `sources` and
  `source_segments`.
- Added deterministic Unicode/newline normalization, paragraph segmentation,
  bounded long-segment splitting, exact character offsets, and content hashes.
- Added stable Source/Segment IDs and unique constraints for idempotent retries.
- Added concurrent-import conflict recovery.
- Added `POST /sources`, `GET /sources`, and `GET /sources/{source_id}`.
- Added strict `SourceReference` with only `source_id` and
  `source_segment_id`.
- Added `source.imported` and `source.import.deduplicated` logs containing
  identifiers and counts but no source text.
- Found and repaired a development schema drift: application `create_all()` had
  created the new tables before Alembic advanced. Normal startup now leaves
  schema changes exclusively to Alembic; `create_all()` is test-only.
- Migration upgrade/downgrade, `alembic check`, Ruff, formatting, and all 21
  tests pass.
- Manual API verification imported two ordered segments, returned the same IDs
  on a duplicate import, and queried them from a fresh application instance.

### M2.2 fake parallel research agents

- Added an `episode-research` Workflow with a durable Manager and two sibling
  Child Tasks: Timeline Researcher and Theme Researcher.
- Added strict Pydantic outputs with required source references, bounded
  confidence, forbidden extra fields, and exact quote-to-segment validation.
- Added a deterministic Fake Provider fixture for both research roles.
- Added bounded Worker batches with a maximum of two concurrent Provider calls.
- Added deterministic fan-in that waits for both children and creates one
  idempotent `episode_research_bundle`.
- Added child-to-parent failure propagation, sibling cancellation, lease
  clearing, and stale-result fencing.
- Added stable fan-out/fan-in Events and operational batch/failure/stale-result
  logs without source or generated content.
- Added unit, concurrency-probe, failure-injection, and HTTP integration tests.
- M2.2 changes no database tables; Alembic remains at `0002_source_contract`.
- Ruff, formatting, and all 28 tests pass. The HTTP integration path imports a
  Source, starts the Run, executes both children, and returns three Artifacts.
