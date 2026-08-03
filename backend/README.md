# Epiphany Studio Backend

The backend currently implements a single-user, single-process durable task
runner through the M3.9 draft-aware supplemental-interview slice, including parallel
research, model-call traces, a serial Interviewer, durable
`waiting_for_user`, source-ID based Resume, a serial Editor, Draft Quality,
explicit Revision child Runs, and deterministic Markdown exports. The
M5.1 Source Starter reuses the same Run, Task, ModelCall, Artifact and Event
runtime for one bounded blank-page helper; its generated candidate is not
imported as evidence until the user explicitly confirms the edited text. The
milestone walkthroughs below retain their historical workflow versions so old
Runs remain understandable. The backend also includes an opt-in DeepSeek V4
adapter. The default remains the deterministic
`FakeProvider`, which reports zero tokens and zero cost, so setup, Swagger, and
the default test suite need no API key, network request, or paid model call.

The first workflow is deliberately small:

```text
prepare_sources -> fake_research -> assemble_artifact
```

The historical workflow-v4 path introduced in M3.2 is:

```text
research_manager
  -> fan-out (maximum 2)
       |- timeline_research
       `- theme_research
  -> validate strict schemas and Source references
  -> fan-in
  -> episode_research_bundle
  -> serial interviewer
  -> validate strict Interview Scaffold schema and bundle citations
  -> build_interview_scaffold_result
  -> waiting_for_user
  -> import already-transcribed user material as a Source
  -> idempotent Resume
  -> serial build_podcast_draft Editor
  -> validate strict structure plus initial/supplemental citations
  -> build_podcast_draft_result
  -> export Podcast Draft and Show Notes
  -> human final review
```

Each agent-executed step is a persisted Task. The worker claims it with a lease,
writes an append-only Event, commits an idempotent Artifact, and asks the
deterministic Orchestrator what may run next. Human waiting, Source import,
Resume, and deterministic checkpoint transitions are persisted state changes,
not placeholder Tasks. Before entering the Provider, the Worker reserves a
durable `ModelCall`. The Fake Provider exercises the exact same accounting
boundary as a future hosted model but reports zero tokens and zero cost.

## Local setup

Python 3.12 or newer is required.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn epiphany.main:app --reload
```

Alembic is the schema authority. Normal application startup does not call
`metadata.create_all()`; that helper is reserved for isolated tests. Run
`alembic upgrade head` after pulling a migration and before starting Uvicorn.

Run tests:

```bash
pytest
```

The current full backend suite collects and passes 442 tests. Historical milestone-specific test
counts are kept in the learning chapters and development log instead of being
presented here as a permanently current total.

The default SQLite database is written to `./data/epiphany.db`, which is ignored
by Git.

## M1 API

```text
GET  /health
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
POST /runs/{run_id}/cancel
```

Example:

```bash
curl -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -d '{"payload":{"topic":"成年十年"}}'
```

Copy the returned `id`, then inspect the durable state and event replay:

```bash
curl http://127.0.0.1:8000/runs/run_REPLACE_ME
curl http://127.0.0.1:8000/runs/run_REPLACE_ME/events
```

Restarting Uvicorn does not remove completed Runs because SQLite is the source
of truth. The in-process worker requeues expired leases on startup.

## M2.1 source API

Import normalized plain text:

```bash
curl -i -X POST http://127.0.0.1:8000/sources \
  -H 'content-type: application/json' \
  -H 'x-request-id: req_source_import' \
  -d '{
    "title": "EP0 draft",
    "source_type": "podcast_draft",
    "text": "第一段素材。\n\n第二段素材。",
    "metadata": {"episode": 0}
  }'
```

Query summaries or one Source with its ordered segments:

```bash
curl http://127.0.0.1:8000/sources
curl http://127.0.0.1:8000/sources/src_REPLACE_ME
```

Re-importing the same normalized text returns HTTP 200 with `created: false`
and the existing stable IDs. A new Source returns HTTP 201. The whole normalized
text stays in local SQLite and is not returned by the API; callers receive the
ordered segments needed for future citations.

## M5.1 Project Source Starter API

The browser uses this API when a user clicks **帮我起个头**. It is independent
of visual Scaffold/Draft editing because its result is ordinary editable Source
text.

Create or reuse a Project, then create one idempotent starter Run:

```bash
curl -i -X POST http://127.0.0.1:8000/projects/proj_REPLACE_ME/source-starters \
  -H 'content-type: application/json' \
  -d '{
    "submission_id": "starter-demo-1",
    "source_title": "我为什么想了解潜水",
    "source_type": "journal",
    "mode": "exploration_outline",
    "intent": "我为什么会被潜水吸引？"
  }'
```

Poll the returned Run and its durable Events:

```bash
curl http://127.0.0.1:8000/runs/run_REPLACE_ME
curl http://127.0.0.1:8000/runs/run_REPLACE_ME/events
```

