# Development Log

## 2026-08-04

### M5.1c Obsession/Bear realistic browser E2E

- Ran a complete Playwright + React UI + real DeepSeek V4 Pro content journey
  using a copyright-bounded, transformative synthetic Bear persona based on
  public reporting about the 2026 film *Obsession*. Public plot anchors,
  creator interpretation, non-canonical diary material, supplemental answers,
  and a style-only Writing Sample remain separate Sources.
- Persisted one Project with 9 Sources / 82 Segments and 10 Runs. The successful
  lineage moved from a 9.71-minute parent draft through grounded material reuse,
  two draft-aware interview rounds, and one feedback-driven Revision. The final
  candidate contains 4,055 non-whitespace spoken characters, estimates 14.48
  minutes, cites 24/24 paragraphs across 6 Sources / 31 Segments, and has no
  exact or repeated-window duplication.
- Exercised bounded failures rather than deleting them: three overloaded
  preflight Runs, one locally rejected oversized Provider bundle, one two-503
  Revision, one Reviewer budget exhaustion, and strict Reviewer evidence
  failures. The Project ledger contains 31 calls, 373,020 tokens, and estimated
  CNY 1.067259 including failures and retries.
- Added an optional Worker batch cooldown, defaulting to zero, so constrained
  live accounts can space model batches without slowing Fake tests or normal
  deployments. The live experiment used one Worker and a 30-second cooldown;
  the setting and behavior have regression coverage.
- The first full-suite rerun exposed that the untracked live `.env` could leak
  the 30-second cooldown and 80,000-character Editor limit into deterministic
  tests. The test harness now pins Fake Provider, zero cooldown, and the default
  48,000-character boundary per test. A plain `.venv/bin/pytest -q` from the
  backend directory is again deterministic and cannot make a paid call.
- Human review caught a high-severity Bear POV violation that the same-model
  Reviewer missed: an otherwise cited draft narrated Nikki's reaction after
  Bear's death. A selected-feedback Revision removed that omniscient passage.
  Final human review then found a smaller meta-editorial voice leak and three
  remaining parallel-contrast templates, so the system correctly remains at
  `79 / revision_recommended` and `would_record_as_is=false`.
- Recorded two product gaps discovered only through the real journey: the UI
  does not expose `apply_selected_feedback` even after saving feedback, and a
  cancelled Task can still receive a late successful Provider completion and
  incur cost. Both remain explicit follow-up work rather than being hidden by
  the successful final Run.
- Added the reproducible fixture, final readable candidate, machine-readable
  run summary, and the full user/admin experiment report at
  `docs/experiments/m5-1c-obsession-bear-browser-e2e.zh-CN.md`. Local SQLite and
  Playwright traces remain uncommitted debug evidence.
- Validation: 443 backend tests, Ruff lint/format, 43 frontend tests, production
  frontend build, and `git diff --check` all pass.

## 2026-08-03

### M5.1b real-browser DeepSeek E2E and Source Starter hardening

- Used Playwright to exercise the local React Console as a real user instead of
  calling the workflow only through Swagger. The committed data is a fully
  synthetic persona and is reproducible from
  `backend/fixtures/e2e/m5-1b-real-browser/`; API keys, local databases, model
  response bodies, and private material remain uncommitted.
- Hardened Source Starter invalid-output handling into a bounded chain: one
  model repair retry, deterministic `server_line_grounding`, then the
  server-owned safe template only if line grounding cannot pass the same full
  contract. Line grounding retains useful topic-specific lines and replaces
  only unsupported user history, dialogue, or unverified facts with visible
  completion/verification regions. It cannot confirm a Source, and Provider
  failures are not converted into apparent success.
- Restored the original Source Starter `mode` and `intent` from persisted Run
  input after refresh. The browser can now recover the exact pending settings,
  while still warning that unconfirmed local textarea edits are not durable.
- Real `exploration_outline` Run `run_3c637233a17843119f546c1521ec0024`
  passed strict validation in one DeepSeek call and was intentionally abandoned
  without importing evidence. Real `starter_draft` Run
  `run_8eda93938e4b4a1881eb47453bd5073f` reached the confirmation checkpoint via
  line grounding; a simulated user edited it into a 516-character factual
  Source and explicitly confirmed the import.
- Completed a separate three-Run podcast chain through the browser:
  `run_c41c726fdcca4136bd1e317dbcbce21a` (10.11 minutes),
  `run_c344c19e9cb844c29c4daac81434cb00` (12.61 minutes), and
  `run_2fec917404234405b9ec7c2c9ab16802` (14.59 minutes). The final draft had
  26/26 cited paragraphs across 5 Sources / 31 Segments and no exact duplicate
  paragraph. Duration passed the 85% floor, but seven parallel-contrast warnings
  capped the score at 79 and preserved `revision_recommended`.
- Fixed the Run Trace UI so Markdown/export actions are derived from actual
  Artifacts rather than workflow version alone. A final Revision no longer
  requests an unavailable supplemental plan or advertises a missing Scaffold,
  eliminating the observed 409 without hiding real errors.
