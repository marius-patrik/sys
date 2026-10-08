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
        for rule in SEED_INTENT_RULES:
            db.execute("INSERT INTO intent_rules(id,prefix,action_id,priority) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", rule)
        for entry in SEED_GRAPH_CATALOG:
            db.execute("INSERT INTO graph_catalog(revision_id,description,tags) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", entry)
        for event_kind, action_id in [ ("surface.input", "living.dispatch"), ("control.action", None) ]:
            db.execute("INSERT INTO subscriptions(id, event_kind, action_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (f"bootstrap.{event_kind}", event_kind, action_id))
        # Upgrade only the unmodified legacy v0.28 dispatcher.
        db.execute("UPDATE subscriptions SET action_id='living.dispatch', revision=revision+1 WHERE id='bootstrap.surface.input' AND action_id='living.echo'")


SEED_GRAPHS = [
    {"id":"bootstrap.echo.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"echo","capability":"text.echo","inputs":{"value":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"echo","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}},
    {"id":"bootstrap.upper.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"upper","capability":"text.upper","inputs":{"value":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"upper","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}},
    {"id":"bootstrap.dispatch.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"dispatch","capability":"input.dispatch","inputs":{"value":{"input":"text"}}}
        ],"outputs":{"view":{"node":"dispatch","port":"view"}}}},
    {"id":"bootstrap.remember.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"store","capability":"memory.remember","inputs":{"value":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"store","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}},
    {"id":"bootstrap.recall.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"read","capability":"memory.search","inputs":{"query":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"read","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}},
    {"id":"bootstrap.answer.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"recall","capability":"memory.search","inputs":{"query":{"input":"text"}}},
            {"id":"model","capability":"model.answer","inputs":{
                "question":{"input":"text"},"context":{"node":"recall","port":"value"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"model","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}},
    {"id":"bootstrap.compose.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"composer","capability":"graph.compose","inputs":{"value":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"composer","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}}
]
SEED_ACTIONS=[
    ("living.echo","Echo text","bootstrap.echo.1","text"),
    ("living.upper","Uppercase text","bootstrap.upper.1","text"),
    ("living.dispatch","Interpret input","bootstrap.dispatch.1","text"),
    ("living.remember","Remember evidence-linked text","bootstrap.remember.1","text"),
    ("living.recall","Search scoped memory","bootstrap.recall.1","text"),
    ("living.ask","Answer with memory and DSH","bootstrap.answer.1","text"),
    ("living.compose","Propose a typed graph","bootstrap.compose.1","text"),
]
SEED_INTENT_RULES=[
    ("remember","remember ","living.remember",100),
    ("recall","recall ","living.recall",100),
    ("memory-search","search memory ","living.recall",100),
    ("compose","compose ","living.compose",100),
    ("uppercase","uppercase ","living.upper",100),
    ("echo","echo ","living.echo",100),
]
SEED_GRAPH_CATALOG=[
    ("bootstrap.echo.1","Echo input",["echo","text"]),
    ("bootstrap.upper.1","Uppercase text",["uppercase","text"]),
    ("bootstrap.remember.1","Store evidenced memory",["remember","memory"]),
    ("bootstrap.recall.1","Retrieve memory",["search","memory"]),
    ("bootstrap.answer.1","Answer using memory and model",["question","answer","model"]),
    ("bootstrap.compose.1","Create validated graph proposal",["compose","graph"]),
]