After generation succeeds, a `source-starter` v1 Run has one succeeded
`build_source_starter` Task, one ModelCall, and one
`source_starter_candidate` Artifact, but the Run itself durably pauses at
`waiting_for_user / awaiting_source_confirmation`. This checkpoint does
**not** increase the Project's Source count. The candidate contains editable
`starter_text`, questions, uncertainties, and safety flags.

After the user has edited and checked the text, confirm it through the separate
endpoint:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/projects/proj_REPLACE_ME/source-starters/run_REPLACE_ME/confirm \
  -H 'content-type: application/json' \
  -d '{
    "submission_id": "starter-confirm-demo-1",
    "title": "我为什么想了解潜水",
    "source_type": "journal",
    "text": "这里放用户已经核对并修改过的最终正文。"
  }'
```

Confirmation atomically imports the text as a Source, links it to the Project,
saves `origin=ai_assisted` and `user_confirmed=true` metadata, appends a
server-owned `source_starter_confirmation` Artifact and confirmation Events,
and transitions the Run to `succeeded / complete`. If any write fails, the
whole transaction rolls back to the same waiting checkpoint.

The semantic confirmation fingerprint contains title, source type, and text;
it deliberately excludes `submission_id`. Replaying the same content with a
new submission ID returns the original result and appends that ID to the
Artifact's audited `submission_ids` list. Changing semantic content after
confirmation returns 409.

Only `journal`, `podcast_draft`, and `other` are accepted.
`writing_sample` and `voice_note_transcript` are rejected because AI text
cannot impersonate a user-owned style sample or an actual speech transcript.
The confirmed AI-assisted Source is also rejected as a Writing Sample when a
Project Run or Revision later builds writing-style context.

Browser retry after a polling/network error only repeats `GET /runs/{id}` and
`GET /runs/{id}/events`; it does not create another Run or ModelCall. Refresh
can restore the server candidate Artifact and the Run's original `mode` and
`intent`, but cannot recover edits that only existed in the browser and were
never confirmed. Regeneration is blocked after the candidate text has been
edited, so two generated candidates are not silently stacked into one Source.

Live validation has a bounded failure chain. A first invalid hosted result may
schedule one repair attempt, which is recorded as a second ModelCall. If the
second response has a valid candidate shape but some lines still invent user
history, dialogue, or unverified facts, `server_line_grounding` preserves only
individually safe lines/items and converts the unsafe parts into explicit
completion/verification regions. The complete candidate is then validated
again. If that deterministic repair cannot pass, the Worker uses a
server-owned safe template. Provider/network failures are not disguised as a
successful template, and none of these paths confirms or imports a Source.

Targeted zero-network tests:

```bash
pytest tests/test_source_starter.py -q
```

See the [M5.1 learning chapter](../docs/learning/m5-1-source-starter.zh-CN.md)
for the four visible progress steps, Fake-browser walkthrough, grounding chain,
and debugging path. The complete synthetic Playwright/DeepSeek evidence and its
reproducible inputs are in the
[M5.1b experiment report](../docs/experiments/m5-1b-real-browser-e2e.zh-CN.md)
and [`fixtures/e2e/m5-1b-real-browser/`](fixtures/e2e/m5-1b-real-browser/). The
public record never contains an API key or private user material.

## Current episode-research API (workflow v4)

Import a Source as shown above, copy its `source.id`, and start a Run:

```bash
curl -i -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -H 'x-request-id: req_research_demo' \
  -d '{
    "workflow_type": "episode-research",
    "payload": {
      "topic": "五年后重新开始录播客",
      "source_ids": ["src_REPLACE_ME"]
    }
  }'
```

New `episode-research` requests require both a non-blank `topic` and at least
one `source_id`; missing or blank topics return HTTP 422. New Runs are stamped
with `workflow_version: "v4"`. The response initially contains a running
`research_manager` and two queued children with the Manager's ID in
`parent_task_id`. With the default Worker enabled, poll the returned Run ID:

```bash
curl http://127.0.0.1:8000/runs/run_REPLACE_ME
curl http://127.0.0.1:8000/runs/run_REPLACE_ME/events
```

The waiting Run has:

- four succeeded Tasks: one Manager, two sibling Researchers, and one serial
  Interviewer;
- two validated Researcher Artifacts, one `episode_research_bundle`, and one
  `build_interview_scaffold_result`;
- `model_call_count: 3`, representing three Fake Provider executions;
- durable `workflow.fan_out.started`, `workflow.fan_in.waiting`, and
  `workflow.fan_in.completed` Events followed by
  `workflow.interview_scaffold.queued` and
  `workflow.interview_scaffold.completed`;
- `status: "waiting_for_user"` and
  `current_step: "awaiting_interview_response"`, with no queued/running Task
  and no `run.succeeded` Event.

The Scaffold is already readable at this point:

```bash
curl -OJ \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/exports/interview-scaffold.md
```

To simulate a spoken follow-up, type or paste the **already-transcribed
text** into a new Source. This does not request microphone permission and does
not upload or transcribe audio:

```bash
curl -i -X POST http://127.0.0.1:8000/sources \
  -H 'content-type: application/json' \
  -d '{
    "title": "EP0 interview response round 1",
    "source_type": "voice_note_transcript",
    "text": "我重新听见五年前的声音时，最明显的感觉是时间被保存下来了。",
    "metadata": {"round": 1}
  }'
