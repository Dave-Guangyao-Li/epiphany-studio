# Development Log

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

Begin M2.1 with `Source` / `SourceSegment` persistence and plain-text import.
Keep M2.1 and M2.2 on the Fake Provider until source, citation, and fan-out/fan-in
contracts are stable; only then add the real OpenAI Provider in M2.3.

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
