# Implementation status

## Integrated development slice (v0.29)

The canonical architecture remains [architecture.md](architecture.md). The source now implements the first PostgreSQL event and deterministic DAG graph runtime, with the following experimental paths:

- Database-owned versioned memories with direct evidence links and scoped full-text retrieval. Interpretation/contradiction reconciliation is not autonomous.
- An input dispatcher driven by seeded PostgreSQL intent rules, plus validated graph proposals with versioned immutable graph definitions.
- A model node that calls the configured single DSH bridge, with a mock DSH HTTP node server used in CI. The installed DSH runtime and actual model provider must be configured separately.
- One dynamically discovered action catalog used by HTTP, CLI, TUI, web workspace and MCP tool projection.
- The PostgreSQL routing, node scheduling, step fencing, and durable activation state remain the existing runtime's foundation.

## Explicitly not implemented

Full graph synthesis beyond the seed templates; per-event semantic attention/classification; model-driven memory extraction or contradiction resolution; physical OCI arbitrary native workers; mature MCP session handling and MCP Apps; production authentication and per-user authorization; true autonomous lifelong learning.

CI uses a real PostgreSQL service and a deterministic fake DSH model endpoint. An end-to-end mock test does not establish successful live DSH integration or production readiness. The single DSH bundle exports a real ctx.llm.stream bridge, but its integration with a pinned upstream DSH installation still needs a provider-backed deployment test.

## Test

Run `python -m unittest discover -s tests -p 'test_full_system.py' -v` with `LIVING_TEST_DATABASE_URL` set, or review GitHub Actions logs for the PostgreSQL integration job.