```

Copy that response's `source.id`, then Resume:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/resume \
  -H 'content-type: application/json' \
  -H 'x-request-id: req_resume_ep0_round_1' \
  -d '{
    "checkpoint": "interview_scaffold",
    "submission_id": "ep0-round-1",
    "source_ids": ["src_REPLACE_WITH_SUPPLEMENTAL_SOURCE"]
  }'
```

The first valid call returns `resumed: true`, adds one
`user_material_submission` Artifact containing Source/Segment references
rather than transcript text, and queues one `build_podcast_draft` Editor Task.
The response may still show the Editor as `queued` or `running`; poll the Run
until it reaches `succeeded / complete`. The final v4 Run has 5 Tasks,
6 Artifacts, and 4 ModelCalls. Repeating the exact Resume returns
`idempotent_replay: true` without another Artifact, Event, Task, or paid call.
Reusing the same submission ID with different Sources returns HTTP 409.

Export all three documents:

```bash
curl -OJ \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/exports/interview-scaffold.md
curl -OJ \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/exports/podcast-draft.md
curl -OJ \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/exports/show-notes.md
```

The Fake Provider creates deterministic, source-grounded regression output
without a paid call. It extracts topic-relevant sentences, dates, themes, and
quotes from the SourceSegments assigned to each Task; the guarded E2E supplies
the committed synthetic fixture. The exported Scaffold is therefore readable
during manual testing. It is still not a model-quality benchmark: its main job is to
validate bounded parallel research, the sequential Interviewer, strict output,
citation scope, supplemental-evidence use, failure propagation, idempotent
transitions, and late-result fencing.

Focused verification:

```bash
pytest tests/test_research_schemas.py \
       tests/test_research_workflow.py \
       tests/test_interview_scaffold.py \
       tests/test_interview_export_api.py \
       tests/test_editor_core.py \
       tests/test_editor_workflow.py \
       tests/test_human_checkpoint_api.py -q
```

The failure-injection test returns an out-of-scope Source reference from one
child while delaying the other. It verifies that the child and Manager fail,
the sibling is cancelled, the late result is rejected, and no Artifact is
written.

## M2.3a zero-network model-call trace

Every attempted Provider invocation is now visible in the Run response under
`model_calls`. Each record includes:

- Task ID and attempt number;
- Provider and model;
- `started`, `succeeded`, `failed`, or `timed_out` status;
- input/output tokens;
- duration in milliseconds;
- estimated cost in millionths of the named currency;
- a stable error code without prompt or response content.

`EPIPHANY_MODEL_MAX_CALLS_PER_RUN` defaults to six. The Worker reserves a call
before invoking the Provider, so a limit failure does not accidentally send an
extra paid request. Retries consume another call because a real provider may
charge for every attempt.

Focused zero-cost verification:

```bash
pytest tests/test_model_call_trace.py -vv
```

This test module uses only Fake Providers. It verifies successful usage
accounting, retry accounting, timeout traces, and rejection before an
over-budget invocation.

## M2.3b-1 DeepSeek adapter without live usage

The DeepSeek adapter supports `deepseek-v4-flash` and `deepseek-v4-pro` through
`https://api.deepseek.com/chat/completions`. It sends one HTTP request per Task
attempt, uses JSON Output with thinking disabled, and returns usage and
estimated cost micros in the explicitly configured billing currency to the
existing ledger.

It is disabled by default. The committed example remains:

```env
EPIPHANY_MODEL_PROVIDER=fake
EPIPHANY_DEEPSEEK_API_KEY=
EPIPHANY_DEEPSEEK_MODEL=deepseek-v4-flash
EPIPHANY_DEEPSEEK_BILLING_CURRENCY=USD
EPIPHANY_DEEPSEEK_MAX_TOKENS=2000
EPIPHANY_DEEPSEEK_RESEARCH_MAX_TOKENS=4000
EPIPHANY_DEEPSEEK_INTERVIEW_MAX_TOKENS=4000
EPIPHANY_DEEPSEEK_MAX_SOURCE_CHARS=24000
EPIPHANY_DEEPSEEK_MAX_INTERVIEW_BUNDLE_CHARS=24000
```

The output limits are task-specific: the generic 2,000-token limit remains for
short utility generations, while Timeline/Theme Research and the Interview
Scaffold each receive a bounded 4,000-token allowance. A Research response that
ends with `finish_reason=length` receives at most one durable compact-repair
attempt; both paid attempts remain visible in `model_calls`. Truncation in other
task kinds is not blindly retried with the same instructions.

