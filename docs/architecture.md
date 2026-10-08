# Living Intelligence — Executable Architecture

**8 October 2026.** PostgreSQL owns the system's durable intelligence; `livingd` is its replaceable graph executor. This is an experimental computational substrate, **not an implemented general intelligence**. See [implementation.md](implementation.md) for tested functionality and unresolved gaps.

## Ownership and interfaces

PostgreSQL owns events, causal history, knowledge, evidence, goals, schedules, graph and capability revisions, scopes, grants, executions, leases, external-effect states, encrypted credentials and interface definitions. The database is a peer process and may run remotely. In-memory executor caches are disposable.

`livingd` exposes one authenticated **Control API**. CLI, TUI, GUI and MCP clients discover authorized database-defined actions and views. The current action convention accepts one text input and typed graph outputs; the longer-term target is complete JSON Schema-to-interface projection. The GUI renders database-defined component trees, though new presentation primitives still require a renderer.

LiteLLM is the **sole source of model IDs and inference**. `livingd` discovers available models via `/v1/models`, reads scoped model selections and an encrypted LiteLLM key from PostgreSQL and invokes the OpenAI-compatible completion API. The DB DSN and encryption root are bootstrap secrets kept outside that encrypted database. The optional single `dsh-living` plugin contributes guarded tools; the Living profile disables DSH's stock `agent-loop`. Physical Python programs run through an isolated OCI broker with its own Docker engine, not the core's Docker socket.

## How events become work

1. **Ingest:** authenticated input, external observation or internal result is validated and committed as a durable event with scope, source idempotency key and optional causal parent; a PostgreSQL trigger queues routing atomically.
2. **Route:** the executor uses `FOR UPDATE SKIP LOCKED` to claim a routing row, matches database subscriptions by event kind, resolves the scoped Control action and pins the immutable graph revision in per-subscriber deliveries.
3. **Deliver:** the graph is type-checked against database capability definitions; the originating principal and required grants are checked. An activation pins scope, root event, grants, graph and capability snapshots. Invalid deliveries fail durably rather than executing.
4. **Attend:** selected observations activate versioned memory attention policies. The initial filter distinguishes short/known observations from evidence candidates. A policy can optionally invoke LiteLLM for bounded structured candidate extraction.
5. **Execute:** the graph scheduler leases nodes whose dependencies are complete. Each node calls a registered adapter, outside a transaction. Independent ready nodes may execute concurrently. Results and generated events commit together with a fencing check.
6. **Continue:** new events re-enter routing. Persisted `scheduled_events` become `wake.due` when eligible. The system can become quiescent when no work is due.

Event IDs represent identity rather than commit order. Routing and deliveries—not ephemeral notifications—are the durable queues. A process restart reads them from PostgreSQL.

## How graph nodes execute

A graph is an immutable typed DAG of capabilities. Each node binds literal, input or predecessor-result ports, with validated connections. A capability is a database record containing typed ports, adapter family, effect classification, required grants and revision. The executor provides generic adapter interpreters: pure expressions, SQL memory, LiteLLM inference, DSH guarded tools, OCI program, Control dispatch, graph composer, nested graph and goals. Registering a new capability *using an existing adapter family* requires no source change. Arbitrary code injection is not a supported adapter.

`livingd` leases a ready node for 90 seconds and renews it during long operations. Only the current epoch may commit completion or failure. Failed read/inference nodes get bounded backoff/retry; external effects are never blindly replayed. Their ledger records `running`, `committed` or `uncertain`. An uncertain result suspends its activation until explicitly reconciled by an authorized principal. Cancellation affects active child graphs and marks running external effects uncertain.

A `graph.call` node validates an authorized child graph, creates a nested activation with inherited grants and a depth bound, then waits durably. Its worker is free while the child runs. On child completion the parent resumes from the persisted output. Root event queries resolve the causal descendants, so the user sees the final answer rather than a mere dispatch acknowledgement.

## Graph composition

The composer first searches the scope-visible `graph_catalog` for a reusable typed graph. A bounded LiteLLM request may instead propose a graph using only capabilities in the exposed manifest; the validator rejects unknown ports, cycles, unauthorized effects and out-of-budget structures. Accepted graphs become immutable scoped revisions and discoverable Control actions. The **current matching implementation is a simple lexical heuristic**, not a full planner or generalized graph synthesizer.

## Database-owned memory

Event history records *what happened*. Memory records *claims supported by evidence*. PostgreSQL stores normalized `memory_claims`, `memory_evidence`, pending `memory_candidates`, `memory_entities`, subject/predicate/object relationships, and `memory_conflicts`. A model can extract candidate claims but cannot automatically establish them as truth. A scoped approval operation links the accepted claim to its original observation. Conflicting object assertions sharing a subject and predicate are preserved and marked disputed rather than overwritten.

Memory retrieval is scope-filtered PostgreSQL full-text search with bounded context and evidence/revision information. The requesting graph decides when to inject retrieved context into a model invocation. Embeddings, temporal evidence calibration, robust entity resolution, consolidation and autonomous distillation remain future work.

## Goals and event-driven autonomy

`goal_units` persists project goals independently of user turns. Authorized operations schedule future Control actions in `scheduled_events`, tied to a goal. The executor claims due rows and emits exactly one idempotent `wake.due` event; cancelled goals cancel pending timers. This makes persistence and asynchronous activity possible but **does not provide autonomous planning, continual self-directed learning or sentience**.

## Security and verification contract

Bearer tokens map to database principals, scopes and grants. Admins can register typed capabilities and graphs, manage database-encrypted credentials, and inspect/reconcile effects. Development tokens and loopback binding do not replace production identity, isolation, auditing or cost budgets.

GitHub Actions uses real PostgreSQL, fault-injected stale leases, subprocess restarts, concurrent workers, a real OCI sandbox and Nix-built image; model and DSH services are tested via protocol-compatible mocks. **Real model-provider and upstream DSH installation tests require a separate provisioned environment**. Never claim full-system readiness from a green mock-only run.
