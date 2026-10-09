# Living Intelligence (`sys`)

**PostgreSQL owns memory, executable graphs, policies, credentials, events, goals and their revisions.** `livingd` interprets database-defined execution, LiteLLM supplies available models, and one optional DSH plugin exposes guarded tools. This is an **experimental persistent computational runtime**, not a finished autonomous or self-improving intelligence.

[Architecture](docs/architecture.md) · [Implementation and CI matrix](docs/implementation.md)

## Local development

```sh
python3 scripts/init-local-secrets.py
docker compose -f compose.yaml -f compose.local-db.yaml up --build -d
python3 scripts/smoke.py
```

Open `http://127.0.0.1:8080`; paste the Control token from `.private/living_control_token` in the GUI. The CLI and TUI read it from the same file:

```sh
python3 interfaces/cli/living.py catalog
python3 interfaces/cli/living.py input remember "apples are fruit"
python3 interfaces/cli/living.py input recall apples
python3 interfaces/cli/living.py input goal "Improve this project"
python3 interfaces/tui/living.py
```

The Control API derives operations and views from PostgreSQL. Every input is a durable event; graph outputs and their causal descendants are available from `GET /v1/events/{event_id}`. Scoped admin APIs can publish typed capabilities/graphs without rebuilding the clients. Graphs support durable child activation, limited retries and explicit external-effect reconciliation. Goals can schedule future actions through the same event router.

## Models and integration credentials

In the GUI, open **LiteLLM Connection**, enter a gateway URL and virtual key, then select an advertised model. Only LiteLLM supplies model IDs, through `/v1/models`. The key and model preferences are stored in PostgreSQL; keys are encrypted with the bootstrap key in `.private/living_seal_key`. The DB login and the encryption root necessarily stay outside the encrypted database.

For local API access:

```sh
export LIVING_TOKEN="$(cat .private/living_control_token)"
curl -fsS -H "Authorization: Bearer $LIVING_TOKEN" \
  http://127.0.0.1:8080/v1/models
```

Use the authenticated `POST /v1/settings/litellm` endpoint or GUI to configure the gateway. **This Compose project does not deploy a live LiteLLM instance.** Models must already be configured on your gateway.

The single `plugins/dsh/living` bundle disables the stock DSH ReAct agent loop inside the Living profile and exposes guarded DSH tool calls. Full DSH installation and real provider integration remain **unverified**. Python OCI workers use the separate `workers/oci/broker.py` service with a **dedicated** Docker engine; do not expose the host socket to `livingd`.

## Scope and safety

A local bearer token maps to database-held principal, scope and grants. Keep the Control API on loopback: this is development authentication, **not production multi-tenant isolation**. Do not commit keys or control tokens. Run `python3 scripts/smoke.py` to check the local HTTP/PostgreSQL path.

Observations can produce evidence-backed memory candidates; optional LiteLLM extraction is enabled through a database-owned attention policy, and claim promotion requires approval. Contradictory structured claims are preserved. Real semantic consolidation, autonomous planning, mobile/native renderers and production security remain future work.

CI verifies PostgreSQL execution, concurrency/restart/fault tests, an actual LiteLLM 1.95.0 proxy connected to a deterministic local provider, mocked DSH tool contracts, physical OCI sandbox behavior and Nix container boot. **The proxy test verifies LiteLLM routing, not a live third-party model account or an entirely deployed intelligent system.** See [implementation.md](docs/implementation.md) for exact coverage.