The two character limits are independent. `MAX_SOURCE_CHARS` protects raw
Researcher input; `MAX_INTERVIEW_BUNDLE_CHARS` protects the validated
Timeline/Theme bundle passed to the Interviewer. They default to the same value
for compatibility, but a deployment may tune them separately.

`EPIPHANY_DEEPSEEK_BILLING_CURRENCY` accepts `USD` or `CNY`. It defaults to
`USD` so existing installations retain their previous behavior. The DeepSeek
completion response contains Token usage but not the account's billing
currency, so the backend cannot safely auto-detect it. Set it explicitly in
the ignored `backend/.env`; for an account whose Dashboard and balance are in
CNY, use:

```env
EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY
```

Each new `ModelCall` stores the estimate and its configured currency together.
Existing USD rows are not converted or rewritten, and no database migration is
needed. When a summary contains more than one currency, it reports one total
per currency rather than producing an invalid mixed-currency sum.

Focused zero-network verification:

```bash
pytest tests/test_deepseek_provider.py \
       tests/test_deepseek_research_workflow.py -vv
```

The Provider HTTP tests use `httpx.MockTransport`; they do not read the local
API key or contact DeepSeek. They cover the successful v3
research-and-interview workflow, 429 retry accounting, terminal authentication
failure, timeout status, invalid citations, response usage/cost, and
secret/content log redaction.

When DeepSeek is selected, use `workflow_type: "episode-research"`. The old
`fake-podcast` workflow is intentionally rejected before HTTP rather than sent
to the hosted model.

## M2.3b-2a bounded live-smoke command

The smoke harness is deliberately separate from `pytest`, `uvicorn`, and
Swagger. Running it without `--execute` is a zero-network preflight:

```bash
cd backend
source .venv/bin/activate
python -m epiphany.live_deepseek_smoke
```

The preflight reports whether a key is present, but never prints its value. It
also shows the fixed safety boundary:

- synthetic text only;
- `deepseek-v4-flash`;
- three model calls maximum;
- one attempt per model-backed Task;
- one in-flight request, so the two Researchers and final Interviewer are
  serialized;
- 800 output tokens maximum per call;
- a small estimated cost in the explicitly configured billing currency, rather
  than a billing guarantee.

To perform the intentional live check, put the key only in ignored
`backend/.env`:

```env
EPIPHANY_DEEPSEEK_API_KEY=your-local-key
EPIPHANY_DEEPSEEK_BILLING_CURRENCY=CNY
```

The example uses `CNY` because the current local DeepSeek account is billed in
CNY. Use `USD` for a USD-billed account.

Then run:

```bash
python -m epiphany.live_deepseek_smoke --execute
```

The command applies Alembic to the dedicated ignored
`data/deepseek-live-smoke.db`, imports a short synthetic Source, runs the two
Researcher Tasks and then the Interviewer, and exits successfully only if all
three ModelCalls and all four Artifacts succeed and the v3 Run reaches its
durable waiting checkpoint. It prints IDs, task/call status, tokens, duration,
estimated cost totals grouped by currency, and artifact kinds. It does not
print the key, Prompt, source text, generated content, or error response body.
No FastAPI server or Swagger page is needed.

Focused zero-network safety verification:

```bash
pytest tests/test_live_deepseek_smoke.py -vv
```

If the live command fails, start with the final `run.id`, Task `error_code`,
ModelCall status, and the structured logs. A 401 usually means an invalid key,
402 means insufficient API balance, and network/timeout failures leave a
durable trace in the dedicated database.

The first live verification completed on 2026-07-28 with Run
`run_e8ad6452087c479cb84293ae3919201d`:

- both `timeline_research` and `theme_research` succeeded on attempt 1;
- two `deepseek-v4-flash` ModelCalls succeeded;
- strict schema, source-reference, and quote validation passed;
- deterministic fan-in produced all three expected Artifacts;
- usage was 1,092 input and 1,209 output tokens;
- combined Provider latency was 15,435 ms;
- estimated total cost was USD 0.000491.

These values are a historical smoke result, not a future latency or billing
guarantee. The ignored SQLite trace retains the corresponding Tasks, Events,
ModelCalls, and Artifact metadata. Enabling CNY for future calls does not alter
these two USD rows.

M2.4 changed the then-current harness ceiling from two calls to three; M3.1 kept
that ceiling and expected workflow v3 to stop at `waiting_for_user`. M2.4 itself
used only the zero-network dry-run; the later M3.1 realistic E2E section records
the newer paid validation. The two-call, 2,301-token, USD 0.000491 result above
remains the historical M2.3b trace.

## M2.4 Interview Scaffold and Markdown export

