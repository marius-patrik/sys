# Implementation status

## Integrated development slice (v0.29)

The canonical architecture remains [architecture.md](architecture.md). The source now implements the first PostgreSQL event and deterministic DAG graph runtime, with the following experimental paths:

- Database-owned versioned memories with direct evidence links, scoped full-text retrieval, and event-triggered attention leading to reviewable candidates. Interpretation and contradiction adjudication are not autonomous.
- An input dispatcher driven by seeded PostgreSQL intent rules, plus validated, read-only graph proposals published as dynamic actions with immutable revisions.
- A model node that calls the configured single DSH bridge, with a mock DSH HTTP node server used in CI. The installed DSH runtime and actual model provider must be configured separately.
- One dynamically discovered action catalog used by HTTP, CLI, TUI, web workspace and MCP tool projection.
- A separate OCI broker can execute Python without networking or filesystem mounts, with a real Docker isolation CI job; the core never mounts the deployment engine socket.
- The PostgreSQL routing, node scheduling, step fencing, and durable activation state remain the existing runtime's foundation.

## Explicitly not implemented

Unbounded/general-purpose graph synthesis (the model-assisted composer is type- and capability-restricted); semantic model-based memory extraction and contradiction adjudication; general multi-language OCI toolchains (Python execution through a separate broker is implemented); mature MCP session handling and MCP Apps; production authentication and per-user authorization; autonomous lifelong learning.

CI uses a real PostgreSQL service and a deterministic fake DSH model endpoint. An end-to-end mock test does not establish successful live DSH integration or production readiness. The single DSH bundle exports a real ctx.llm.stream bridge, but its integration with a pinned upstream DSH installation still needs a provider-backed deployment test.

## Test

Run `python -m unittest discover -s tests -p 'test_full_system.py' -v` with `LIVING_TEST_DATABASE_URL` set, or review GitHub Actions logs for the PostgreSQL integration job.