- The current validation baseline is 442 passing backend tests, Ruff checks,
  43 passing frontend tests, and a production frontend build. Full actions,
  costs, quality evidence, trace files, and residual debts are recorded in
  `docs/experiments/m5-1b-real-browser-e2e.zh-CN.md`.

### M5.1 AI-assisted Source Starter and visible progress

- Added a Project-scoped `source-starter` workflow v1 for the blank-page
  problem. It snapshots the server-owned Project title/description plus the
  source title/type, starter mode, and optional intent, then queues exactly one
  `build_source_starter` Task through the existing durable Worker.
- Reused Run, Task, ModelCall, Artifact, Event, lease, retry, fencing,
  cancellation, recovery, budget, and idempotency infrastructure. No new table,
  queue, orchestration framework, or Alembic migration was added.
- Added a strict `source-starter-candidate.v1` contract with editable starter
  text, 2--8 grounded brainstorming questions, explicit uncertainties, and
  mandatory confirmation/verification safety flags.
- Tightened the model boundary: it cannot invent first-person experiences,
  dates, dialogue, external facts, professional conclusions, or completed
  outcomes. Unknown facts and missing personal detail remain visibly marked
  for verification or user completion; all Project/form context is untrusted
  input.
- Kept candidate and evidence separate. A successful model call persists a
  `source_starter_candidate` Artifact, then durably pauses the Run at
  `waiting_for_user / awaiting_source_confirmation`; it does not create a
  Source. Only an explicit confirmation endpoint imports the edited text.
- Made confirmation one atomic mutation: Source/Segments, ProjectSource,
  server-owned `source_starter_confirmation` Artifact, confirmation/success
  Events, and the Run's `waiting -> succeeded` transition commit together.
  Injected failure before the Event proves the entire mutation rolls back and
  can be retried from the same waiting checkpoint.
- Added semantic confirmation idempotency and lineage. The fingerprint covers
  final title/type/text but excludes transport `submission_id`; replaying the
  same content with a new ID returns the original Source and appends the ID to
  the Artifact's audited `submission_ids`. Changed content after commit returns
  409. The resulting Source records `origin=ai_assisted`,
  `user_confirmed=true`, starter Run/Artifact IDs, and bounded execution
  metadata.
- Limited AI starters to `journal`, `podcast_draft`, and `other`. Both UI and
  backend reject `writing_sample` and `voice_note_transcript`, because generated
  text cannot impersonate a user-owned style sample or an actual voice capture.
- Extended that identity boundary beyond initial Source type. Any confirmed
  `origin=ai_assisted` Source is filtered/rejected as a Writing Sample by the
  Run form, Project Run creation, Editor/Reviewer style hydration, and Revision
  inheritance/addition paths.
- Added a Source-import UI with two starter modes, optional intent, safe append
  and regeneration behavior, follow-up prompts, explicit confirmation, and a
  link to the complete Run Trace. Existing text is never silently overwritten;
  a modified candidate is preserved rather than auto-removed or stacked with a
  regenerated candidate.
- Added GET-only network recovery. Poll/retry reads the same Run and Events and
  cannot create a second paid call. Refresh recovers an unconfirmed Candidate
  Artifact, but explicitly warns that unsaved browser edits are not durable;
  a slow recovery response previews rather than overwrites newly typed text.
- Translated persisted execution into four visible steps: prepare Project
  context, generate, validate/persist candidate, and wait for user edit and
  confirmation. The last step is a real durable Run checkpoint, not a fake
  running Task or timer animation, and it completes only from persisted
  confirmation Artifact/Event evidence.
- Kept the ordinary textarea as the first editing surface. Source text needs no
  visual Scaffold/Draft editor to complete this loop; the richer editor remains
  a separate M5 item.
- Added backend and frontend tests for generation/confirmation boundaries,
  durable waiting, retry/cancel, semantic replay/conflict, atomic rollback,
  forbidden types, AI-assisted style-sample rejection, invalid-output error
  redaction, GET-only recovery, refresh/no-overwrite behavior, edited-candidate
  regeneration blocking, persisted progress evidence, and confirmation gating.
  The later M5.1b regression baseline supersedes this snapshot with 442 passing
  backend tests and 43 passing frontend tests, plus Ruff and the production
  build.
- Completed a Fake real-browser E2E covering refresh recovery, three completed
  automatic steps plus one active confirmation step, atomic Source confirmation,
  all-four-steps completion, and the durable waiting/confirmed/succeeded Event
  chain.
- The first live DeepSeek attempt revealed a speculative first-person claim not
  grounded in input. It was never confirmed or imported. Prompt rules, a
  deterministic post-provider first-person guard, redacted failure behavior,
  and positive/negative regression tests were added.
- Re-ran a bounded live `exploration_outline` with
  `run_2dcf880f20ff4983b7d2eda643d766c5`. One `deepseek-v4-flash` call reached
  `waiting_for_user / awaiting_source_confirmation` with 713 input and 570
  output tokens, 7,009 ms duration, and CNY 0.001853 estimated cost. The DOM
  showed the first three steps complete and confirmation active; logs contained
  no candidate body. The synthetic Run was cancelled without importing a
  Source. M5.1 is complete; visual Scaffold/Draft editing remains separate.