After both Researchers succeed, the Manager deterministically writes
`episode_research_bundle`. Workflow v2 then queues
`build_interview_scaffold`; this Interviewer runs only after the bundle exists
and receives the requested topic plus the bounded Timeline and Theme content.
Its prompt and output schema are strict. Validation rejects unexpected fields,
blank text, a title that differs from the topic, malformed sections, and any
Source reference that is absent from the research bundle.

When the historical v2 Run succeeds, `output_artifact_id` points to
`build_interview_scaffold_result`. Export the validated artifact as Markdown:

```bash
curl -OJ \
  http://127.0.0.1:8000/runs/run_REPLACE_ME/exports/interview-scaffold.md
```

`GET /runs/{run_id}/exports/interview-scaffold.md` returns deterministic
`text/markdown`, renders citations as `[S1]` labels plus a Source-title and
segment-position appendix, and escapes raw HTML and Markdown control syntax
from model-produced text. Structured Artifacts and SQLite retain the original
Source/Segment IDs. It returns HTTP 404 for an unknown Run and HTTP 409 until
a valid scaffold and all referenced source metadata are available. v3 and v4
Runs may export it while `waiting_for_user`; v2 Runs may export it after
`succeeded`. v4 can still export the Scaffold after the final output changes
to the Editor Artifact.

Existing in-flight `episode-research` Runs stamped `workflow_version: "v1"`
retain their original completion semantics: they stop after fan-in with
`episode_research_bundle` as the output, without requiring a new topic or
queuing an Interviewer. M2.4 reuses the existing runtime tables and requires no
database migration.

## M3.1 durable human checkpoint

Workflow v3 preserves the M2.4 Task graph but changes the post-Interviewer
boundary:

```text
running / build_interview_scaffold
  -> waiting_for_user / awaiting_interview_response
  -> POST /sources
  -> POST /runs/{run_id}/resume
  -> running / accepting_user_material
  -> succeeded / complete
```

The waiting status, Scaffold output, Tasks, Artifacts, ModelCalls, and Events
are all stored in SQLite. Restarting Uvicorn therefore leaves the same Run at
the same checkpoint, and `worker.run_until_idle()` has nothing to claim.

Resume stores a fifth Artifact of kind `user_material_submission`. Its content
contains the checkpoint, stable submission ID, Scaffold Artifact ID, Source
IDs, and SourceSegment references. The transcript itself remains in the Source
tables and is never copied into the Artifact, Event payloads, or stdout logs.
The original Run input also remains unchanged.

M3.1 intentionally completes immediately after accepting the material. It
does not enqueue an Editor, generate a podcast draft or Show Notes, or change
the output away from the Scaffold. That is the next M3.2 vertical slice.

Focused verification:

```bash
pytest tests/test_human_input_schemas.py \
       tests/test_human_checkpoint_api.py \
       tests/test_research_workflow.py \
       tests/test_interview_export_api.py -vv
```

These tests cover restart persistence, waiting-state export, 404/409/422
errors, idempotent replay, conflicting submissions, concurrent duplicate
Resume, cancellation while waiting, Resume-versus-Cancel terminal-state
fencing, and content-free logs.

Current concurrency boundary: one application process owns one `RunService`.
That service uses a shared mutation lock so Resume and Cancel cannot both win.
SQLite's Artifact unique constraint prevents duplicate submission rows across
processes, but the losing multi-process request is not yet translated into an
idempotent replay or HTTP 409. Add database compare-and-set/row locking or
catch-and-reread conflict handling before a multi-worker deployment.

No migration was added. `alembic current` remains
`0003_model_call_trace (head)` and `alembic check` reports no new operations.
The M3.1 historical backend suite contained 130 tests.

## M3.2 Editor and final Markdown

Workflow v4 preserves the same durable waiting point, then continues after
Resume:

```text
waiting_for_user / awaiting_interview_response
  -> POST /sources with an already-transcribed follow-up
  -> POST /runs/{run_id}/resume
  -> running / accepting_user_material
  -> build_podcast_draft queued -> running -> succeeded
  -> succeeded / complete
```

The Editor is a serial root Task, not another Researcher child. Its bounded
input contains the validated Scaffold, the initial SourceSegments actually
referenced by that Scaffold, and the newly submitted SourceSegments. Topic,
Scaffold, and Source text are all treated as untrusted data.

The strict output contains a Podcast Script and Show Notes. Every narrative
paragraph and Show Notes item must cite an allowed SourceSegment. The Podcast
Script must use both initial and supplemental evidence; Show Notes must use
supplemental evidence. Wrong topics, unknown references, missing supplemental
grounding, extra fields, or malformed output fail before an Artifact is
committed.

Default Editor-specific DeepSeek limits:

```text
EPIPHANY_DEEPSEEK_MAX_EDITOR_BUNDLE_CHARS=48000
EPIPHANY_DEEPSEEK_EDITOR_MAX_TOKENS=20000
```

