# Epiphany Studio Backend

The backend currently implements a single-user, single-machine durable task
runner plus the M2.2 parallel research workflow and the M2.3a model-call trace.
M2.3b-1 also includes an opt-in DeepSeek V4 adapter. The default remains the
deterministic `FakeProvider`, so setup, Swagger, and the default test suite need
no API key, network request, or paid model call.

The first workflow is deliberately small:

```text
prepare_sources -> fake_research -> assemble_artifact
```

The first parent/child workflow is:

```text
research_manager
  -> fan-out (maximum 2)
       |- timeline_research
       `- theme_research
  -> validate strict schemas and Source references
  -> fan-in
  -> episode_research_bundle
```

Each step is a persisted Task. The worker claims it with a lease, writes an
append-only Event, commits an idempotent Artifact, and asks the deterministic
Orchestrator what may run next. Before entering the Provider, the Worker reserves
a durable `ModelCall`. The Fake Provider exercises the exact same accounting
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

## M2.2 parallel research API

Import a Source as shown above, copy its `source.id`, and start a Run:

```bash
curl -i -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -H 'x-request-id: req_research_demo' \
  -d '{
    "workflow_type": "episode-research",
    "payload": {"source_ids": ["src_REPLACE_ME"]}
  }'
```

The response initially contains a running `research_manager` and two queued
children with the Manager's ID in `parent_task_id`. With the default Worker
enabled, poll the returned Run ID:

```bash
curl http://127.0.0.1:8000/runs/run_REPLACE_ME
curl http://127.0.0.1:8000/runs/run_REPLACE_ME/events
```

The completed Run has:

- three succeeded Tasks: one Manager and two sibling Researchers;
- two validated child result Artifacts plus one `episode_research_bundle`;
- `model_call_count: 2`, representing two Fake Provider executions;
- durable `workflow.fan_out.started`, `workflow.fan_in.waiting`, and
  `workflow.fan_in.completed` Events.

The Fake Provider is not pretending to provide useful AI research. It validates
the orchestration contract before a real provider is introduced: true bounded
parallel execution, strict structured output, citation scope, exact quote
matching, failure propagation, idempotent fan-in, and late-result fencing.

Focused verification:

```bash
pytest tests/test_research_schemas.py tests/test_research_workflow.py -q
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
estimated USD micros to the existing ledger.

It is disabled by default. The committed example remains:

```env
EPIPHANY_MODEL_PROVIDER=fake
EPIPHANY_DEEPSEEK_API_KEY=
EPIPHANY_DEEPSEEK_MODEL=deepseek-v4-flash
EPIPHANY_DEEPSEEK_MAX_TOKENS=2000
EPIPHANY_DEEPSEEK_MAX_SOURCE_CHARS=24000
```

Focused zero-network verification:

```bash
pytest tests/test_deepseek_provider.py \
       tests/test_deepseek_research_workflow.py -vv
```

The Provider HTTP tests use `httpx.MockTransport`; they do not read the local
API key or contact DeepSeek. They cover successful dual research, 429 retry
accounting, terminal authentication failure, timeout status, invalid
citations, response usage/cost, and secret/content log redaction.

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
- two model calls maximum;
- one attempt per child Task;
- one in-flight request, so an early failure can cancel the second call;
- 800 output tokens maximum per call;
- expected total cost below USD 0.01, as an estimate rather than a billing
  guarantee.

To perform the intentional live check, put the key only in ignored
`backend/.env`:

```env
EPIPHANY_DEEPSEEK_API_KEY=your-local-key
```

Then run:

```bash
python -m epiphany.live_deepseek_smoke --execute
```

The command applies Alembic to the dedicated ignored
`data/deepseek-live-smoke.db`, imports a short synthetic Source, runs the two
Researcher Tasks, and exits successfully only if both ModelCalls and the final
fan-in succeed. It prints IDs, task/call status, tokens, duration, estimated
cost, and artifact kinds. It does not print the key, Prompt, source text,
generated content, or error response body. No FastAPI server or Swagger page is
needed.

Focused zero-network safety verification:

```bash
pytest tests/test_live_deepseek_smoke.py -vv
```

If the live command fails, start with the final `run.id`, Task `error_code`,
ModelCall status, and the structured logs. A 401 usually means an invalid key,
402 means insufficient API balance, and network/timeout failures leave a
durable trace in the dedicated database.

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
