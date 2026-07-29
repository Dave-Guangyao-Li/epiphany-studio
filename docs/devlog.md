# Development Log

## 2026-07-29

### M3.4 Draft Quality Report and independent feedback

- Added workflow v6 for Creative Brief Runs with Draft Quality enabled by
  default. Explicit `draft_quality.enabled=false` keeps the previous v5 path,
  so callers can opt out of the fifth model call and persisted historical Runs
  retain their original semantics.
- Added deterministic post-Editor metrics for configured/estimated duration,
  paragraph citation coverage, Source/Segment diversity, exact paragraph
  duplication, repeated eight-character windows, must-include and avoid
  patterns, fixed filler phrases, template phrases, and repeated
  "not ... but ..." constructions.
- Kept the duration claim narrow: it is derived from non-whitespace Draft
  characters and the configured characters-per-minute rate. It is not measured
  audio duration; real recording time can be stored separately in user
  feedback.
- Added one serial `review_podcast_draft` root Task with six fixed dimensions.
  Every assessable result requires a score, Draft location, and verbatim quote.
  Code verifies that the quote belongs to that location and that every
  SourceReference stays within the Draft block's allowed evidence.
- Kept the final decision outside the model. Deterministic blockers cannot be
  overwritten by the Reviewer. The versioned experimental aggregate is 60%
  deterministic and 40% model score, is omitted when any model dimension is
  unassessable, and always requires human review.
- Explicitly labels same-model Reviewer output as self-review/advisory. The
  report exposes observable repetition and style signals instead of claiming
  an "AI-written probability."
- Added graceful degradation. A permanent Reviewer, authentication, or
  model-budget failure preserves and exports the valid Editor Draft. An
  existing deterministic blocker remains `blocked`; otherwise the report is
  `automated_review_incomplete`. Retry, lease recovery, cancellation,
  idempotency, fencing, and ModelCall accounting continue through the Reviewer
  step.
- Added JSON and Markdown quality-report endpoints plus append-only user
  feedback endpoints. Human feedback and synthetic E2E feedback are stored
  separately; the service calculates `human_signal_eligible`, and the E2E uses
  `synthetic_test`, which cannot count as real-user approval. In the current
  unauthenticated MVP, the origin is caller-declared rather than verified.
- Kept feedback comments, Source text, Draft prose, prompts, and model output
  out of Events and structured logs. Stable events record only IDs, decisions,
  counts, ratings, and error codes needed for diagnosis.
- Reused the existing Run, Task, Artifact, Event, and ModelCall schema. No
  migration is required.
- Recorded the 2026-07-29 validation snapshot: Ruff check passed, Ruff format
  check passed for 71 files, all 205 pytest cases passed, Alembic upgrade plus
  `alembic check` reported no new upgrade operations, and both the M3.3 Fake
  regression E2E and M3.4 Fake E2E passed.
- Completed the bounded 2026-07-29 DeepSeek v6 E2E as
  `run_276a3bce22394eb8a56edd6af8760012`. All five calls and six Tasks
  succeeded. The Run produced 11 workflow Artifacts and 12 after an idempotent
  `synthetic_test` feedback submission.
- Recorded 26,618 input tokens, 11,239 output tokens, 61,669 ms combined
  Provider duration, and local estimated cost CNY 0.049096. All three App
  lifespans/checkpoints, the durable queued Reviewer, supplemental references,
  feedback replay, and 85-line JSONL log redaction checks passed.
- The successful report was honestly `revision_recommended`: deterministic 72
  for 1,429 non-whitespace characters / estimated 5.1 minutes against a
  10-minute target. It still had 100% citation coverage, 4 Sources /
  10 Segments, no exact duplicate paragraph, one filler hit, and four
  "not ... but ..." hits.
- The same-model Reviewer scored all six dimensions 5/5 and produced an
  experimental 83.2. This divergence validates the architecture boundary:
  self-review remains advisory, while code-owned duration findings continue
  to recommend more material rather than padded prose.
- Preserved the failed paid attempt before the successful Run. Editor correctly
  rejected it with `podcast_draft_missing_supplemental_source_reference`; the
  output-tail reference self-check was strengthened before retrying. Its local
  estimated CNY 0.039696 is separate debugging cost and is not included in the
  successful Run estimate.
- Local cost estimates are not provider invoices. Official dashboard values may
  differ because of pricing rules, cache treatment, and reporting delay.

