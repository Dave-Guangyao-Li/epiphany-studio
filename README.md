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
- the official OpenAI Python SDK and Responses API;
- one in-process durable worker loop;
- `asyncio` for bounded fan-out/fan-in;
- Server-Sent Events for live progress;
- local filesystem storage.

The MVP intentionally does **not** use LangGraph, Temporal, Celery, Redis,
PostgreSQL, Kubernetes, a vector database, or per-agent containers.

Those tools may become justified later, but they should not hide the core
orchestration and reliability concepts during validation.

## Model access and cost

ChatGPT subscription usage and OpenAI API usage are separate. This project uses
an OpenAI Platform API key, which is billed through the API account at standard
API rates.

The model is configurable. The initial low-cost default is
`gpt-5.6-luna`, with a mock provider for tests. Per-run call limits, parallelism,
token usage, and estimated cost will be recorded.

No key or personal source material belongs in Git.

Official references:

- [OpenAI authentication and API billing](https://learn.chatgpt.com/docs/auth#sign-in-with-an-api-key)
- [OpenAI current model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Responses API](https://developers.openai.com/api/docs/guides/migrate-to-responses)

## Repository documents

- [Product specification](docs/product-spec.zh-CN.md)
- [Architecture](docs/architecture.zh-CN.md)
- [MVP roadmap](docs/roadmap.zh-CN.md)
- [Architecture decision: lightweight orchestration](docs/adr/0001-lightweight-orchestration.zh-CN.md)
- [Development log](docs/devlog.md)

## Status

Planning and architecture baseline. Application code is the next milestone.

## License

[MIT](LICENSE)