The final `output_artifact_id` points to `build_podcast_draft_result`.
`GET /runs/{run_id}/exports/podcast-draft.md` and
`GET /runs/{run_id}/exports/show-notes.md` render that strict JSON
deterministically. The Scaffold Artifact remains stored, and its original
export endpoint continues to work after Editor completion.

Resume replay cannot queue a duplicate Editor. Retry attempts receive separate
ModelCall rows but commit one idempotent final Artifact. Startup recovery,
cancel fencing, timeout, and the per-Run budget reuse the existing Worker.
With a three-call budget, Editor is rejected before the fourth Provider
request. Workflow v3 Runs keep their historical immediate-completion Resume
semantics, so deployment does not add a paid call to persisted work.

M3.2 reuses the existing schema; `alembic check` reports no new operations.
The current full suite contains 151 passing tests.

## M3.2 backend E2E

The guarded E2E command drives the real FastAPI lifespan, Worker,
Orchestrator, SQLite stores, HTTP routes, checkpoint, Resume replay, logs, and
Markdown export with a committed synthetic fixture. It does not require a
separately running Uvicorn process.

```bash
# preflight only: no database, network request, or paid call
python -m epiphany.checkpoint_e2e --provider deepseek

# complete zero-cost journey
python -m epiphany.checkpoint_e2e --provider fake --execute

# explicit, bounded live journey; may incur DeepSeek API charges
python -m epiphany.checkpoint_e2e --provider deepseek --execute
```

The default ignored evidence is written to
`data/editor-e2e.db` and `artifacts/editor-e2e/`. The machine-readable
report summarizes Run/Task/Artifact/ModelCall state, events, tokens, estimated
cost, redaction checks, and failures. It exports `interview-scaffold.md`,
`podcast-draft.md`, and `show-notes.md`.

The current realistic fixture contains three coherent initial Sources plus one
complete supplemental transcript. Researcher source input is capped at 8,000
characters; the validated merged research Bundle has a separate 24,000
character ceiling before the Interviewer; the combined Editor input has its
own 32,000-character E2E ceiling and 6,000-token output limit. The first
distinction was added after a real run completed both Researchers but rejected
the larger Bundle before the third network request.

The historical corrected bounded M3.1 DeepSeek run
`run_44c9db75a74744ac940efd2d27172107` completed the full M3.1 journey:

- 4 Sources and 21 SourceSegments;
- 4 succeeded Tasks;
- 4 Artifacts / 3 ModelCalls / 26 Events while waiting;
- 5 Artifacts / 3 ModelCalls / 29 Events after Resume;
- 10,046 input and 6,670 output tokens;
- 52,003 ms combined Provider time;
- estimated CNY 0.023386;
- structured-log redaction and idempotent replay checks passed.

Human review found useful concrete questions and one unsupported tense
escalation from a planned Episode 0 to an already-published episode. The
Interviewer prompt now explicitly preserves plan/draft/wish status, but valid
citation IDs still do not prove semantic entailment; human review remains part
of the product boundary.

The M3.2 Fake E2E verifies 4 Tasks / 4 Artifacts / 3 ModelCalls at the waiting
checkpoint and 5 / 6 / 4 after Editor completion. It requires the Draft and
Show Notes to use the supplemental Source, checks the exact ten-event delta
after Resume, proves Resume replay is idempotent, verifies the Scaffold hash
remains unchanged, and rejects source text or secrets in structured logs.

The explicit 2026-07-29 live DeepSeek run
`run_88d16bf3e03f45a98edfea2c164e383a` passed all guarded checks:

- 5 succeeded Tasks, 6 Artifacts, and 4 succeeded ModelCalls;
- 26 Events before Resume and 36 after Editor completion;
- 16,667 input tokens and 9,468 output tokens;
- 73,018 ms combined Provider duration;
- estimated CNY 0.035603;
- Scaffold, Podcast Draft, Show Notes, idempotency, grounding, and log
  redaction checks all passed.

The cost is a local configured-price estimate, not the provider invoice. The
fixture is synthetic, and valid citations still do not replace final human
content review.

See
[`docs/learning/m3-1-backend-e2e.zh-CN.md`](../docs/learning/m3-1-backend-e2e.zh-CN.md)
for repeatable commands, and
[`docs/learning/m3-1-realistic-e2e-evidence.zh-CN.md`](../docs/learning/m3-1-realistic-e2e-evidence.zh-CN.md)
for the failure analysis, successful live evidence, cost table, and content
review of the historical M3.1 run. The current Editor design, tests, and
commands are documented in
[`docs/learning/m3-2-editor-final-markdown.zh-CN.md`](../docs/learning/m3-2-editor-final-markdown.zh-CN.md).

## M3.3 Creative Brief and material readiness

Supplying a `creative_brief` records a 10, 15, or 30 minute target, an
adjustable characters-per-minute estimate, scenario, audience, communication
goal, tone, required details, and patterns to avoid. Material readiness is
ordinary deterministic code, not a model call. It persists aggregate counts
without copying Source text and keeps an obviously short Run at
`waiting_for_user / awaiting_more_material`.

