# Living Intelligence (sys)

PostgreSQL owns memory, programs and events. The livingd process executes database-defined graphs. **Experimental local development only.**

[Architecture](docs/architecture.md) | [Implementation notes](docs/implementation.md)

## Start locally

Use PostgreSQL 16 and a Python 3.11+ environment:

```sh
python3 -m pip install -r requirements-dev.txt
export LIVING_DATABASE_URL=postgresql://living:living_test_password@127.0.0.1:5432/living
export LIVING_DEV_MODE=1 LIVING_MIGRATE_ON_BOOT=1
python3 scripts/init-local-secrets.py
export LIVING_BOOTSTRAP_TOKEN_FILE="$PWD/.private/living_control_token"
PYTHONPATH=core/livingd python3 -m livingd serve
```

The bootstrap script also writes `.private/living_control_token`. Enter that token in the web UI; generic CLI/TUI clients read it from this file or `LIVING_BOOTSTRAP_TOKEN`. Open http://127.0.0.1:8080 for the generic GUI, run python interfaces/tui/living.py for a TUI, or use interfaces/cli/living.py. The same action catalog is exposed over POST /mcp.

Try `remember apples are fruit`, then `recall apples`. Questions use the models advertised by the configured LiteLLM gateway, never a locally hardcoded model list. Configure the gateway and select models through the dynamically rendered LiteLLM settings and model views in the GUI, or the API below.

Graph proposals, automatic observation-to-memory-candidate routing, human approval, and scoped evidence-linked memory are implemented. An optional independent OCI broker executes Python without network or mounted filesystems; it must run against a separately provisioned worker engine. Production-grade authentication and open-ended autonomous learning are not implemented. Model requests fail visibly when LiteLLM is unavailable or unconfigured.

## Test the full development workflow

1. Start PostgreSQL and the development server above.
2. In the GUI or generic CLI, submit `remember apples are fruit` and then `recall apples`.
3. Send an observed fact via `living observe ...`. The memory-attention graph records a candidate, inspectable at `GET /v1/memory/candidates`.
4. Approve the candidate with `living.approve-memory` using its UUID, then retrieve it as scoped memory.
5. Submit `compose explain this system`. A safe graph is validated and published as a new dynamic Control action, visible to CLI/TUI/GUI/MCP without editing the clients.
6. Configure LiteLLM and choose an advertised model; ask a question to exercise memory → LiteLLM model → view nodes.

Optional physical Python workers run in `workers/oci/broker.py` on a dedicated Docker engine. The broker accepts a bootstrap token, but `livingd` reads its corresponding `worker.token` from the encrypted PostgreSQL credential table. Configure it using the local-only settings API before invoking `living.python`. Do not expose either development service publicly.

## LiteLLM and credential setup

The local bootstrap script also creates `.private/living_seal_key`, which is mounted into the core container. It is an encryption root, **not** a provider credential. PostgreSQL holds the encrypted LiteLLM virtual key and the selected model IDs. The database connection credential and the encryption root must necessarily be available before PostgreSQL credentials can be read.

The local-only configuration API accepts these calls (do not commit keys):

```sh
curl -sS -H "Authorization: Bearer $(cat .private/living_control_token)" http://127.0.0.1:8080/v1/settings/litellm \
  -H 'Content-Type: application/json' \
  -d "{\"base_url\":\"http://litellm:4000\",\"api_key\":\"$LITELLM_VIRTUAL_KEY\"}"
curl -sS http://127.0.0.1:8080/v1/models
curl -sS http://127.0.0.1:8080/v1/models/selection \
  -H 'Content-Type: application/json' \
  -d '{"purpose":"answer","model":"YOUR_DISCOVERED_MODEL_ID"}'
```

The key is AES-256-GCM encrypted with per-record random nonces and scope/name binding before storage. GET endpoints return only metadata, never secret bytes. Do **not** expose this development server beyond loopback; bearer-token authentication is not a replacement for a production identity and network security design or use a shared public host. The key is used only to authenticate with LiteLLM; upstream provider secrets belong to LiteLLM.

For a separate local worker broker, store its bootstrap token through `POST /v1/settings/credentials/worker` using `{"token":"..."}`; the broker itself still needs that token supplied securely at startup.

## Testing

GitHub Actions runs pure contracts, PostgreSQL integration tests, mock-DSH model execution tests, MCP/UI tests and Nix build checks. Passing the mock bridge tests is not proof of a live DSH/model deployment.