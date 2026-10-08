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

Graph proposals and scoped evidence-linked memory are implemented. Arbitrary native programs, production authentication, and autonomous learning are not enabled. The model gateway fails visibly when not configured.

## Testing

GitHub Actions runs pure contracts, PostgreSQL integration tests, mock-DSH model execution tests, MCP/UI tests and Nix build checks. Passing the mock bridge tests is not proof of a live DSH/model deployment.