### M3.3 Creative Brief and deterministic material readiness

- Added a strict `CreativeBrief` for episode intent instead of leaving
  duration and audience only inside an Editor Prompt. The first contract
  supports 10, 15, or 30 minutes, an adjustable characters-per-minute estimate,
  scenario, audience, communication goal, tone, required content, and patterns
  to avoid.
- Added a deterministic `MaterialReadinessReport`. It counts unique
  non-whitespace Source characters, initial and supplemental Sources,
  SourceSegments, source diversity, and duplicate/overlapping segments. It
  reports the configured target range, estimated supported range, missing
  character count, stable gap codes, and bounded follow-up questions.
- Kept Source text out of the persistable readiness report. The calculator may
  read SourceSegments in memory, but the report contains only aggregate counts,
  limitations, and SourceReferences attached to follow-up questions.
- Added workflow v5 and a second durable checkpoint:
  `waiting_for_user / awaiting_more_material`. Obvious shortage is a normal,
  recoverable product state, not a Provider failure. The workflow does not
  queue Editor or spend its model call until another idempotent Source
  submission passes the same deterministic gate.
- Chose a transparent v1 estimate of 280 non-whitespace characters per minute
  with a 15% tolerance. Both values are explicit assumptions and must not be
  described as measured recording speed or a semantic content-quality score.
- Added cumulative multi-round supplemental Source handling. An insufficient
  submission is persisted and returns to the same checkpoint; later rounds
  include all accepted material. Replaying one submission is idempotent, and
  only the first ready round queues the single Editor.
- Restored minimum disclosure for v5: readiness and Editor receive only the
  initial SourceSegments actually referenced by the validated Scaffold, not
  every paragraph from every initial Source. Duplicate initial/prior Sources
  are rejected before persistence.
- Deduplicate evidence both by stable SourceSegment reference and by normalized
  text content, so copied text under another Source cannot inflate evidence
  volume or source diversity.
- Made the cumulative supplemental boundary explicit and atomic: 500 segments
  are accepted, the 501st is rejected without a submission Artifact, Event, or
  Run-state change. Removed the hidden 20-round submission-history limit.
- Removed the obsolete 4,000-token Prompt constraint and raised the default
  DeepSeek Editor output ceiling from 6,000 to 20,000 tokens so a 30-minute
  Brief is not internally contradictory. Billing remains based on actual
  returned tokens.
- Added a privacy-safe synthetic E2E that imports three initial Sources,
  reaches the checkpoint, fully restarts the App against the same SQLite
  database, imports a synthetic supplemental transcript, replays Resume, and
  completes Editor plus all Markdown exports. The raw initial Sources contain
  2,106 non-whitespace characters, but the Scaffold-grounded minimum-disclosure
  set contains 488 against a 2,380 lower bound; after a 2,215-character
  supplement it measures 2,703 and becomes ready.
- The accepted E2E shape is 4 Tasks / 5 Artifacts / 3 ModelCalls while waiting
  and 5 / 8 / 4 after Editor. Every check passed with Fake Provider, zero
  tokens and zero cost. The test also verifies structured-log redaction and no
  task, event, model-call, or cost changes across restart.
- Synthetic fixtures verify engineering behavior; they are not counted as real
  user validation.
- Deferred draft scoring and model self-review to M3.4. A future evaluator must
  use strict evidence-bearing output, remain separate from deterministic
  blockers, and never present Mock feedback or a same-model self-review as
  human approval.
- This slice uses Run input plus Artifacts and requires no database migration.
  All 178 tests, Ruff lint/format, Alembic upgrade/check, diff whitespace
  validation, and the independent Fake E2E pass. A second paid DeepSeek run
  was intentionally deferred until M3.4 can validate generation and self-review
  together.

### M3.2 grounded Editor and final Markdown

- Versioned new `episode-research` Runs as v4 while preserving persisted v1,
  v2, and v3 completion semantics. The existing four-Task research and
  Interviewer flow still pauses at
  `waiting_for_user / awaiting_interview_response`.
- Changed v4 Resume from deterministic completion to durable continuation:
  it commits the source-reference-only `user_material_submission`, returns the
  Run to `running`, and idempotently queues one serial root
  `build_podcast_draft` Editor Task.