- Final review rejects hash collisions with an existing Writing Sample/plain
  Source instead of overwriting provenance; validates starter text, questions,
  and uncertainties for bounded obvious invention/fact patterns; reconciles
  cancel response loss through GET under the mutation guard; and marks context
  complete as soon as a Run exists.
- Strict live Run `run_dcaeeadc20964a2dbc15568112d87c28` reached the waiting
  checkpoint in one call (886/590 tokens, 7,695 ms, estimated CNY 0.002066).
  Browser stale-cancel simulation reconciled POST conflict through GET, cleared
  the candidate without error, and created no duplicate call. No Source was
  imported and no candidate body or key is recorded.

## 2026-07-31

### M4/M5 local Project workspace and Run Trace Console

- Added durable `Project` and `ProjectSource` records. Sources remain globally
  content-addressed and deduplicated, while the association table groups them
  into one local creative workspace without copying source text.
- Added nullable `Run.project_id` plus Project-scoped `submission_id` and
  request fingerprinting. A same-key/same-payload retry returns the original
  Run with `X-Idempotent-Replay: true`; a same-key/different-payload request
  returns 409. Revision children inherit the parent Project.
- Added Alembic revision `0005_project_workspace` and validated clean upgrade,
  schema check, downgrade to `0004_run_lineage`, and re-upgrade. Existing
  non-Project Runs remain readable because `project_id` is nullable.
- Added Project list/detail/create, Project Source import/linking, Project Run
  creation, and filtered Run-list APIs. Factual and style-only Source IDs must
  already belong to the selected Project; cross-Project references are
  rejected before a Run is created.
- Added replayable SSE at `GET /runs/{id}/events/stream`. It replays durable
  SQLite Events after a query/header cursor, polls for newly committed Events,
  emits comment heartbeats without inventing Event rows, and closes after the
  Run reaches `succeeded`, `failed`, or `cancelled`.
- Added a React/Vite local Console with Project list/workspace, Source import
  and detail, Run configuration/history, durable Event Timeline, Task,
  Artifact and ModelCall panels, Markdown viewers, cancellation, human
  checkpoint Resume, quality feedback, and explicit Revision actions.
- Kept browser state disposable: refresh reloads Project and Run state from
  FastAPI/SQLite. Event replay and UI polling remain available if the live SSE
  connection drops. Frontend requests carry an `X-Request-ID` for correlation.
- Verified 363 backend tests and 15 frontend tests. Project tests cover durable
  refresh discovery, Source-link replay, Project Run idempotency, cross-Project
  rejection, Run listing, and child lineage. SSE tests cover ordering,
  reconnect replay, heartbeat, disconnect, missing Run, and terminal closure.
- This slice is a local development Console, not completed deployment work.
  Visual Scaffold editing, Dockerfile, production single-machine deployment,
  authentication, audio/STT, and backup automation remain intentionally open.

### M4/M5 real-browser E2E evidence

- Created one Project through the Vite UI, imported a 587-character journal
  split into 5 segments and a 335-character writing sample split into 3, and
  verified that factual/style roles remain mutually exclusive in the form.
- Created one workflow-v8 Run and visibly replayed two-Researcher fan-out,
  deterministic fan-in, Interviewer, and the material-readiness checkpoint.
  Four supplemental Source submissions moved the latest readiness evidence
  from 78 available / 2,302 missing characters to 2,403 / 0.
- Editor and Reviewer then completed. The succeeded Run exposed 6 Tasks,
  5 zero-cost Fake calls, 19 Artifacts, 64 durable Events, Draft, Show Notes,
  and quality output in the browser.
- Deterministic quality correctly blocked the roughly 1.6/10-minute Fake Draft.
  This is workflow/UI evidence, not acceptance of Fake-generated prose or a
  substitute for human recordability review.
- The walkthrough exposed and then verified fixes for two UI issues. The
  checkpoint now shows exact readiness counts, supported duration, gaps, and
  concrete questions without raw Source IDs. SSE-driven refreshes are
  coalesced and in-flight deduplicated; terminal Runs do not open SSE, derived
  records load once, and only workflow v9 requests a supplemental Plan.
- A second waiting Run showed 78 current / 2,380 minimum / 2,302 missing
  characters, a 0.24--0.32 minute support estimate, six concrete questions,
  and a live SSE state. A fresh terminal-v8 session produced no supplemental
  409, favicon 404, repeated polling, or browser console error.
- Preserved one explicit research caveat: initial readiness currently counts
  only initial Segments cited by the Scaffold, not every character in a
  selected Source. This prevents unrelated volume from inflating readiness,
  but may undercount useful material when Scaffold coverage is narrow; the E2E
  records the policy and does not claim it is fixed.

### M3.9 draft-aware supplemental interview

