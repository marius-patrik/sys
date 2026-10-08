"""PostgreSQL adapter. The database is authoritative, including executable graphs."""
import json
import os
from pathlib import Path


def connection_string():
    filename = os.environ.get("LIVING_DATABASE_URL_FILE")
    if filename:
        return Path(filename).read_text().strip()
    return os.environ["LIVING_DATABASE_URL"]


def connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(connection_string(), row_factory=dict_row, connect_timeout=5)


def migrate():
    migration_dir = Path(__file__).resolve().parents[3] / "db" / "migrations"
    if not migration_dir.is_dir():
        migration_dir = Path(os.environ["LIVING_MIGRATIONS_DIR"])
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for p in sorted(migration_dir.glob("*.sql")):
            if db.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (p.name,)).fetchone():
                continue
            db.execute(p.read_text())
            db.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (p.name,))


def seed():
    # The seed is idempotent, but it never overwrites user-modified definitions.
    with connect() as db:
        for graph in SEED_GRAPHS:
            from .logic import validate_graph
            validate_graph(graph["definition"])
            db.execute("INSERT INTO graph_revisions(id, definition) VALUES (%s, %s::jsonb) ON CONFLICT DO NOTHING", (graph["id"], json.dumps(graph["definition"])))
        for action in SEED_ACTIONS:
            db.execute("INSERT INTO control_actions(id, title, graph_revision, input_name) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", action)
        db.execute("""
            INSERT INTO control_views(id,definition)
            VALUES ('interface.root', %s::jsonb) ON CONFLICT DO NOTHING
        """, (json.dumps({"type":"container","title":"Living Intelligence",
            "children":[{"type":"action-list","source":"control.catalog"},
                        {"type":"activity-list","source":"control.activations"}]}),))
        for event_kind, action_id in [ ("surface.input", "living.echo"), ("control.action", None) ]:
            db.execute("INSERT INTO subscriptions(id, event_kind, action_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (f"bootstrap.{event_kind}", event_kind, action_id))


SEED_GRAPHS = [
    {"id": "bootstrap.echo.1", "definition": {
        "inputs": {"text": "text"}, "nodes": [
            {"id": "echo", "capability": "text.echo", "inputs": {"value": {"input": "text"}}},
            {"id": "view", "capability": "view.text", "inputs": {"value": {"node": "echo", "port": "value"}}}
        ], "outputs": {"view": {"node": "view", "port": "view"}}}},
    {"id": "bootstrap.upper.1", "definition": {
        "inputs": {"text": "text"}, "nodes": [
            {"id": "upper", "capability": "text.upper", "inputs": {"value": {"input": "text"}}},
            {"id": "view", "capability": "view.text", "inputs": {"value": {"node": "upper", "port": "value"}}}
        ], "outputs": {"view": {"node": "view", "port": "view"}}}}
]
SEED_ACTIONS = [
    ("living.echo", "Echo text", "bootstrap.echo.1", "text"),
    ("living.upper", "Uppercase text", "bootstrap.upper.1", "text"),
]
