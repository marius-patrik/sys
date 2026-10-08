"""Durable PostgreSQL event routing and bounded, deterministic graph execution."""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone

from .database import connect
from .logic import validate_graph, due_nodes, node_inputs, execute_pure, selected_outputs

log = logging.getLogger(__name__)


def ingest(kind: str, source: str, source_id: str, scope: str, payload: dict, cause: int | None = None) -> int:
    """Ingest exactly one logical event per (source, source_id). Trigger enqueues it."""
    with connect() as db:
        row = db.execute("""
            INSERT INTO events(kind, source, source_id, scope_id, payload, causation_event_id)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT(source,source_id) DO NOTHING RETURNING id
        """, (kind, source, source_id, scope, json.dumps(payload), cause)).fetchone()
        if row is None:
            row = db.execute("SELECT id, kind, scope_id, payload FROM events WHERE source=%s AND source_id=%s", (source, source_id)).fetchone()
            if (row["kind"],row["scope_id"],row["payload"]) != (kind,scope,payload):
                raise ValueError("idempotency key reused with different event payload")
        return row["id"]


def route_once() -> bool:
    # Pin the chosen action and immutable graph while routing; later edits to
    # subscriptions or action catalog cannot silently change existing work.
    with connect() as db:
        row = db.execute("""
            SELECT r.event_id, e.kind, e.payload FROM event_routing r
            JOIN events e ON e.id=r.event_id
            WHERE r.state='pending' ORDER BY r.event_id LIMIT 1
            FOR UPDATE OF r SKIP LOCKED
        """).fetchone()
        if not row:
            return False
        subscriptions = db.execute("""
            SELECT id, revision, action_id FROM subscriptions
            WHERE event_kind=%s AND enabled ORDER BY id
        """, (row["kind"],)).fetchall()
        for sub in subscriptions:
            action_id = row["payload"].get("action_id") if row["kind"] == "control.action" else sub["action_id"]
            action = db.execute("""
                SELECT id, revision, graph_revision, input_name FROM control_actions
                WHERE id=%s AND enabled
            """, (action_id,)).fetchone()
            if action:
                db.execute("""
                    INSERT INTO event_deliveries(event_id,subscription_id,subscription_revision,
                        target_action_id,target_action_revision,target_graph_revision,target_input_name)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """,(row["event_id"],sub["id"],sub["revision"],action["id"],action["revision"],
                      action["graph_revision"],action["input_name"]))
            else:
                db.execute("""
                    INSERT INTO event_deliveries(event_id,subscription_id,subscription_revision,state,reason)
                    VALUES(%s,%s,%s,'failed','missing or unauthorized action') ON CONFLICT DO NOTHING
                """,(row["event_id"],sub["id"],sub["revision"]))
        db.execute("UPDATE event_routing SET state='routed',lease_epoch=lease_epoch+1 WHERE event_id=%s",(row["event_id"],))
        return True


def deliver_once() -> bool:
    with connect() as db:
        row = db.execute("""
            SELECT d.event_id, d.subscription_id, d.subscription_revision,
                   d.target_graph_revision, d.target_input_name, e.payload, e.scope_id
            FROM event_deliveries d JOIN events e ON e.id=d.event_id
            WHERE d.state='pending' ORDER BY d.event_id LIMIT 1
            FOR UPDATE OF d SKIP LOCKED
        """).fetchone()
        if row is None:
            return False
        value = row["payload"].get("text")
        graph = db.execute("SELECT definition FROM graph_revisions WHERE id=%s", (row["target_graph_revision"],)).fetchone()
        try:
            if graph is None or not isinstance(value,str):
                raise ValueError("missing graph or invalid text")
            validate_graph(graph["definition"])
            if graph["definition"]["inputs"].get(row["target_input_name"]) != "text":
                raise ValueError("incompatible action input")
        except (ValueError,KeyError) as exc:
            db.execute("""
                UPDATE event_deliveries SET state='failed',reason=%s
                WHERE event_id=%s AND subscription_id=%s AND subscription_revision=%s
            """,(str(exc)[:500],row["event_id"],row["subscription_id"],row["subscription_revision"]))
            return True
        activation_id=uuid.uuid5(uuid.NAMESPACE_URL,f"living:{row['event_id']}:{row['subscription_id']}:{row['subscription_revision']}")
        db.execute("""
            INSERT INTO activations(id,event_id,graph_revision,scope_id,inputs)
            VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING
        """,(activation_id,row["event_id"],row["target_graph_revision"],row["scope_id"],
              json.dumps({row["target_input_name"]:value})))
        db.execute("""
            UPDATE event_deliveries SET state='done',activation_id=%s
            WHERE event_id=%s AND subscription_id=%s AND subscription_revision=%s
        """,(activation_id,row["event_id"],row["subscription_id"],row["subscription_revision"]))
        return True


