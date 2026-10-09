# Implementation and verification status

Source and CI, not the conceptual design, determine what is working. This is a development runtime.

| Layer | Implemented | Verification / limits |
|---|---|---|
| Event processing | Transactional ingress, deduplication, subscribed routing, scoped delivery, causal lineage | Real PostgreSQL; no generalized predicate/attention scorer |
| Graph executor | DB capability registry and immutable snapshots; typed graphs, grant checks, lease renewal/fencing, bounded retry, cancellation | Real PostgreSQL, multiple executor threads, fresh subprocess, fault injection |
| Logical workers | Nested `graph.call` activations, durable child output and parent continuation | PostgreSQL nested test; bounded depth, no independent agent roles |
| External effects | OCI Python broker with no-network sandbox; effect ledger, uncertainty and manual reconciliation | Real Docker isolation; no automatic exactly-once effect guarantee |
| Models | LiteLLM `/v1/models` discovery and text chat; encrypted DB key and model policy | Real LiteLLM 1.95.0 proxy + deterministic fake upstream; **no live external provider** |
| DSH | Single guarded DSH tool bridge; profile patch disables stock agent loop | Mocked `ctx.tools.execute`; **no verified upstream DSH installation** |
| Memory | DB text search, evidence, typed entities/relations/conflicts, optional model candidate extraction + approval | PostgreSQL+mock inference; semantic embeddings, advanced consolidation absent |
| Control | Bearer principals/grants; scoped action catalog; typed capability & graph publication | API/integration tests; one-text-action constraint and local-only auth |
| Interfaces | Dynamic CLI/TUI/Web view, MCP resources/tools and durable task handles | Automated clients; no broad MCP host/mobile renderer interoperability |
| Goals | Persisted goals and one-time scheduled wake events | PostgreSQL test; no goal-generating autonomous planner |

## Running the development stack

```sh
python3 scripts/init-local-secrets.py
docker compose -f compose.yaml -f compose.local-db.yaml up --build -d
python3 scripts/smoke.py
```

Open `http://127.0.0.1:8080` and paste the local Control token from `.private/living_control_token` in the GUI. CLI/TUI clients read that file. Keep the server on loopback. The generated `.private/living_seal_key` decrypts PostgreSQL-stored integration keys; the DB connection string is also a bootstrap secret.

The GUI configures a LiteLLM gateway and discovers its models. LiteLLM itself is **not launched by this Compose file**. DSH must be installed and its tool bridge configured separately. Do not pass Docker's host socket to `livingd`; the OCI broker must be isolated on another engine.

### Control contract

- `POST /v1/inputs`: create user event; `GET /v1/events/{id}`: inspect causal descendants, activation states and terminal result.
- `GET /v1/control/catalog`: scope-and-grant filtered actions; `POST /v1/control/capabilities` and `POST /v1/control/graphs`: scoped admin definitions.
- `GET /v1/control/handles/{id}`, `POST /v1/control/handles/{id}/cancel`: inspect/cancel durable work.
- `GET|POST /v1/goals`, `POST /v1/goals/{id}/cancel`, `GET|POST /v1/schedule`: durable activity and wakeups.
- `GET|POST /v1/settings/litellm`, `GET /v1/models`, `GET|POST /v1/models/selection`: database-owned inference settings.
- `GET|POST /v1/memory/attention/policy`, `GET /v1/memory/candidates`, `GET /v1/memory/conflicts`: evidence review.
- `GET /v1/control/effects`, `POST /v1/control/effects/{id}/reconcile`: explicit resolution of uncertain external effects.
- `POST /mcp`: stateless agent projection; MCP Tasks are scoped persistent handles.

## CI gates

A merge requires all checks green on **the same PR SHA**, then separately on the resulting `main` commit:

1. Pure contracts, authenticated HTTP smoke, PostgreSQL migrations and integration tests (event/delivery, scoped access, dynamic graph registration, causally aggregated outputs, memory, goal wakeup, nested graph, long-execution leases, stale worker, restart, concurrency, MCP task/cancel and effect reconciliation).
2. Real Docker/OCI sandbox smoke test.
3. Nix package and OCI image boot smoke test.
4. Real LiteLLM proxy launch, encrypted PostgreSQL gateway credential, advertised model selection and graph-backed completion via a deterministic OpenAI-compatible upstream.

The LiteLLM **proxy itself is genuine** and its upstream response is deterministic; the DSH tool host remains mocked. Passing these jobs does **not** establish a connected third-party model provider, deployed upstream DSH host, native interfaces, general semantic learning or production security. The proxy CI lane pins LiteLLM 1.95.0 to FastAPI 0.140.0 because newer FastAPI removed an API needed by that LiteLLM release.

## Remaining engineering and research

Production identity/key rotation, tenant-aware resource/cost budgets and audit; richer graph matching/validation and multimodal schemas; robust async effect idempotency/reconciliation; live upstream model/DSH integration; persistent semantic memory indexing and consolidation; universal interface renderer; open-ended goal composition and self-directed improvement. These are not implemented, regardless of other tests passing.
