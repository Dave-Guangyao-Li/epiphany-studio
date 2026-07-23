# Epiphany Studio Backend

The backend currently implements a single-user, single-machine durable task
runner plus the M2.2 parallel research workflow. It intentionally uses a
deterministic `FakeProvider`; no OpenAI key or paid model call is needed.

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
Orchestrator what may run next. `model_call_count` counts provider invocations in
M1, even though the provider is fake and makes no network or paid model call.

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
