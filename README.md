# Living Intelligence (sys)

PostgreSQL owns memory, programs and events. The livingd process executes database-defined graphs. **Experimental local development only.**

[Architecture](docs/architecture.md) | [Implementation notes](docs/implementation.md)

## Start locally

Use PostgreSQL 16 and a Python 3.11+ environment:

```sh
python3 -m pip install -r requirements-dev.txt
export LIVING_DATABASE_URL=postgresql://living:living_test_password@127.0.0.1:5432/living
export LIVING_DEV_MODE=1 LIVING_MIGRATE_ON_BOOT=1
PYTHONPATH=core/livingd python3 -m livingd serve
```

Open http://127.0.0.1:8080 for the generic GUI, run python interfaces/tui/living.py for a TUI, or use interfaces/cli/living.py. The same action catalog is exposed over POST /mcp.

Try `remember apples are fruit`, then `recall apples`. Asking a general question requires a configured DSH node bridge (LIVING_DSH_URL and LIVING_DSH_TOKEN). The single DSH plugin lives at plugins/dsh/living.

Graph proposals, automatic observation-to-memory-candidate routing, human approval, and scoped evidence-linked memory are implemented. An optional independent OCI broker executes Python without network or mounted filesystems; it must run against a separately provisioned worker engine. Production authentication and open-ended autonomous learning are not implemented. Model requests fail visibly when DSH is unavailable.

## Test the full development workflow

1. Start PostgreSQL and the development server above.
2. In the GUI or generic CLI, submit `remember apples are fruit` and then `recall apples`.
3. Send an observed fact via `living observe ...`. The memory-attention graph records a candidate, inspectable at `GET /v1/memory/candidates`.
4. Approve the candidate with `living.approve-memory` using its UUID, then retrieve it as scoped memory.
5. Submit `compose explain this system`. A safe graph is validated and published as a new dynamic Control action, visible to CLI/TUI/GUI/MCP without editing the clients.
6. With a DSH bridge connected, ask a question to exercise memory→DSH model→view nodes.

Optional physical Python workers run in `workers/oci/broker.py`, with `LIVING_WORKER_TOKEN` (at least 12 characters) and a Docker daemon dedicated to untrusted workloads. Start the broker locally, set `LIVING_WORKER_URL=http://127.0.0.1:8091` and the same token for `livingd`, then invoke `living.python` with Python source. Do not expose either development API or broker publicly.

## Testing

GitHub Actions runs pure contracts, PostgreSQL integration tests, mock-DSH model execution tests, MCP/UI tests and Nix build checks. Passing the mock bridge tests is not proof of a live DSH/model deployment.