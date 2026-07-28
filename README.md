# Epiphany Studio

> A local-first, source-grounded AI workspace for turning journals, voice notes,
> and lived experience into interview scaffolds, podcast scripts, and personal
> essays.

Epiphany Studio is an open-source side project for exploring a question:

**Can an AI system help a person remember, reflect, and create without flattening
their life into generic summaries?**

The first user is the creator of the project. The first product loop is deliberately
small:

```text
source material
  -> evidence-backed extraction
  -> interview scaffold
  -> human reflection
  -> podcast/article draft
  -> human approval
```

## Why this project

This is both a real personal tool and an engineering learning project. It is
intended to exercise:

- parent/child agent orchestration;
- backend API and persistent state;
- bounded parallel execution;
- event streaming and trace UI;
- cancellation, retries, recovery, and idempotency;
- source-grounded memory;
- deployment and observability.

The goal is not to demonstrate the largest number of agents. The goal is to
build the smallest system whose behavior remains understandable and recoverable.

## MVP

The first vertical slice will:

1. Import a small set of journal entries or podcast drafts.
2. Start one persistent `EpisodeRun`.
3. Run two read-only child tasks in parallel:
   - timeline extraction;
   - themes and verbatim-detail extraction.
4. Merge their structured results into a semi-scripted interview scaffold.
5. Pause for human input.
6. Generate a source-grounded draft and show notes.
7. Expose a replayable run/task event trace.

Every extracted claim must point back to a source segment. Generated memories
remain candidates until the user confirms them.

## Intentionally lightweight

The initial implementation will use:

- Python 3.12;
- FastAPI and Pydantic;
- SQLite in WAL mode;
- a vendor-neutral model Provider, with DeepSeek as the first live adapter;
- one in-process durable worker loop;
- `asyncio` for bounded fan-out/fan-in;
- Server-Sent Events for live progress;
- local filesystem storage.

The MVP intentionally does **not** use LangGraph, Temporal, Celery, Redis,
PostgreSQL, Kubernetes, a vector database, or per-agent containers.

Those tools may become justified later, but they should not hide the core
orchestration and reliability concepts during validation.

## Model access and cost

Consumer chat subscriptions and application API usage are separate products.
When a live Provider is enabled, the backend uses that provider's API key and
the provider bills the API account according to its own token prices.

The current default is a deterministic Fake Provider and needs no API key.
M2.3a persists one `ModelCall` record per attempted Provider call, including
status, attempt, provider, model, latency, input/output tokens, estimated cost,
and currency. It enforces the per-Run call limit before entering the Provider.
The Fake Provider reports zero tokens and zero cost, so this accounting and
failure behavior can be tested without a network request.

M2.3b-1 adds a direct `httpx` adapter for the current DeepSeek V4 API plus
Timeline/Theme prompts, strict JSON parsing, error mapping, usage/cost
accounting, input/output bounds, and log redaction. It remains opt-in:
`EPIPHANY_MODEL_PROVIDER=fake` is still the default. Provider HTTP tests use
MockTransport and smoke safety tests use a Fake Provider, so neither incurs API
usage.

M2.3b-2a adds a separate, bounded live-smoke command. Its default mode is a
zero-network preflight; `--execute` is required before it can send requests
using short synthetic material. The current workflow-v3 harness is capped at
three calls—two Researchers followed by one serial Interviewer—fixes the model
to `deepseek-v4-flash`, allows one attempt per model-backed Task, applies
Alembic to a dedicated ignored SQLite trace database, and prints only IDs,
status, tokens, latency, cost, and error codes. M2.3b-2b used the earlier
two-call boundary to complete the first live smoke successfully: both Research
Tasks passed strict validation and fan-in, with an estimated total cost of USD
0.000491.

M2.3b-3 makes DeepSeek cost estimates follow an explicit billing currency.
`EPIPHANY_DEEPSEEK_BILLING_CURRENCY` accepts `USD` or `CNY` and defaults to
`USD` for compatibility. DeepSeek does not return the account billing currency
in a model response, so the backend does not guess from locale, API key, or
balance. Live summaries keep totals grouped by currency instead of adding
unlike amounts. No database migration is required, and existing USD traces
remain historical USD estimates.

No key or personal source material belongs in Git.

Official references:

- [OpenAI: ChatGPT and API billing are separate](https://help.openai.com/en/articles/9039756-managing-billing-settings-on-chatgpt-web-and-platform)
- [DeepSeek API quick start](https://api-docs.deepseek.com/quick_start/pricing-details-usd/)
- [DeepSeek model pricing](https://api-docs.deepseek.com/quick_start/pricing/)

## Repository documents

- [Learning and practice guide](docs/learning/README.zh-CN.md) — beginner-friendly
  explanations, tests, local debugging, and a chapter for every completed slice
- [Product specification](docs/product-spec.zh-CN.md)
- [Architecture](docs/architecture.zh-CN.md)
- [MVP roadmap](docs/roadmap.zh-CN.md)
- [Architecture decision: lightweight orchestration](docs/adr/0001-lightweight-orchestration.zh-CN.md)
- [Development log](docs/devlog.md)

## Status

M1 is complete: the FastAPI API, SQLite runtime tables, three-step Fake
Workflow, durable Worker, retry/cancel/recovery paths, and Alembic migration are
implemented. The full suite passes with 11 tests, and a restart demonstration
confirms that completed Runs remain queryable from SQLite.

The development observability baseline adds JSON stdout logs and an
`X-Request-ID` correlation header. Durable Events remain the workflow trace;
operational logs contain IDs and timing, never imported source text.

M2.1 adds the first product-domain behavior: plain text can be imported as a
local `Source`, deterministically split into ordered `SourceSegment` records,
deduplicated on retry, and queried again after restart. No source content is
sent to a model in this slice.

M2.2 adds the first parent/child Agent workflow without a paid model call. An
`episode-research` Run creates one durable Manager Task and fans out a Timeline
Researcher and Theme Researcher with a maximum concurrency of two. Their strict
outputs must cite only the Source Segments assigned to the Task; quote
candidates must exist verbatim in the cited segment. The Manager waits for both
results and creates one deterministic research bundle. Invalid citations fail
the parent, cancel the sibling, and fence late writes. The full suite passes
with 28 tests.

M2.3a adds the zero-network model-call boundary before any paid integration.
Every Fake Provider attempt now reserves a durable call record, is subject to a
six-call default Run budget, and records terminal status, timing, tokens, and
estimated cost. Retry attempts are counted independently; timeouts and
budget-limit failures remain visible after restart. A new Alembic migration
adds the trace table, and the full suite passes with 32 tests.

M2.3b-1 connects the current DeepSeek V4 OpenAI-compatible contract behind the
same Provider boundary without making a live request. Mock HTTP tests prove the
request shape, both research prompts, usage/cost calculation, retry
classification, timeout accounting, strict source validation, and end-to-end
fan-in. A paid-but-truncated response still records non-zero usage.

M2.3b-2a adds the explicit two-call smoke harness and verifies its dry-run,
call/attempt bounds, isolated trace database, and redacted summary without
network access. M2.3b-2b then completes one explicit live run with synthetic
material: two `deepseek-v4-flash` calls succeed without retry, strict source
validation accepts both results, and deterministic fan-in produces the final
research bundle. The persistent trace records 1,092 input tokens, 1,209 output
tokens, 15,435 ms of combined Provider latency, and USD 0.000491 estimated cost.
M2.3b-3 adds explicit USD/CNY pricing selection and currency-grouped summaries
without rewriting this historical USD trace or changing the database schema.

M2.4 completes the Interview Scaffold slice. Every new `episode-research`
payload must include a non-blank `topic` with its `source_ids` and is stamped
as workflow `v2`. The Timeline and Theme Researchers still fan out in parallel
and produce a deterministic research bundle; only after that bundle exists
does a serial Interviewer run with its own strict schema and prompt. The
scaffold validator rejects extra fields, blank content, topic drift, and any
source reference outside the research bundle. A complete v2 Run therefore has
four Tasks, four Artifacts, and three ModelCalls. Completed scaffolds can be
rendered through `GET /runs/{run_id}/exports/interview-scaffold.md`; the
renderer is deterministic, keeps citations, and escapes Markdown control
syntax and raw HTML. Existing in-flight workflow-v1 Runs retain their original
behavior and finish at the research bundle. This slice needs no database
migration. The Fake Provider remains the zero-token, zero-cost default.

The live-smoke harness now has a three-call ceiling for the v2 shape, but M2.4
was verified only in dry-run mode: no new paid live smoke was performed. The
full suite passes with 99 tests. The historical M2.3b live result above remains
the two-call, 2,301-token, USD 0.000491 trace.

M3.1 adds the first durable human checkpoint. New `episode-research` Runs are
stamped workflow `v3`. After the two parallel Researchers, deterministic
fan-in, and serial Interviewer finish, the Run pauses at
`waiting_for_user / awaiting_interview_response` with four Tasks, four
Artifacts, and three completed ModelCalls. The validated Scaffold can already
be exported while the Run waits, and restarting the backend does not lose the
checkpoint.

Supplemental speech is currently **text**, not live audio. The user imports an
already-transcribed passage through `POST /sources` and passes its Source ID to
`POST /runs/{run_id}/resume`. Resume persists a source-reference-only
`user_material_submission`, supports idempotent network replay, and completes
the M3.1 checkpoint without another Task or model call. It therefore adds no
Token or API cost, and the output remains the Scaffold until the M3.2 Editor is
implemented. v1 and v2 in-flight semantics remain compatible.

M3.1 does not request microphone permission and does not implement recording,
speech-to-text, TTS, voice cloning, a final podcast draft, Show Notes, or a Web
UI. The supported runtime remains local and single-process; database-level
multi-process Resume coordination is deferred to deployment hardening. The
current full suite passes with 113 tests, Ruff lint/format and Alembic
current/check pass, and the guarded DeepSeek validation remained a zero-network
dry run.

## License

[MIT](LICENSE)
