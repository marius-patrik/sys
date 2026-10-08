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
    # The registry and grants are authoritative database records.
    from .registry import SEED_CAPABILITIES, catalog
    from .auth import install_owner
    with connect() as db:
        for capid, inputs, outputs, adapter, config, effect, grants in SEED_CAPABILITIES:
            db.execute("""
                INSERT INTO capability_registry(id,revision,input_ports,output_ports,adapter,adapter_config,effect,required_grants)
                VALUES (%s,1,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s)
                ON CONFLICT DO NOTHING
            """,(capid,json.dumps(inputs),json.dumps(outputs),adapter,json.dumps(config),effect,grants))
        install_owner(db)
        manifest=catalog(db)
        for graph in SEED_GRAPHS:
            from .logic import validate_graph
            validate_graph(graph["definition"],manifest)
            db.execute("INSERT INTO graph_revisions(id, definition) VALUES (%s, %s::jsonb) ON CONFLICT DO NOTHING", (graph["id"], json.dumps(graph["definition"])))
        for action in SEED_ACTIONS:
            db.execute("INSERT INTO control_actions(id, title, graph_revision, input_name) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", action)
        db.execute("""
            INSERT INTO control_views(id,definition)
            VALUES ('interface.root', %s::jsonb) ON CONFLICT DO NOTHING
        """, (json.dumps({"type":"container","title":"Living Intelligence",
            "children":[{"type":"action-list","source":"control.catalog"},
                        {"type":"activity-list","source":"control.activations"},
                        {"type":"table","title":"Pending memories","source":"memory.candidates"},
                        {"type":"form","title":"LiteLLM Connection","endpoint":"/v1/settings/litellm",
                         "fields":[{"name":"base_url","label":"Gateway URL","type":"url","required":True},
                                   {"name":"api_key","label":"Virtual API key","type":"password"}]},
                        {"type":"model-picker","title":"LiteLLM Models","models":"/v1/models",
                         "selection":"/v1/models/selection","endpoint":"/v1/models/selection"}]}),))
        for rule in SEED_INTENT_RULES:
            db.execute("INSERT INTO intent_rules(id,prefix,action_id,priority) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", rule)
        for entry in SEED_GRAPH_CATALOG:
            db.execute("INSERT INTO graph_catalog(revision_id,description,tags) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", entry)
        for event_kind, action_id in [("surface.input","living.dispatch"),("control.action",None),
                                       ("tool.observed","living.attend"),("source.changed","living.attend")]:
            db.execute("INSERT INTO subscriptions(id, event_kind, action_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (f"bootstrap.{event_kind}", event_kind, action_id))
        for policy in [("bootstrap.tool","tool.observed",16),("bootstrap.source","source.changed",16),("bootstrap.manual","control.action",16)]:
            db.execute("INSERT INTO attention_policies(id,event_kind,min_length) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",policy)
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
    {"id":"bootstrap.attend.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"attention","capability":"memory.attend","inputs":{"value":{"input":"text"}}}
        ],"outputs":{"view":{"node":"attention","port":"view"}}}},
    {"id":"bootstrap.approve.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"approval","capability":"memory.approve","inputs":{"value":{"input":"text"}}}
        ],"outputs":{"view":{"node":"approval","port":"view"}}}},
    {"id":"bootstrap.python.1","definition":{
        "inputs":{"text":"text"},"nodes":[
            {"id":"execute","capability":"program.python","inputs":{"code":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"execute","port":"value"}}}
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
    ("living.python","Run isolated Python","bootstrap.python.1","text"),
    ("living.attend","Interpret observation for memory","bootstrap.attend.1","text"),
    ("living.approve-memory","Approve an evidenced memory candidate","bootstrap.approve.1","text"),
]
SEED_INTENT_RULES=[
    ("remember","remember ","living.remember",100),
    ("recall","recall ","living.recall",100),
    ("memory-search","search memory ","living.recall",100),
    ("compose","compose ","living.compose",100),
    ("uppercase","uppercase ","living.upper",100),
    ("echo","echo ","living.echo",100),
    ("run-python","run python ","living.python",100),
    ("observe","observe ","living.attend",100),
    ("approve-memory","approve memory ","living.approve-memory",100),
]
SEED_GRAPH_CATALOG=[
    ("bootstrap.echo.1","Echo input",["echo","text"]),
    ("bootstrap.upper.1","Uppercase text",["uppercase","text"]),
    ("bootstrap.remember.1","Store evidenced memory",["remember","memory"]),
    ("bootstrap.recall.1","Retrieve memory",["search","memory"]),
    ("bootstrap.answer.1","Answer using memory and model",["question","answer","model"]),
    ("bootstrap.compose.1","Create validated graph proposal",["compose","graph"]),
]
