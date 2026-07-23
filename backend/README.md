# Epiphany Studio Backend

M1 implements a single-user, single-machine durable task runner. It intentionally
uses a deterministic `FakeProvider`; no OpenAI key or paid model call is needed.

The first workflow is deliberately small:

```text
prepare_sources -> fake_research -> assemble_artifact
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