- Added workflow v9 only for current v2 `podcast-revision` child Runs. New
  initial episode Runs and explicit legacy Revision Request v1 keep their
  frozen v8 behavior; an omitted version can still idempotently replay a
  persisted historical v1 request.
- Added the serial `plan_draft_supplemental_interview` Planner after a Revision
  Editor and Reviewer when the exact spoken-character count remains below the
  Creative Brief's 85% floor. Duration gating no longer relies on the rounded
  display-minute value.
- Build at most 24 trusted anchors from the latest Draft's spoken opening,
  section paragraphs, and closing. Each model question must bind one allowed
  anchor and copy an exact quote; code validates it and injects stable
  `q1`--`q6` IDs.
- Persist the question Plan as an Artifact while leaving the valid Draft as the
  succeeded Run output. `GET /runs/{id}/supplemental-interview-plan` is
  read-only and creates no Task, ModelCall, or fee.
- Store user answers as new factual Sources, and version the Revision Request
  so Plan Artifact ID, answered question IDs, and Source IDs remain exactly
  traceable. The answer Revision explicitly prioritizes those Sources while
  preserving grounded parent content.
- Derive interview rounds on the server and stop after two or once the exact
  duration floor is reached. Once a Plan exists, another
  `reuse_unused_material` Revision cannot bypass the answered-question path.
- Preserve valid Drafts when the Planner exhausts retries or returns invalid
  output. A deterministic fallback Plan remains tied to exact latest-Draft
  anchors, and round two uses different dialogue, sensory, and contrast angles
  instead of repeating round one.
- Reused the existing Run, Task, Artifact, ModelCall, Event, Source, lease,
  retry, cancellation, and lineage tables; no Alembic migration or new
  framework was required. No paid DeepSeek request was made in this slice.
- The complete Fake E2E grows 2,509 → 3,073 → 3,637 spoken characters across
  two answer-driven Revisions, crosses the 3,570-character floor, and proves
  read replay, Plan enforcement, unknown-question rejection, provenance,
  fallback, idempotency, and the no-third-round boundary.
- Final validation passed all 354 backend tests, Ruff formatting/lint, and
  `git diff --check`.

### M3.8 source-grounded spoken-length recovery

- Corrected the Improvement Plan's material inventory to use only references
  attached to spoken opening, section paragraphs, and closing. A reference in
  Section metadata or Show Notes no longer makes a factual Segment look used
  by the script.
- Added a deterministic `length_recovery_plan` for an explicitly requested
  `reuse_unused_material` Revision. It binds the parent Draft's actual spoken
  characters, the 85% minimum, target, 115% maximum, remaining gaps, candidate
  unused characters, readiness, and priority factual references.
- Kept the boundary human-led: reading the Plan makes no Provider call; only an
  idempotent Revision request creates a child Run; Reviewer feedback never
  starts another Editor loop automatically.
- Tightened the Revision prompt and schema so priority references must resolve
  to allowed factual Segments that are absent from the parent spoken units.
  The Editor is told to add specific sourced information, not cover every
  reference, repeat the parent, or invent detail to reach a number.
- Reused the existing post-Revision metrics, Reviewer, non-compensatory score
  caps, ModelCall ledger, parent immutability, cancellation, recovery, and
  comparison behavior. No workflow version, API, database table, or migration
  was added.
- Kept the full unused inventory auditable but limited the Revision shortlist
  to 12 deterministic candidates. Exact duplicate/copied text is removed;
  Brief gaps, supplemental material, and concrete-detail signals rank the
  rest. The Source contract still has no structured `material_kind`, so it
  cannot reliably classify editorial instructions before generation.
- Added `style.editorial_instruction_leakage` for obvious meta-editing text in
  spoken prose and fixed the Chinese enumeration rule so ordinary uses such as
  “最后一个空行李箱” do not count without an enumeration marker.
- Versioned that semantic change as
  `draft_quality_rules_v3_editorial_instruction`,
  `zh_podcast_style_v2_enumeration_precision`, and
  `deterministic_quality_facts_v2_editorial_instruction`; idempotency keys now
  include the effective rules version while old v1/v2 Artifacts remain
  readable.
- Versioned the Improvement Plan with `prior_length_recovery_attempted`. After
  one grounded recovery still leaves a short Draft, the Plan no longer
  recommends another consecutive reuse pass; it recommends targeted
  supplemental material and a lower duration preset.
- Added the guarded `epiphany.length_recovery_e2e` driver with a read-only
  preflight, zero-cost Fake execution, exactly one explicit child Revision,
  post-Revision deterministic/model quality gates, redacted JSONL logs, and
  parent/child token, latency, and currency-grouped cost summaries. Warning
  regression compares non-duration warnings so moving from a duration blocker
  to a duration warning is not falsely treated as an unrelated regression.
- Final Fake v8 passed all workflow checks but failed content acceptance:
  456 → 2,083 spoken characters, 12/12 priority references used, still below
  the 3,570 lower bound, and editorial-instruction leakage detected. Its
  post-Revision action is `add_supplemental_material`; all seven calls are
  deterministic and zero-cost.
