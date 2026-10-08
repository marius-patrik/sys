# Living Intelligence (`sys`)

Persistent, database-resident computational architecture. **Current implementation: v0.28 deterministic vertical slice**, not a general agent, semantic-memory runtime, or production server.

- [Architecture and execution contracts](docs/architecture.md)
- [Implemented features and limitations](docs/implementation.md)
- [Deterministic contracts](contracts/)
- [PostgreSQL migration](db/migrations/0001_vertical_slice.sql)

## Development

The initial implementation requires Python 3.11+, `psycopg` 3, and PostgreSQL 16+. It exposes an **unauthenticated development-only API**, bound to loopback; do not deploy publicly.

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m unittest discover -s tests -p 'test_engine_*.py' -v
python3 contracts/test_contracts.py

export LIVING_TEST_DATABASE_URL='postgresql://living:living_test_password@127.0.0.1:5432/living'
python3 -m unittest discover -s tests -p 'test_postgres_integration.py' -v
```

The integration test applies migrations, seeds a deterministic graph, ingests a request, routes its event, executes two graph nodes, checks idempotency, and inspects committed results. It requires a real PostgreSQL connection; CI provides one.

For Docker/Nix bootstrap, see [implementation instructions](docs/implementation.md) and `compose*.yaml`. Generate `flake.lock` before building; the flake currently targets `x86_64-linux`.

## CI

GitHub Actions runs pure contract tests and actual PostgreSQL 16 integration tests on pushes and PRs. A separate Nix job evaluates and builds the core derivation. These checks do **not** certify the missing DSH, semantic memory, MCP, or dynamic UI capabilities.