def _claim_node():
    """Claim exactly one ready node; no external work is done in the transaction."""
    with connect() as db:
        activations = db.execute("""
            SELECT a.id, a.graph_revision, a.inputs, a.scope_id, a.event_id, g.definition
            FROM activations a JOIN graph_revisions g ON g.id=a.graph_revision
            WHERE a.state IN ('pending','running')
            ORDER BY a.created_at, a.id LIMIT 32 FOR UPDATE OF a SKIP LOCKED
        """).fetchall()
        for a in activations:
            g = a["definition"]
            validate_graph(g)
            rows = db.execute("SELECT node_id, state, lease_epoch, lease_until, result FROM node_runs WHERE activation_id=%s", (a["id"],)).fetchall()
            done = {r["node_id"] for r in rows if r["state"] == "completed"}
            running = {r["node_id"] for r in rows if r["state"] == "leased" and r["lease_until"] and r["lease_until"] > datetime.now(timezone.utc)}
            available = due_nodes(g, done, running)
            if not available:
                if len(done) == len(g["nodes"]):
                    outputs = {r["node_id"]: r["result"] for r in rows}
                    final = selected_outputs(g, outputs)
                    db.execute("UPDATE activations SET state='completed',result=%s::jsonb,updated_at=now() WHERE id=%s", (json.dumps(final), a["id"]))
                    db.execute("""
                        INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                        VALUES ('activation.completed','livingd',%s,%s,%s::jsonb,%s)
                        ON CONFLICT(source,source_id) DO NOTHING
                    """, (f"completed:{a['id']}", a["scope_id"], json.dumps({"activation_id":str(a["id"]),"outputs":final}),a["event_id"]))
                    return {"finalized": True}
                continue
            node_id = available[0]
            node = next(n for n in g["nodes"] if n["id"] == node_id)
            previous = next((r for r in rows if r["node_id"] == node_id), None)
            epoch = previous["lease_epoch"] + 1 if previous else 1
            db.execute("""
                INSERT INTO node_runs(activation_id,node_id,lease_epoch,lease_until)
                VALUES (%s,%s,%s, now() + interval '20 seconds')
                ON CONFLICT(activation_id,node_id) DO UPDATE
                SET state='leased',lease_epoch=EXCLUDED.lease_epoch,
                    lease_until=EXCLUDED.lease_until,error=NULL
                WHERE node_runs.state='leased' AND node_runs.lease_until < now()
            """, (a["id"],node_id,epoch))
            db.execute("UPDATE activations SET state='running',updated_at=now() WHERE id=%s", (a["id"],))
            outputs = {r["node_id"]: r["result"] for r in rows if r["state"] == 'completed'}
            args = node_inputs(g, node_id, a["inputs"], outputs)
            return {"finalized": False, "activation_id":a["id"],"node_id":node_id,"epoch":epoch,
                    "capability":node["capability"], "args":args,"scope_id":a["scope_id"],"cause":a["event_id"]}
        return None


def node_once() -> bool:
    task = _claim_node()
    if not task:
        return False
    if task["finalized"]:
        return True
    # A single pure node runs outside the DB transaction. Invalid state cannot commit.
    try:
        result = execute_pure(task["capability"], task["args"])
    except Exception as exc:
        log.exception("node failed: %s", task)
        with connect() as db:
            db.execute("""
                UPDATE node_runs SET state='failed',error=%s,lease_until=NULL
                WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s AND state='leased'
            """, (str(exc)[:1000],task["activation_id"],task["node_id"],task["epoch"]))
            db.execute("UPDATE activations SET state='failed', updated_at=now() WHERE id=%s", (task["activation_id"],))
        return True
    with connect() as db:
        row = db.execute("""
            UPDATE node_runs SET state='completed', result=%s::jsonb, lease_until=NULL
            WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s
              AND state='leased' AND lease_until >= now()
            RETURNING activation_id
        """, (json.dumps(result), task["activation_id"], task["node_id"],task["epoch"])).fetchone()
        if row:
            db.execute("UPDATE activations SET updated_at=now() WHERE id=%s", (task["activation_id"],))
            db.execute("""
                INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                VALUES('node.completed','livingd',%s,%s,%s::jsonb,%s)
                ON CONFLICT(source,source_id) DO NOTHING
            """, (f"node:{task['activation_id']}:{task['node_id']}", task["scope_id"],json.dumps({"activation_id":str(task["activation_id"]),"node_id":task["node_id"],"result":result}),task["cause"]))
    return True


def tick() -> bool:
    # Each operation is bounded; routing and execution progress independently.
    return route_once() or deliver_once() or node_once()