- The first live attempt preserved a `podcast_revision_no_change` failure
  after Flash returned the parent Draft unchanged; its earlier harness had
  masked that root cause as an Artifact error. The corrected live DeepSeek v2
  completed 7/7 calls and grew 1,310 → 2,371 spoken characters
  (4.68 → 8.47 minutes), using 86,497 input and 17,496 output tokens in
  159,228 ms for locally estimated CNY 0.201153. Workflow passed; content
  acceptance remained false because the Draft was still short.
- No new paid call was made after the deterministic stopping, warning, and
  editorial-leakage fixes. Human recordability remains pending; full evidence
  is recorded in
  `docs/experiments/m3-8-grounded-length-recovery-e2e.zh-CN.md`.
- Final local validation passed all 341 backend tests, Ruff lint, the
  102-file format check, `git diff --check`, and the zero-cost Fake v8
  end-to-end workflow. Fake exits non-zero only because its content acceptance
  intentionally remains failed.

### M3.7d realistic synthetic-persona E2E

- Froze one fictional but realistic corpus with three factual Sources, one
  full supplemental transcript, and four independent style-only samples.
  Facts and style remain disjoint.
- Added a resumable local driver for Research, deterministic fan-in,
  Interviewer, durable checkpoint, restart, supplemental Source, idempotent
  Resume, Editor, quality metrics, Reviewer, exports, and frozen A/B preflight.
- Isolated Dry Run, Fake, live main, A/B, and blind evaluation into unique
  ignored databases and artifact directories, preserving evidence across
  crashes or conversation disconnects.
- Found and fixed experiment-driver configuration drift: preflight advertised
  the product's 48,000-character Editor limit while execution inherited an old
  32,000-character checkpoint default. The original 37,091-character bundle
  was blocked locally before an Editor request; the driver now passes and
  verifies the effective Settings limits without changing old E2E defaults.
- Completed a live DeepSeek main Run with 5/5 accepted calls: 48,663 input
  tokens, 12,121 output tokens, 89,327 ms provider time, and locally estimated
  CNY 0.113035. One Pro Reviewer response violated the verbatim evidence
  contract; the workflow retained deterministic results and degraded model
  review to unavailable rather than accepting a paraphrase or silently
  retrying.
- Confirmed that 4,679 grounded characters could conservatively support
  14.2--19.2 minutes, while the Editor used only 1,893 spoken characters
  (6.76 minutes). The next product action should be a Revision using untouched
  evidence, not another generic request for user material.
- Completed the frozen DeepSeek A/B with 4/4 calls and no retry. Both arms were
  duration-blocked and capped at 39, but exact spoken-unit overlap fell from
  the earlier 0.90 to 0.1875, making the candidates distinguishable.
- Persisted a non-human blind audit before reveal. It directionally preferred
  the Sample candidate for voice and recordability; the record explicitly
  remains `human_rating=false`, not user feedback or a product winner.
- The complete experiment sequence used a locally estimated CNY 0.283416.
  Passed all 315 backend tests, Ruff lint, the 99-file format check, and Fake
  v8 E2E. Corpus design, commands, failures, costs, metrics, and privacy
  boundaries are recorded in
  `docs/experiments/m3-7d-realistic-persona-e2e.zh-CN.md`. The writing-style
  experiment scope remained frozen; its one bounded length-recovery finding
  was addressed in M3.8 before moving to M4.1.

## 2026-07-30

### M3.7b/c bounded writing-style A/B execution and blind rating

- Kept the final M3 experiment out of the production API, database, and
  workflow state machine. One local runner reuses a completed v8 Editor input
  and performs at most two Flash Editor plus two Pro Reviewer calls.
- Extended the experiment contract to freeze both rendered Editor prompt
  hashes, the style-aware Reviewer contract/prompt/formula versions, API base
  URL, billing currency, timeout, and existing model/token/bundle settings.
  Paid execution recomputes the contract and blocks with zero calls on drift.
- Randomized Editor and Reviewer arm order by default so treatment is not
  permanently tied to first/second request order. Both Reviewers still receive
  the same ready style Sample, while only the Editor treatment differs.
- Added a private atomic experiment ledger. The output directory is exclusively
  claimed; manifest and result files use `0700`/`0600`; each request is
  persisted as `started` before it may incur cost and then as
  `succeeded`/`failed`. A crashed `started` request requires provider-dashboard
  reconciliation rather than an automatic retry.
- Corrected privacy metadata: the public-safe manifest contains no Source,
  Sample, Prompt, model text, or Key, while private Draft files contain model
  text and private Quality files may contain quoted Source/Sample evidence.
- Added a local blind layer that validates the execution hashes, randomly maps
  the scripts to Candidate A/B, separates a salted private mapping from a
  public commitment, and exports only escaped script text before reveal.
- Required both candidate voice-match and recordability ratings plus a forced
  voice choice before reveal. Candidate/mapping tampering and conflicting
  rating replays are rejected; reveal presents evidence but never selects a
  winner.