- Added a strict Editor input/output contract. The bounded input contains the
  validated Scaffold, initial SourceSegments referenced by it, and the new
  supplemental SourceSegments. Podcast Script must cite both initial and
  supplemental evidence; Show Notes must cite supplemental evidence.
- Added an injection-resistant Editor Prompt plus separate
  `EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS` and
  `EPIPHANY_DEEPSEEK_EDITOR_MAX_TOKENS` limits.
- Extended Fake and DeepSeek Providers through the same Editor validation
  boundary. Fake output is deterministic, readable, grounded in fixture text,
  zero-token, and zero-cost.
- Added deterministic, escaped Podcast Draft and Show Notes renderers plus
  `GET /runs/{run_id}/exports/podcast-draft.md` and
  `GET /runs/{run_id}/exports/show-notes.md`. The Scaffold remains exportable
  after the final Run output changes to `build_podcast_draft_result`.
- Extended retry, startup recovery, cancellation, late-result fencing,
  ModelCall accounting, call-budget rejection, and Resume idempotency to the
  Editor. A normal final v4 Run has 5 Tasks, 6 Artifacts, and 4 ModelCalls.
- Validate the complete Editor input before Resume persists any submission or
  changes Run state. Overlapping initial/supplemental Sources and oversized
  supplemental bundles now return 409 while the Run remains recoverably
  `waiting_for_user`, without creating a paid attempt.
- Reject any visible `src_...` or `seg_...` pattern in final Markdown, including
  unknown IDs and Markdown-escaped variants. Concrete Provider exports are
  lazy-loaded so Editor prompt modules also import correctly in a clean process.
- Extended the guarded E2E through Editor completion and all three Markdown
  files. Fake E2E passed all state, count, event-order, citation, supplemental
  evidence, idempotency, stable-Scaffold, and log-redaction checks.
- Completed an explicit synthetic `deepseek-v4-flash` E2E with Run
  `run_88d16bf3e03f45a98edfea2c164e383a`: 4 calls succeeded, using 16,667 input
  tokens, 9,468 output tokens, and 73,018 ms combined Provider duration, with
  estimated cost CNY 0.035603. Events grew from 26 at the checkpoint to 36
  after Editor completion. The estimate is not a provider invoice, and the
  generated candidate content still requires human review.
- Reused the current schema with no migration. All 151 tests, Ruff
  lint/format, Alembic upgrade/current/check, and the focused E2E checks pass.

## 2026-07-28

### M3.1 realistic Fake + DeepSeek E2E acceptance

- Replaced the short/filler fixture with three coherent initial Sources and one
  complete supplemental voice-note transcript. Tests now enforce minimum
  lengths, paragraph counts, roles, uniqueness, and privacy markers.
- Made the deterministic Fake Provider extract topic-relevant sentences,
  dates, themes, and verbatim quotes from the fixture. It remains offline,
  repeatable, zero-token, and zero-cost, but its exported Scaffold is now
  readable enough for human regression review.
- Changed Markdown citations from raw `src_...#seg_...` strings to stable
  `[S1]` labels plus a Source-title/segment-position index. Raw IDs remain in
  structured Artifacts and SQLite; missing citation metadata fails export.
- Forwarded the Run topic into both Researchers and treated topic and Source
  text as untrusted data. Added a prompt rule that plans, drafts, wishes, and
  attempts cannot be rewritten as already completed or published facts.
- The first full-material DeepSeek Run completed both Researchers and fan-in
  but rejected the Interviewer before network entry with
  `provider_input_too_large`. It exposed that the validated merged research
  Bundle had incorrectly reused the raw-source 8,000-character limit.
- Split the boundaries into 8,000 characters for Researcher source input and
  24,000 for the Interviewer research Bundle, while retaining a bounded
  three-call, one-attempt, one-concurrent-request live harness.
- The corrected DeepSeek Run
  `run_44c9db75a74744ac940efd2d27172107` passed the complete
  `waiting_for_user -> Resume -> succeeded` journey with 4 Sources, 21
  Segments, 4 successful Tasks, 5 final Artifacts, 3 ModelCalls, and 29 Events.
  It used 10,046 input and 6,670 output tokens, 52,003 ms combined Provider
  time, and an estimated CNY 0.023386.
- The failed diagnostic Run used an estimated CNY 0.012172, so the two new
  realistic attempts total an estimated CNY 0.035558. These are local price
  table estimates, not provider billing guarantees.
