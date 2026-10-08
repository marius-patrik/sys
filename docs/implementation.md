# Implementation status

## Integrated development slice (v0.29)

The canonical architecture remains [architecture.md](architecture.md). The source now implements the first PostgreSQL event and deterministic DAG graph runtime, with the following experimental paths:

- Database-owned versioned memories with direct evidence links, scoped full-text retrieval, and event-triggered attention leading to reviewable candidates. Interpretation and contradiction adjudication are not autonomous.
- An input dispatcher driven by seeded PostgreSQL intent rules, plus validated, read-only graph proposals published as dynamic actions with immutable revisions.
- A model node calls a PostgreSQL-configured LiteLLM gateway; CI uses a mock LiteLLM `/v1/models` and `/v1/chat/completions` service.
- One dynamically discovered action catalog used by HTTP, CLI, TUI, web workspace and MCP tool projection.
- A separate OCI broker can execute Python without networking or filesystem mounts, with a real Docker isolation CI job; the core never mounts the deployment engine socket.
- The PostgreSQL routing, node scheduling, step fencing, and durable activation state remain the existing runtime's foundation.

## Explicitly not implemented

Unbounded/general-purpose graph synthesis (the model-assisted composer is type- and capability-restricted); semantic model-based memory extraction and contradiction adjudication; general multi-language OCI toolchains (Python execution through a separate broker is implemented); mature MCP session handling and MCP Apps; production authentication and per-user authorization; autonomous lifelong learning.

CI uses real PostgreSQL and a mock LiteLLM model gateway. It verifies model discovery, selection and encrypted key storage, but does not establish that a real LiteLLM provider is connected. The single DSH bundle is an optional tool bridge; provider credentials do not reside in DSH.

## Test

Run `python -m unittest discover -s tests -p 'test_full_system.py' -v` with `LIVING_TEST_DATABASE_URL` set, or review GitHub Actions logs for the PostgreSQL integration job.
## Live smoke test

With the development core running, use `python scripts/smoke.py`. The same test runs in CI with real PostgreSQL and verifies HTTP, memory, graph composition, the Control catalog, MCP and web serving.

## LiteLLM model nodes and credentials

A model invocation reads the scope's LiteLLM gateway and encrypted virtual key from PostgreSQL, discovers available model IDs from the proxy, chooses the stored model policy for the task, and calls LiteLLM directly. The GUI provides a database-defined gateway form and model selection view; the local Control endpoints are described in the root README.

The optional single DSH plugin executes tool calls through the DSH tool runtime, not model inference. CI verifies that boundary using a mocked DSH tool registry. The encryption root and database connection string remain bootstrap secrets outside the encrypted database. Real LiteLLM deployment and production authentication remain unverified.