- Added 17 focused tests covering the frozen input, four-call order and shared
  Reviewer Sample, contract drift, Editor/Reviewer failure accounting, output
  exclusivity and permissions, blind redaction/escaping, tamper detection,
  idempotent rating, rating conflicts, and reveal gating.
- Ran one explicitly approved DeepSeek pair from frozen Run
  `run_1bbe5ae81b0e4f118331461ab61dd656`: 2/2 Flash Editor and 2/2 Pro Reviewer
  calls succeeded with no retry, using 42,503 input and 9,579 output tokens,
  81,387 ms provider time, and an estimated CNY 0.117905. Both anonymous Drafts
  remained duration-blocked at 5.64--6.02 minutes despite equal 84.67 model
  scores; their deterministic score was 58 and capped overall score was 39.
- The source material and writing Sample in that source Run are complete
  synthetic fixtures. The run validates real Provider behavior and the
  experiment mechanism, not the effectiveness of a user's private writing
  Sample. A blind human can still assess naturalness and recordability, while
  genuine personal-style validation is deferred until consented UI onboarding.
- Stored the human rating before reveal. Candidate A received voice-match 3/5
  and recordability 3/5; Candidate B received 2/5 and 3/5. The user made a
  low-confidence forced choice for A, noting that its opening was slightly
  closer but still needed more concrete lived detail and that the latter parts
  of both Drafts were nearly identical.
- Revealed A as `with_sample` and B as `without_sample`, but did not report a
  winner. Deterministic comparison found 9 of 10 spoken units exactly equal,
  exact overlap 0.90, normalized character similarity 0.9638, and only the
  opening as a different spoken unit.
- Versioned the blind manifest and reveal as v2. Distinctness is committed with
  the mapping and candidate hashes; exact overlap >= 0.70 or normalized
  character similarity >= 0.90 produces
  `inconclusive_low_distinctness / directional_only`. The original v1 rating
  evidence was preserved rather than rewritten or regenerated.
- Passed all 310 backend tests, focused experiment tests, Ruff lint/format,
  diff check, a clean Alembic upgrade through `0004_run_lineage`, and
  `alembic check`.
- Closed and froze M3. General benchmarking, automatic winner selection, more
  paid pairs, and a blind-rating UI remain out of scope. The next slice is
  M4.1 replayable SSE, followed by the M5.1 minimal Run Trace UI.

### M3.7a frozen-input writing-style A/B preflight

- Split the writing-sample experiment into three reviewable slices instead of
  adding generation, automated review, blind rating, and live calls in one
  change. M3.7a contains only the frozen-input and safety-preflight boundary;
  M3.7b and M3.7c remain separate planned changes.
- Added a loader for one completed workflow-v8 `episode-research` Run. It
  requires exactly one succeeded original `build_podcast_draft` Task, validates
  its final Draft, cross-checks Run/Task/final-Artifact linkage, topic,
  Creative Brief, enabled quality configuration, and the Editor style profile
  and segments against the Run's explicit consented writing-style contract.
- Added an explicit read-only SQLite mode using URI `mode=ro` and
  `PRAGMA query_only=ON`. It refuses missing paths, does not create parent
  directories, does not enable WAL, and rejects write statements. SQLite may
  still maintain connection-level WAL/SHM sidecars when reading a WAL-mode
  database, but the database contents and workflow records remain unchanged.
- Derived `without_sample` and `with_sample` Editor inputs from the exact same
  persisted bundle. The former clears only `writing_style_profile` and
  `writing_style_segments`; topic, factual Sources, supplemental material,
  Interview Scaffold, Creative Brief, and quality configuration remain frozen.
- Added canonical SHA-256 evidence for the common Editor input, style context,
  both prompts, and a wider common experiment contract containing quality
  configuration, model tiers, temperatures, token limits, bundle limits, and
  the shared Reviewer style context. The preflight refuses to continue unless
  common-input hashes match and the Sample actually changes the Editor prompt.
- Added a guarded CLI:
  `python -m epiphany.writing_style_ab --run-id ... --database ...`.
  This slice is always a dry run: it has no `--execute` mode, makes zero
  Provider calls, writes no experiment files, and never mutates the source Run.
- Kept preflight output content-free. It reports IDs, hashes, counts, planned
  Flash/Pro models, the future four-call ceiling, and privacy flags without
  printing Source text, writing samples, prompts, or API keys.
- Added ten focused tests for the zero-network boundary, single-variable arm
  derivation, prompt-treatment reach, valid completed-v8 loading, tampered
  persistent relations, read-only write rejection, missing paths, and a
  blocked missing-Run path.
- Passed the complete 308-test backend suite, full Ruff lint/format checks, a
  clean Alembic upgrade to `0004_run_lineage`, and `alembic check` with no
  ungenerated schema operations.
- Ran a realistic zero-cost workflow-v8 fixture to
  `run_1bbe5ae81b0e4f118331461ab61dd656`, then ran the new preflight against its
  dedicated SQLite database. It reported a matching common input hash, a ready
  846-character style context from 1 Source / 7 Segments, zero calls, and all
  privacy flags false.
