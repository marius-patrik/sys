# v0.28 implementation map

This is **real source code for the first deterministic vertical slice**, not a design revision. It is not deployed or PostgreSQL-validated in the authoring environment.

| v0.27 contract | Implementation |
|---|---|
| PostgreSQL is authoritative | `db/migrations/0001_vertical_slice.sql`; graphs, subscriptions, events, activations, Control actions/views and runs |
| Source-idempotent event ingestion | `engine.ingest()`; `(source,source_id)` uniqueness and payload-mismatch rejection |
| Event enqueue atomic with event commit | `AFTER INSERT events` trigger inserts `event_routing` |
| Durable subscription routing | `engine.route_once()`; `SKIP LOCKED`, transactionally pinned action and graph revisions |
| Event delivery → graph activation | `engine.deliver_once()`; deterministic activation UUID, one transaction |
| Immutable graph definitions | SQL trigger prevents graph-revision UPDATE/DELETE |
| Graph typing and dependencies | `logic.validate_graph()`, `due_nodes()`, `node_inputs()` |
| Bounded executor with fencing | `engine._claim_node()`, `node_once()`; 20-second node lease and epoch predicate on completion |
| Atomic causal completion records | Node output and `node.completed` insert share one PostgreSQL commit |
| Dynamic Control actions/views | Database rows → `GET /v1/control/catalog` and `/views/{id}` |
| Generic client | `interfaces/cli/living.py` discovers catalog; no per-action command registry |
| Observability | `GET /v1/events/{id}`, `/v1/control/handles/{id}` and snapshot-based development SSE |

## Explicitly absent

This slice does not execute or claim to validate: score-based attention for optional observations, semantic memory, graph search/model-driven composition, branches, typed side effects, outbox, real authentication, PostgreSQL row-level tenant security, DSH/LiteLLM, independent OCI engine, GUI/TUI/MCP, cross-instance failover and production-grade subscriber streaming. The v0.27 reference-policy tests are preserved under `contracts/` but not wired into this runtime. It would be incorrect to present their passing result as proof that the policy runs.

The implementation intentionally supports only input/control events and read-only deterministic operations, to validate durable event → subscription → activation → node → committed result before expanding the runtime. Its runtime is a Python bootstrap packaged by Nix, replacing the former nonfunctional Rust health-server stub for this isolated slice; no inference that the production language has been definitively selected.

## Gates

**Static/unit test gate:** execute `python3 -m unittest discover -s tests -p 'test_engine_*.py' -v` and `python3 contracts/test_contracts.py`.

**Real database gate:** provide PostgreSQL 16+, install `psycopg` 3 or enter Nix dev shell, set `LIVING_TEST_DATABASE_URL`, and run `python3 -m unittest discover -s tests -p 'test_postgres_integration.py' -v`. Both tests must execute rather than skip. Test idempotent ingest, routed activation, two committed nodes, emitted events, final view, and durable replay. Then run a real Compose/Nix build and test service restarts. Those gates cannot be marked green until an actual server/build environment is available.

**Next implementation increment:** PostgreSQL-owned memory tables/queries and explicit versioned memory-attention subscriptions, then the graph composer and single DSH execution plugin. Implement real auth and effect authorization before any public deployment.
