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