- The preflight does not measure whether a sample improves the Draft. M3.7b
  will implement the bounded two-Editor/two-Reviewer executor; M3.7c will hide
  arm identity until human voice-match and recordability ratings are saved.

### M3.6 consented writing-style context and explicit guided Revision Runs

- Versioned new quality-enabled Runs as workflow v8. Historical v1–v7 Runs
  retain their persisted execution semantics; the Revision path uses a
  separate `podcast-revision` workflow rather than replaying or mutating its
  parent.
- Added Alembic revision `0004_run_lineage`. It adds nullable
  `runs.parent_run_id`, a self-referential foreign key, and an index so a child
  Revision Run can be traced to its immutable parent.
- Added optional `writing_style_reference` input for one to five user-owned
  existing Sources. The caller must attest ownership and consent to model
  processing, and style Source IDs must be disjoint from factual `source_ids`
  within that Run. `writing_sample` is only an optional import/UI label; the
  explicit per-Run style-only selection is authoritative.
- Built a deterministic, bounded `writing_style_profile`: at most 20 selected
  segments and 12,000 non-whitespace characters, with stable references,
  hashes, aggregate sentence/paragraph statistics, readiness, and no copied
  sample text. The ready threshold is 800 characters and five sentences.
- Fixed the Editor priority boundary as application safety and grounded facts,
  then the current revision request and Creative Brief, then writing samples,
  then defaults. Samples are untrusted `style_only` data: they cannot supply
  facts, instructions, or citations. Output validation also rejects direct
  copying of a distinctive sample window unless that text independently
  appears in allowed factual evidence.
- Added a style-aware Reviewer v3 contract. A ready profile adds the seventh
  `personal_style_match` dimension and requires evidence from both the Draft
  and a style sample. Missing or limited style context retains the six existing
  dimensions and cannot support a claim that the Draft sounds like the user.
  Human `voice_match_rating` remains the final product signal.
- Added an on-demand deterministic `draft_improvement_plan`. It calculates the
  spoken-text duration gap, identifies unused factual SourceSegments, chooses
  between reuse/supplement/lower-duration strategies, carries quality gaps, and
  derives three to six targeted questions from the persisted Interview
  Scaffold. Reading the Plan makes no model call and creates no child Run.
- Added explicit `POST /runs/{parent_id}/revisions`. The request selects
  actions, feedback/gaps, optional supplemental Sources, an optional lower
  target, and a bounded instruction. Stable `submission_id` replay returns the
  same child; reusing it with different choices returns a conflict.
- Added one `revise_podcast_draft` root Task to the child Run. It consumes the
  immutable parent Draft as edit context, not as new factual evidence; returns
  a complete candidate rather than an in-place diff; uses the child's own
  model-call budget; and then queues the ordinary deterministic quality and
  Reviewer path.
- Preserved parent candidate immutability. A completed revision does not alter
  the parent's Draft, quality report, feedback, output pointer, ModelCall
  ledger, or existing Events. The deterministic Plan, explicit request
  provenance, and request Event are append-only additions on the parent.
- Added lazy, persisted `draft_revision_comparison`. It records text-free
  parent/revision summaries and character, duration, deterministic-score, and
  experimental-score deltas. It always records
  `automatic_winner_selected=false` and `requires_human_review=true`.
- Added API, schema, Provider, prompt, retry/recovery, validation, privacy, and
  integration coverage for the complete zero-cost Fake path. Tests exercise
  Plan idempotency, explicit child creation, a child that still completes when
  the per-Run limit is tightened to two after the parent spent five calls,
  style/fact separation, seventh-dimension gating, parent immutability, quality
  re-evaluation, and comparison replay.
- Added a repeatable synthetic-user E2E driver and fixture. The guarded
  `python -m epiphany.guided_revision_e2e --execute` path uses Fake only,
  pauses and resumes the parent, records synthetic feedback, explicitly creates
  the child, exports both candidates and reports, validates log redaction, and
  writes its disposable database/report under ignored local paths. The final
  backend suite has 292 passing tests.
- A live DeepSeek M3.6 E2E has not been run. The passing Fake workflow proves
  orchestration and contracts, not the quality of a real revised script.

### M3.5 Chinese draft-quality calibration and frozen Reviewer comparison

- Versioned new quality-enabled Runs as workflow v7 because the persisted
  Reviewer Task input, Prompt, deterministic rules, and Report semantics
  changed. Persisted M3.4 workflow-v6 Runs remain resumable under their legacy
  v1 contracts; an automated restart regression queues an old-shape Reviewer
  Task, creates fresh runtime objects over the same SQLite file, and completes
  it without silently upgrading stored data.
- Closed the narrower pre-release compatibility gap where a Run was labeled
  v6 but its persisted Reviewer Task already carried current deterministic
  facts. Recovery now selects metrics and report semantics from the durable
  Task contract rather than only the Run label; a second restart regression
  proves that v2 caps, conflict cards, and the `:v2` report key survive.