- Human content review found concrete, multi-source interview questions and
  readable citations, plus one unsupported tense escalation from “planning an
  Episode 0” to “already published.” The original evidence remains untouched;
  the prompt was tightened, while semantic entailment remains a human-review
  or future verifier concern.
- All 130 tests pass. Full Ruff, Alembic, diff, and secret checks are completed
  before the focused commit. The detailed evidence and debugging flow live in
  `docs/learning/m3-1-realistic-e2e.zh-CN.md`.

### M3.1 guarded backend/API E2E harness and earlier probes

- Added a committed, privacy-safe Chinese fixture with three initial Sources
  and one supplemental voice-note transcript.
- Added a CLI that drives the real FastAPI lifespan, Worker, HTTP API,
  Orchestrator, SQLite stores, Markdown export, Resume, and idempotent replay.
  Dry-run never creates runtime files; Fake execution is deterministic and
  free; live DeepSeek execution requires an explicit flag and is limited to
  three calls, one attempt, and one concurrent request.
- Wrote ignored evidence to a dedicated database plus structured JSONL,
  machine-readable report, and Interview Scaffold Markdown.
- Refused databases containing queued/running work before starting the Worker,
  preventing a reused live database from causing undeclared extra model calls.
- Added safe Task error codes, log error-code aggregation, request-ID and
  Markdown-header assertions, provider/model/currency checks, stronger
  redaction checks, and forced INFO acceptance evidence.
- The Fake journey passed at 4 Tasks / 4 Artifacts / 3 ModelCalls while waiting
  and 4 / 5 / 3 after Resume; replay was idempotent and cost stayed zero.
- Three isolated DeepSeek attempts were bounded and stopped safely: two
  reached a truncated Interviewer response and one hit a provider network
  error. Their combined local CNY price-table estimate is `¥0.035096`; a
  complete live E2E had not yet passed at that earlier checkpoint. The
  realistic acceptance section above records the later successful Run.
- Tightened the live Interviewer prompt to request a concise three-section
  scaffold within the output budget.
- At that earlier checkpoint, all 120 tests, Ruff lint/format, Alembic
  current/check, and `git diff --check` passed. M3.1 exported only a Scaffold;
  the later M3.2 slice added the Editor, podcast draft, and Show Notes.

### M3.1 durable human checkpoint and idempotent Resume

- Versioned new `episode-research` Runs as v3 while preserving in-flight v1
  completion at the research Bundle and v2 completion at the Interview
  Scaffold.
- Changed v3 so the completed Interviewer leaves the Run durably at
  `waiting_for_user / awaiting_interview_response`, with all four Tasks
  terminal, four Artifacts persisted, three ModelCalls completed, and no work
  left for the Worker to poll.
- Allowed the validated Interview Scaffold Markdown to be exported while the
  Run is waiting, so the human can read the questions before supplying more
  material.
- Reused `POST /sources` for already-transcribed supplemental speech and added
  `POST /runs/{run_id}/resume`, whose strict request contains a checkpoint,
  caller-stable submission ID, and one or more Source IDs.
- Persisted a `user_material_submission` Artifact containing only the
  checkpoint, Scaffold ID, Source IDs, and SourceSegment references. Source
  text remains in `sources` / `source_segments` and is not copied into Events
  or operational logs.
- Made identical Resume retries return an idempotent replay without duplicate
  Artifacts or Events; conflicting material, missing Sources, wrong states,
  and invalid bodies return bounded HTTP 409/404/422 responses.
- Serialized Resume and Cancel through one single-process mutation boundary.
  This fixed a review-discovered race where both terminal actions could
  previously succeed. Regression tests now prove that only one terminal event
  wins.
- Kept M3.1 intentionally deterministic after Resume: it records the human
  submission and completes the checkpoint without another Task, Provider call,
  Token, or cost. At that stage `output_artifact_id` still pointed to the
  Scaffold; the later M3.2 Editor consumed the new Source and created the draft.
- Reused the existing runtime schema, so no migration is needed. The full 113
  test suite, Ruff lint/format, Alembic current/check, and guarded DeepSeek
  dry-run pass; no live API request or paid usage was made.
- Recorded the remaining deployment boundary: concurrent Resume across
  multiple processes is protected from duplicate rows by SQLite's unique
  constraint, but the losing request is not yet translated to replay/409.
  The supported M3.1 runtime remains local and single-process.