Each accepted supplemental Source submission is append-only and idempotent.
Readiness is recalculated over all accepted rounds. Editor is queued exactly
once only after the accumulated, deduplicated material crosses the configured
lower bound. This estimate protects against obvious shortage; it does not
promise an actual recording duration or evaluate prose quality.

## M3.4 Draft Quality Report and feedback

With a Creative Brief, draft quality defaults to enabled and a new Run uses
workflow `v6`. Explicitly sending
`"draft_quality": {"enabled": false}` preserves the v5 path and avoids the
extra Reviewer model call.

After Editor, v6 first persists deterministic `draft_metrics_report` data:
estimated duration, paragraph citation coverage, source diversity, exact and
eight-character-window repetition, Brief constraints, filler patterns,
template phrases, and repeated "not ... but ..." style patterns. The duration
is an estimate based on non-whitespace characters and the configured speaking
rate, not measured audio duration.

One serial `review_podcast_draft` Task then evaluates six fixed dimensions:
Brief adherence, source faithfulness, coverage/specificity,
structure/coherence, oral naturalness/voice fit, and
conciseness/non-redundancy. Every assessable dimension needs an exact quote and
a valid Draft location. Application code verifies that the quote exists and
that any SourceReference belongs to that Draft block. The final decision is
code-owned; a model cannot override deterministic blockers.

The model result is always advisory. When Editor and Reviewer use the same
model, the report records `reviewer_relation="same_model"` rather than
presenting the result as an independent review. The API does not report an
"AI-generated probability." It reports observable style signals and
evidence-bearing suggestions.

A permanent Reviewer, authentication, or budget failure preserves the
deterministic metrics and error reason. A pre-existing deterministic blocker
keeps the decision `blocked`; otherwise the report uses
`automated_review_incomplete`. The valid Editor Draft remains exportable and
remains `output_artifact_id`, and the Run succeeds instead of hiding the core
result.

New endpoints:

```text
GET  /runs/{run_id}/quality-report
GET  /runs/{run_id}/exports/quality-report.md
POST /runs/{run_id}/quality-feedback
GET  /runs/{run_id}/quality-feedback
```

Feedback is independent and append-only. `feedback_origin="human"` is marked as
a candidate human signal; `feedback_origin="synthetic_test"` is reserved for
automated E2E and is always persisted with
`human_signal_eligible=false`. In this unauthenticated local MVP the origin is
caller-declared, not identity verification. Feedback comments stay in the
Artifact and can be read through local data APIs, but are not copied into
Events or stdout logs.

Focused validation:

```bash
pytest \
  tests/test_draft_quality.py \
  tests/test_draft_quality_provider.py \
  tests/test_draft_quality_workflow.py \
  tests/test_draft_feedback_api.py -vv
```

M3.4 reuses the existing Run, Task, Artifact, Event, and ModelCall tables. It
requires no migration. The 2026-07-29 validation snapshot passed Ruff,
format-checking for 71 files, all 205 pytest cases, Alembic upgrade/check, the
M3.3 Fake regression E2E, and the M3.4 Fake E2E.

Run the guarded v6 journey with deterministic Fake output:

```bash
python -m epiphany.draft_quality_e2e --provider fake --execute
```

The explicit 2026-07-29 DeepSeek Run
`run_276a3bce22394eb8a56edd6af8760012` passed the same guarded journey:

- workflow v6, 6 succeeded Tasks, and 5/5 succeeded ModelCalls;
- 11 Artifacts before feedback and 12 after one idempotent
  `synthetic_test` feedback submission;
- 26,618 input tokens, 11,239 output tokens, and 61,669 ms combined Provider
  duration;
- local estimated cost CNY 0.049096;
- persistent checkpoints across three App lifespans, durable Reviewer queue,
  supplemental-source grounding, feedback replay, and 85 structured JSON log
  lines with no Source or generated prose.

The report returned `revision_recommended`, deterministic 72, and experimental
83.2. A 10-minute target contained 1,429 non-whitespace characters and was
estimated at 5.1 minutes. It still had 100% paragraph citation coverage,
4 Sources / 10 Segments, no exact duplicate paragraph, one filler hit, and
four "not ... but ..." constructions. The same DeepSeek model used by Editor
scored all six Reviewer dimensions 5/5. That disagreement is expected evidence
that same-model self-review is advisory rather than a release gate.

One earlier paid attempt reached Editor but was rejected by the strict
`podcast_draft_missing_supplemental_source_reference` contract. The
prompt/output tail self-check was strengthened and the next Run passed. The
earlier local CNY 0.039696 estimate is separate debugging expenditure and is
not part of the successful Run's CNY 0.049096. Local estimates can differ from
the provider dashboard or invoice because of pricing rules, cache treatment,
and usage-reporting delay.

