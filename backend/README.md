# Epiphany Studio Backend

The backend currently implements a single-user, single-process durable task
runner through the M3.1 human checkpoint, including parallel research,
model-call traces, a serial Interviewer, durable `waiting_for_user`, source-ID
based Resume, and deterministic Markdown export. M2.3b-1 also includes an
opt-in DeepSeek V4 adapter. The default remains the deterministic
`FakeProvider`, which reports zero tokens and zero cost, so setup, Swagger, and
the default test suite need no API key, network request, or paid model call.

The first workflow is deliberately small:

```text
prepare_sources -> fake_research -> assemble_artifact
```

The current `episode-research` workflow is:

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
  -> complete the M3.1 checkpoint (no new model call)
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

The current full suite passes with 130 tests.

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

## Current episode-research API (workflow v3)

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
with `workflow_version: "v3"`. The response initially contains a running
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

To simulate a spoken follow-up in M3.1, type or paste the **already-transcribed
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

The first valid call returns `resumed: true`, finishes the M3.1 checkpoint, and
adds one `user_material_submission` Artifact containing Source/Segment
references rather than transcript text. It keeps `model_call_count: 3`, so
Resume adds no Token or API cost. Repeating the exact request returns
`idempotent_replay: true` without another Artifact or Event. Reusing the same
submission ID with different Sources returns HTTP 409.

The Fake Provider creates deterministic, source-grounded regression output
without a paid call. It extracts topic-relevant sentences, dates, themes, and
quotes from the SourceSegments assigned to each Task; the guarded E2E supplies
the committed synthetic fixture. The exported Scaffold is therefore readable
during manual testing. It is still not a model-quality benchmark: its main job is to
validate bounded parallel research, the sequential Interviewer, strict output,
citation scope, failure propagation, idempotent transitions, and late-result
fencing.

Focused verification:

```bash
pytest tests/test_research_schemas.py \
       tests/test_research_workflow.py \
       tests/test_interview_scaffold.py \
       tests/test_interview_export_api.py -q
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
EPIPHANY_DEEPSEEK_MAX_SOURCE_CHARS=24000
EPIPHANY_DEEPSEEK_MAX_INTERVIEW_BUNDLE_CHARS=24000
```

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

M2.4 changed the current harness ceiling from two calls to three; M3.1 keeps
that ceiling and expects workflow v3 to stop at `waiting_for_user`. M2.4 itself
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
a valid scaffold and all referenced source metadata are available. Current v3
Runs may export it while `waiting_for_user`; v2 Runs may export it after
`succeeded`.

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
The complete backend suite currently contains 130 tests.

## M3.1 backend E2E

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
`data/checkpoint-e2e.db` and `artifacts/checkpoint-e2e/`. The machine-readable
report summarizes Run/Task/Artifact/ModelCall state, events, tokens, estimated
cost, redaction checks, and failures. The exported file is still an Interview
Scaffold; M3.2 will extend this path to a podcast draft and Show Notes.

The current realistic fixture contains three coherent initial Sources plus one
complete supplemental transcript. Researcher source input is capped at 8,000
characters; the validated merged research Bundle has a separate 24,000
character ceiling before the Interviewer. This distinction was added after a
real run completed both Researchers but rejected the larger Bundle before the
third network request.

The corrected bounded DeepSeek run
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

See
[`docs/learning/m3-1-backend-e2e.zh-CN.md`](../docs/learning/m3-1-backend-e2e.zh-CN.md)
for repeatable commands, and
[`docs/learning/m3-1-realistic-e2e-evidence.zh-CN.md`](../docs/learning/m3-1-realistic-e2e-evidence.zh-CN.md)
for the failure analysis, successful live evidence, cost table, and content
review.

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