- Narrowed duration accounting to the words that a person would actually
  speak: opening text, section paragraph text, and closing text. Titles,
  section headings, structured SourceReferences, `[S1]` labels, source indexes,
  Show Notes, and rendered Markdown no longer inflate
  `script_character_count`.
- Added a bounded, versioned `deterministic_quality_facts` projection for the
  Reviewer. It contains the code-owned target/estimated duration, spoken
  character count, duration coverage/status, citation coverage, finding
  counts, and Chinese-style counts. The strict Task validator recomputes key
  values from the exact Draft and rejects stale or altered facts before a
  Provider call.
- Kept model opinion and application facts visibly separate. The report now
  preserves the raw six-dimension model score and the uncapped 60/40 weighted
  score, then applies the strictest code-owned non-compensatory cap: 39 for any
  deterministic blocker, 59 when estimated duration is below 60% of target,
  and 79 for any deterministic warning.
- Added explicit model/code conflict records instead of silently rewriting raw
  model cards. A generous Reviewer can still be inspected, but it cannot turn
  a blocker into an 80-point candidate.
- Added report-level invariant validation. API reads, exports, and E2E checks
  now recompute the six-card model score, uncapped formula, code cap/reasons,
  capped score, and decision, rejecting internally impossible JSON such as a
  final score above its cap. Historical v1 Artifacts retain their legacy
  compatibility boundary.
- Added conservative, versioned Chinese oral-writing signals for parallel
  contrast, escalation, enumeration, generic transitions/epiphanies/codas,
  over-politeness, and sentence/paragraph-length variation. These are
  observable editing signals, not an AI-authorship detector.
- Prevented overlapping style rules from charging twice. Historical
  `style.template_phrases` and `style.not_but_pattern` remain as informational
  compatibility findings; the `style.zh.*` categories own the score impact.
  A literal `must_include` miss is also informational because substring search
  cannot prove semantic absence. The evidence-bearing Reviewer assesses
  paraphrased coverage.
- Added optional Reviewer-only routing through
  `EPIPHANY_DEEPSEEK_REVIEWER_MODEL`. Leaving it unset reuses the primary
  model; choosing a supported Flash/Pro tier changes only the trusted
  `review_podcast_draft` Task. ModelCall and Artifact execution metadata record
  the actual provider/model, and the report distinguishes `same_model` from
  `cross_tier_same_family`.
- Added a privacy-safe frozen-input comparison runner. It validates one exact
  persisted Draft/metrics/Reviewer bundle, calls Flash and Pro in fixed order,
  and reports only schema status, scores, caps, tokens, latency, and local
  estimated cost. `--recompute-current-rules` rebuilds deterministic
  metrics/facts under the current code without regenerating the Editor Draft.
  Provider responses that incur usage but later fail strict schema/evidence
  validation retain safe token, currency, and estimated-cost fields in the
  failure row instead of disappearing from the experiment ledger.
  These experiment calls do not pretend to be entries in the source Run's
  formal ModelCall ledger.
- Passed the final workflow-v7 zero-cost Fake E2E as
  `run_f41eac8520cd4b47b97cc1181acb3d63`, covering the durable checkpoint,
  two process restarts, supplement, Editor, current deterministic rules,
  Reviewer, exports, feedback replay, and redacted logs.
- Completed the realistic DeepSeek workflow as
  `run_0af27a7596474a92ba79e298e912e35e`. This paid Run is explicitly retained
  as a pre-release M3.5 workflow-v6 development snapshot rather than relabeled
  as v7. It succeeded with six successful Tasks, five calls, and a locally
  estimated CNY 0.089433. The final spoken Draft had 2,055 characters,
  estimated as 7.34 minutes against a 15-minute target.
- Preserved an important harness failure rather than hiding it. The first
  machine report marked `model_calls_match_provider` and
  `quality_report_contract_valid` false because the old checker assumed Editor
  and Reviewer always used one model and only accepted the old relation
  contract. The persisted workflow data was valid. After routing-aware/current
  contract fixes, an offline validation against the same database made both
  checks true, so no paid workflow rerun was needed.
- Recomputed the frozen Draft under current rules: deterministic score 62,
  1 blocker, 1 warning, and 2 informational observations. The strictest cap
  remains 39.
- On the first historical deterministic snapshot, Flash scored 76.67 and Pro
  80; both finished at 39 after the same code cap. In current-rules mode,
  Flash failed the strict output schema while Pro returned raw 70 and final
  39. The two current-rules comparison attempts had local estimated costs of
  CNY 0.013950 and CNY 0.044008.
- Treat the comparison as one synthetic calibration sample, not a benchmark.
  It cannot establish that Pro is generally better or that Flash generally
  fails. More topics, lengths, repeated trials, and real-user feedback are
  required before changing the default.
- At the M3.5 cut, M3.6 remained a plan: an explicit feedback-driven child
  Revision Run with an immutable parent and no score-chasing loop. The M3.6
  entry above records the later implementation of that boundary.

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