See the beginner-oriented
[`M3.4 learning chapter`](../docs/learning/m3-4-draft-quality-report.zh-CN.md)
for the scoring boundary, Swagger walkthrough, event names, and SQLite
queries.

## M3.8 grounded spoken-length recovery

When a completed quality Run is shorter than the Creative Brief, reading

```text
GET /runs/{run_id}/improvement-plan
```

now inventories factual Source Segments that are wholly absent from the spoken
opening, section paragraphs, and closing. Section-level metadata and Show
Notes do not count as spoken use. The current inventory is reference-level:
once a Segment is cited by any spoken unit, it is considered used even when
only a small part of its content was developed. M3.8 does not yet detect
“cited but underused” facts.

An explicit

```text
POST /runs/{run_id}/revisions
```

with `selected_actions=["reuse_unused_material"]` receives an exact
`length_recovery_plan`: current, 85% minimum, target, and 115% maximum spoken
character counts; the remaining gaps; and priority references that still
resolve to allowed factual Sources. `available_unused_character_count` is only
the raw candidate capacity. It does not score relevance, duplication,
sensitivity, or likely prose quality, and therefore cannot guarantee that the
Revision will reach the requested duration.

The user creates one idempotent child Run. The Revision Editor must add
source-grounded information rather than filler or invented detail, after which
the normal metrics, Reviewer, non-compensatory caps, and parent/child
comparison run again. There is no automatic retry-until-long-enough loop. If
the child remains short, this recovery attempt stops. The caller may accept
the shorter Draft, add targeted material, or lower the target duration; the
system never chains another Revision automatically.

Focused zero-cost validation:

```bash
pytest tests/test_draft_improvement.py \
       tests/test_revision_schemas.py \
       tests/test_revision_workflow.py \
       tests/test_length_recovery_e2e.py -vv
```

The final deterministic Fake v8 grows from 456 to 2,083 spoken characters and
uses all 12 priority references, but remains below the 3,570 lower bound for a
15-minute, 280-character-per-minute Brief. It also reports
`style.editorial_instruction_leakage` when a Source's editing note is copied
into spoken prose. The workflow passes while content acceptance fails. This
validates orchestration, contracts, quality re-entry, and the stopping rule;
it is not evidence of publishable prose.

The guarded E2E driver is read-only unless `--execute` is present:

```bash
# Read-only preflight: no database, artifacts, network request, or paid call.
python -m epiphany.length_recovery_e2e

# Complete zero-cost parent + one explicit child Revision.
python -m epiphany.length_recovery_e2e --provider fake --execute
```

The opt-in paid form is:

```bash
python -m epiphany.length_recovery_e2e \
  --provider deepseek \
  --editor-model deepseek-v4-flash \
  --reviewer-model deepseek-v4-pro \
  --execute
```

It is capped at five parent calls plus two child calls, has no hidden retry,
stores its dedicated database and reports under ignored local paths, and
summarizes parent/child tokens, duration, and currency-grouped estimated cost.
The controlled DeepSeek v2 completed all seven calls with no retry. It grew the
Draft from 1,310 spoken characters (4.68 minutes) to 2,371 (8.47 minutes),
using 86,497 input and 17,496 output tokens in 159,228 ms for a locally
estimated CNY 0.201153. The workflow passed, but content acceptance correctly
failed because the Draft remained short. Current deterministic rules exclude
duration findings when comparing whether other warnings worsened, require an
enumeration marker after words such as “最后”, and detect leaked editorial
instructions. These checks reused the persisted Draft and made no new paid
request. New quality Artifacts identify these semantics as
`draft_quality_rules_v3_editorial_instruction`,
`zh_podcast_style_v2_enumeration_precision`, and
`deterministic_quality_facts_v2_editorial_instruction`; older v1/v2 Artifacts
remain readable.

After a Run has already performed one grounded length recovery,
`prior_length_recovery_attempted=true` is persisted in its Improvement Plan.
If the Draft is still short, the Plan no longer recommends another consecutive
reuse attempt. It recommends targeted supplemental material and a lower
duration preset; reading these options does not create a Run or call a model.
Human recordability review remains pending.

See the
[`M3.8 learning chapter`](../docs/learning/m3-8-grounded-length-recovery.zh-CN.md)
for the beginner explanation, manual API path, event trace, cost boundary, and
stopping rules.

## Debugging and logs

The backend writes one-line JSON logs to stdout. HTTP responses include
`X-Request-ID`; sending the same header lets a caller choose a correlation ID.
Run and Worker logs include stable event names plus `run_id`, `task_id`,
`attempt`, status, and duration where applicable.

```bash
curl -i -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -H 'x-request-id: req_local_debug' \
  -d '{"payload":{"topic":"成年十年"}}'
```

Use database Events to replay what happened in a Workflow. Use stdout logs to
diagnose API latency, Worker ownership, retries, failures, and recovery. Logs
must never include source text, prompts, generated model content, or secrets.
