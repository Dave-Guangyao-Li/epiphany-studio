# Development Log

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