- Audio capture, microphone permission, speech-to-text, TTS, and voice cloning
  remain outside this slice. “Voice note transcript” means text supplied after
  transcription.

### M2.4 source-grounded interview scaffold and Markdown export

- Extended `episode-research` after its parallel Timeline/Theme fan-out and
  deterministic fan-in with one serial root `build_interview_scaffold` Task;
  the Interviewer has no parent and is not a third parallel research child.
- Added strict scaffold input/output models, an injection-resistant prompt
  limited to the validated research bundle and its allowed source references,
  and Worker-boundary schema, topic, and citation validation.
- Added deterministic Markdown rendering that retains source labels, strips
  runtime-only execution metadata, and escapes model-produced Markdown control
  syntax and raw HTML.
- Added `GET /runs/{run_id}/exports/interview-scaffold.md`, including readiness,
  output-kind, and final schema checks before returning a UTF-8 attachment.
- A successful v2 run now has four Tasks, four Artifacts, and three ModelCalls:
  Manager, two Researcher children, and the serial Interviewer.
- Proved that a two-call Run budget rejects the third call before Provider
  entry and before a third ModelCall is reserved. Scaffold failures retain the
  two successful research Artifacts and deterministic fan-in Bundle.
- Versioned new `episode-research` runs as v2 while preserving in-flight v1
  behavior, which still completes at the research Bundle without a topic or
  Interviewer.
- Reused the existing Run/Task/Artifact/Event/ModelCall schema, so no database
  migration is needed.
- Added structured scaffold/export observability containing only correlation
  IDs, statuses, and counts; no source text, prompt, model response, or exported
  Markdown is logged.
- Ruff lint and format checks, all 99 tests, Alembic current/check, and the
  guarded DeepSeek dry-run pass. No new paid M2.4 live call was made; the
  earlier M2.3b two-call, 2,301-token verification below remains the historical
  live record.

### M2.3b explicit DeepSeek billing currency

- Added `EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY|USD`, with `USD` as the
  backward-compatible default.
- Kept billing currency explicit because DeepSeek model responses report usage
  but do not identify the account's settlement currency; no locale, API-key, or
  balance-based guessing is performed.
- Allowed the current local CNY-billed account to opt into the official CNY
  price schedule while USD-billed accounts retain the existing USD schedule.
- Changed live-smoke cost totals to remain grouped by currency rather than
  adding unlike amounts.
- Persisted the configured currency when a ModelCall is reserved, so failures
  without usage data do not silently fall back to USD.
- Reused the existing `estimated_cost_micros` and `cost_currency` columns, so no
  database migration is needed and historical USD ModelCalls remain unchanged.
- Added zero-network coverage for the compatibility default, explicit CNY
  accounting, invalid configuration, grouped summaries, and CNY failure and
  timeout traces.

### M2.3b persistent data guide and provider reconciliation

- Added a dedicated beginner-readable SQLite guide instead of continuing to
  grow the general local-development chapter.
- Documented the separate roles of the normal development database, the
  DeepSeek smoke database, and SQLite WAL/SHM sidecars.
- Recorded the purpose of each runtime table, safe read-only inspection
  commands, privacy-sensitive fields, and backup cautions.
- Reconciled the two persisted ModelCalls and 2,301 local Tokens with the
  DeepSeek Dashboard, and clarified the boundary between local estimates and
  provider billing.
- Recorded the invariant that costs in different currencies must be grouped,
  not directly summed or silently rewritten.

### M2.3b-2b first bounded live DeepSeek verification

- Executed the guarded smoke command with its built-in synthetic source and
  dedicated ignored SQLite trace database.
- Completed Timeline and Theme calls with `deepseek-v4-flash`; both succeeded
  on attempt 1 without retry, timeout, or an error code.
- Passed strict response schema, source-reference, verbatim-quote, and
  deterministic fan-in validation, producing the three expected Artifacts.
- Persisted two ModelCalls and the full Run/Task/Event trace under
  `run_e8ad6452087c479cb84293ae3919201d`.
- Recorded 1,092 input tokens, 1,209 output tokens, 15,435 ms combined Provider
  latency, and USD 0.000491 estimated cost.
- Independently queried the SQLite trace and confirmed Run success, three
  successful Tasks, two completed ModelCalls, and one completed fan-in.
- The captured output contained only key presence and sanitized metadata; it
  did not expose the API key, source text, Prompt, or generated content.

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
