"""Durable PostgreSQL event routing and bounded, deterministic graph execution."""
from __future__ import annotations
import json
import logging
import uuid
import threading
import itertools
from datetime import datetime, timezone

from .database import connect
from .logic import validate_graph, due_nodes, node_inputs, selected_outputs
from .runtime import execute
from .memory import record as record_memory
from .composer import install as install_proposal
from .registry import catalog,authorize,visible_graph
from .budgets import reserve,BudgetExceeded

log = logging.getLogger(__name__)


def ingest(kind: str, source: str, source_id: str, scope: str, payload: dict, cause: int | None = None, principal_id: str = 'local-owner') -> int:
    """Ingest exactly one logical event per (source, source_id). Trigger enqueues it."""
    with connect() as db:
        row = db.execute("""
            INSERT INTO events(kind, source, source_id, scope_id, payload, causation_event_id,principal_id)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT(source,source_id) DO NOTHING RETURNING id
        """, (kind, source, source_id, scope, json.dumps(payload), cause,principal_id)).fetchone()
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
            action_id = row["payload"].get("action_id") if row["kind"] in ("control.action","wake.due") else sub["action_id"]
            action = db.execute("""
                SELECT id, revision, graph_revision, input_name FROM control_actions
                WHERE id=%s AND enabled AND (scope_id IS NULL OR scope_id=(SELECT scope_id FROM events WHERE id=%s))
            """, (action_id,row["event_id"])).fetchone()
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
                   d.target_graph_revision, d.target_input_name, e.payload, e.scope_id,e.principal_id
            FROM event_deliveries d JOIN events e ON e.id=d.event_id
            WHERE d.state='pending' ORDER BY d.event_id LIMIT 1
            FOR UPDATE OF d SKIP LOCKED
        """).fetchone()
        if row is None:
            return False
        cancelled=db.execute("SELECT 1 FROM mcp_tasks WHERE event_id=%s AND cancelled_at IS NOT NULL",(row["event_id"],)).fetchone()
        if cancelled:
            db.execute("""
              UPDATE event_deliveries SET state='failed',reason='MCP task cancelled before activation'
              WHERE event_id=%s AND subscription_id=%s AND subscription_revision=%s
            """,(row["event_id"],row["subscription_id"],row["subscription_revision"]))
            return True
        value = row["payload"].get("text")
        graph = db.execute("SELECT definition FROM graph_revisions WHERE id=%s", (row["target_graph_revision"],)).fetchone()
        try:
            if graph is None or not isinstance(value,str):
                raise ValueError("missing graph or invalid text")
            manifest=catalog(db,row["scope_id"])
            validate_graph(graph["definition"],manifest)
            principal=db.execute("SELECT grants FROM control_principals WHERE id=%s AND enabled",(row["principal_id"],)).fetchone()
            if not principal:raise ValueError("event has no valid principal")
            grants=set(principal["grants"])
            for node in graph["definition"]["nodes"]:
                authorize(row["scope_id"],manifest[node["capability"]],grants)
            if graph["definition"]["inputs"].get(row["target_input_name"]) != "text":
                raise ValueError("incompatible action input")
        except (ValueError,KeyError,PermissionError) as exc:
            db.execute("""
                UPDATE event_deliveries SET state='failed',reason=%s
                WHERE event_id=%s AND subscription_id=%s AND subscription_revision=%s
            """,(str(exc)[:500],row["event_id"],row["subscription_id"],row["subscription_revision"]))
            return True
        activation_id=uuid.uuid5(uuid.NAMESPACE_URL,f"living:{row['event_id']}:{row['subscription_id']}:{row['subscription_revision']}")
        db.execute("""
            INSERT INTO activations(id,event_id,graph_revision,scope_id,inputs,principal_id,grants,root_event_id,capability_pins)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,
                (WITH RECURSIVE chain AS (
                    SELECT id,causation_event_id FROM events WHERE id=%s
                    UNION ALL SELECT e.id,e.causation_event_id FROM events e JOIN chain c ON e.id=c.causation_event_id
                ) SELECT id FROM chain WHERE causation_event_id IS NULL LIMIT 1),%s::jsonb)
            ON CONFLICT DO NOTHING
        """,(activation_id,row["event_id"],row["target_graph_revision"],row["scope_id"],
              json.dumps({row["target_input_name"]:value}),row["principal_id"],list(grants),row["event_id"],json.dumps({n["capability"]:manifest[n["capability"]] for n in graph["definition"]["nodes"]})))
        db.execute("""
            UPDATE event_deliveries SET state='done',activation_id=%s
            WHERE event_id=%s AND subscription_id=%s AND subscription_revision=%s
        """,(activation_id,row["event_id"],row["subscription_id"],row["subscription_revision"]))
        return True


def _cancel_revoked(db, activation_id)->bool:
    """Stop a durable unit if its originating principal lost permission.

    Revocation cannot undo a physical effect already in flight; that effect is
    marked uncertain for explicit reconciliation rather than replayed.
    """
    row=db.execute("""
        SELECT a.id,a.grants,a.scope_id,p.id AS principal_exists,
               p.enabled,p.grants AS current_grants,p.scope_id AS principal_scope
        FROM activations a LEFT JOIN control_principals p ON p.id=a.principal_id
        WHERE a.id=%s AND a.state IN ('pending','running','suspended')
        FOR UPDATE OF a
    """,(activation_id,)).fetchone()
    if row is None:return False
    if (row["principal_exists"] is not None and row["enabled"]
            and row["principal_scope"]==row["scope_id"]
            and set(row["grants"])<=set(row["current_grants"] or [])):
        return False
    db.execute("""
        UPDATE execution_effects SET state='uncertain',updated_at=now()
        WHERE activation_id=%s AND state='running'
    """,(activation_id,))
    db.execute("""
        UPDATE node_runs SET state='failed',lease_until=NULL,
               error='execution authorization revoked'
        WHERE activation_id=%s AND state IN ('leased','retry_wait','awaiting_child')
    """,(activation_id,))
    db.execute("""
        UPDATE activations SET state='cancelled',cancelled_at=now(),updated_at=now()
        WHERE id=%s AND state IN ('pending','running','suspended')
    """,(activation_id,))
    return True


def _claim_node():
    """Claim exactly one ready node; no external work is done in the transaction."""
    with connect() as db:
        activations = db.execute("""
            SELECT a.id, a.graph_revision, a.inputs, a.scope_id, a.event_id,a.grants,a.capability_pins, g.definition
            FROM activations a JOIN graph_revisions g ON g.id=a.graph_revision
            WHERE a.state IN ('pending','running')
            ORDER BY a.scheduler_checked_at, a.created_at, a.id LIMIT 32 FOR UPDATE OF a SKIP LOCKED
        """).fetchall()
        for a in activations:
            if _cancel_revoked(db,a["id"]):
                return {"finalized":True}
            g = a["definition"]
            manifest=a["capability_pins"] or catalog(db,a["scope_id"])
            validate_graph(g,manifest)
            rows = db.execute("SELECT node_id, state, lease_epoch, lease_until,next_attempt_at,result,attempts,child_activation_id FROM node_runs WHERE activation_id=%s", (a["id"],)).fetchall()
            for running_child in (r for r in rows if r["state"]=="awaiting_child"):
                child=db.execute("SELECT state,result FROM activations WHERE id=%s",
                                 (running_child["child_activation_id"],)).fetchone()
                if not child:raise RuntimeError("nested graph activation missing")
                if child["state"]=="completed":
                    output=child["result"] or {}
                    value=output.get("view") or output.get("value")
                    if isinstance(value,dict):value=value.get("value",json.dumps(value))
                    if not isinstance(value,str):value=json.dumps(value)
                    db.execute("""
                      UPDATE node_runs SET state='completed',result=%s::jsonb
                      WHERE activation_id=%s AND node_id=%s AND state='awaiting_child'
                    """,(json.dumps({"value":value}),a["id"],running_child["node_id"]))
                    db.execute("""
                      INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                      VALUES('node.completed','livingd',%s,%s,%s::jsonb,%s)
                      ON CONFLICT(source,source_id) DO NOTHING
                    """,("resumed:"+str(a["id"])+":"+running_child["node_id"],a["scope_id"],
                          json.dumps({"activation_id":str(a["id"]),"node_id":running_child["node_id"],
                                      "value":value}),a["event_id"]))
                    return {"finalized":True}
                if child["state"] in ("failed","cancelled"):
                    db.execute("UPDATE node_runs SET state='failed',error=%s WHERE activation_id=%s AND node_id=%s",
                               ("nested graph "+child["state"],a["id"],running_child["node_id"]))
                    db.execute("UPDATE activations SET state='failed' WHERE id=%s",(a["id"],))
                    return {"finalized":True}
            done = {r["node_id"] for r in rows if r["state"] == "completed"}
            running = {r['node_id'] for r in rows if
                       (r['state']=='leased' and r['lease_until'] and r['lease_until']>datetime.now(timezone.utc))
                       or (r['state']=='retry_wait' and r['next_attempt_at'] and r['next_attempt_at']>datetime.now(timezone.utc))
                       or r['state'] in ('failed','uncertain','awaiting_child')}
            available = due_nodes(g, done, running,manifest)
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
            entry=manifest[node['capability']]
            authorize(a['scope_id'],entry,set(a['grants']))
            if previous and previous['state']=='leased' and entry['effect']=='external':
                db.execute("UPDATE node_runs SET state='uncertain',error='expired external operation requires reconciliation' WHERE activation_id=%s AND node_id=%s",(a['id'],node_id))
                db.execute("UPDATE activations SET state='suspended' WHERE id=%s",(a['id'],))
                db.execute("UPDATE execution_effects SET state='uncertain',updated_at=now() WHERE activation_id=%s AND node_id=%s AND state='running'",(a['id'],node_id))
                return {'finalized':True}
            if entry["effect"]=="external":
                try:
                    reserve(db,a["scope_id"],"external")
                except BudgetExceeded as exc:
                    db.execute("""
                      UPDATE activations SET state='failed',result=%s::jsonb,updated_at=now()
                      WHERE id=%s AND state IN ('pending','running')
                    """,(json.dumps({"error":str(exc)}),a["id"]))
                    db.execute("""
                      INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                      VALUES('activation.budget_exceeded','livingd',%s,%s,%s::jsonb,%s)
                      ON CONFLICT(source,source_id) DO NOTHING
                    """,("budget:"+str(a["id"])+":"+node_id,a["scope_id"],
                         json.dumps({"activation_id":str(a["id"]),"reason":str(exc)}),a["event_id"]))
                    return {"finalized":True}
            epoch = previous["lease_epoch"] + 1 if previous else 1
            db.execute("""
                INSERT INTO node_runs(activation_id,node_id,lease_epoch,lease_until)
                VALUES (%s,%s,%s, now() + interval '90 seconds')
                ON CONFLICT(activation_id,node_id) DO UPDATE
                SET state='leased',lease_epoch=EXCLUDED.lease_epoch,
                    lease_until=EXCLUDED.lease_until,error=NULL,attempts=node_runs.attempts+1,next_attempt_at=NULL
                WHERE (node_runs.state='leased' AND node_runs.lease_until < now())
                    OR (node_runs.state='retry_wait' AND node_runs.next_attempt_at <= now())
            """, (a["id"],node_id,epoch))
            if entry["effect"]=="external":
                effect_id=uuid.uuid5(uuid.NAMESPACE_URL,"living-effect:"+str(a["id"])+":"+node_id)
                db.execute("""
                   INSERT INTO execution_effects(id,activation_id,node_id,capability_id,state,idempotency_key)
                   VALUES(%s,%s,%s,%s,'running',%s)
                   ON CONFLICT(activation_id,node_id) DO NOTHING
                """,(effect_id,a["id"],node_id,node["capability"],str(effect_id)))
            db.execute("UPDATE activations SET state='running',updated_at=now(),scheduler_checked_at=now() WHERE id=%s", (a["id"],))
            outputs = {r["node_id"]: r["result"] for r in rows if r["state"] == 'completed'}
            args = node_inputs(g, node_id, a["inputs"], outputs)
            return {"finalized": False, "activation_id":a["id"],"node_id":node_id,"epoch":epoch,
                    "capability":node["capability"], "args":args,"scope_id":a["scope_id"],"cause":a["event_id"],"manifest":entry}
        # Even if 32 old activations are waiting for leases, newer activations
        # must eventually enter the scheduling window.
        if activations:
            db.execute("UPDATE activations SET scheduler_checked_at=now() WHERE id=ANY(%s)",
                       ([a["id"] for a in activations],))
        return None


def _renew(activation_id,node_id,epoch,stop):
    while not stop.wait(15):
        try:
            with connect() as db:
                row=db.execute("""
                    UPDATE node_runs SET lease_until=now()+interval '90 seconds'
                    WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s
                    AND state='leased' AND lease_until>=now()
                    RETURNING node_id
                """,(activation_id,node_id,epoch)).fetchone()
            if not row:return
        except Exception:
            log.exception("node lease heartbeat failed")

def node_once() -> bool:
    task=_claim_node()
    if not task:return False
    if task["finalized"]:return True
    stop=threading.Event()
    heartbeat=threading.Thread(target=_renew,args=(task["activation_id"],
        task["node_id"],task["epoch"],stop),daemon=True)
    heartbeat.start()
    try:
        from .runtime import execute_registered
        result=execute_registered(task["capability"],task["args"],task["scope_id"],
            task["cause"],task["manifest"],task["activation_id"],task["node_id"])
    except Exception as exc:
        log.warning("graph node %s.%s failed: %s",task["activation_id"],task["node_id"],exc)
        with connect() as db:
            effect=task["manifest"]["effect"]
            retryable=effect in ("read","inference") and not isinstance(exc,BudgetExceeded)
            state='uncertain' if effect=='external' else 'retry_wait' if retryable and task["epoch"]<3 else 'failed'
            row=db.execute("""
                UPDATE node_runs SET state=%s,error=%s,lease_until=NULL,
                  next_attempt_at=CASE WHEN %s='retry_wait'
                    THEN now()+(%s*interval '1 second') ELSE NULL END
                WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s
                  AND state='leased' AND lease_until>=now()
                  AND EXISTS (
                    SELECT 1 FROM activations a
                    JOIN control_principals p ON p.id=a.principal_id
                    WHERE a.id=%s AND a.state='running' AND p.enabled
                      AND p.scope_id=a.scope_id AND p.grants @> a.grants
                  )
                RETURNING node_id
            """,(state,str(exc)[:1000],state,2**min(task["epoch"],5),
                 task["activation_id"],task["node_id"],task["epoch"],
                 task["activation_id"])).fetchone()
            if row is None:
                _cancel_revoked(db,task["activation_id"])
            if row and state=='uncertain':
                db.execute("UPDATE activations SET state='suspended',updated_at=now() WHERE id=%s AND state='running'",(task["activation_id"],))
                db.execute("UPDATE execution_effects SET state='uncertain',updated_at=now() WHERE activation_id=%s AND node_id=%s",(task["activation_id"],task["node_id"]))
            if row and state=='failed':
                db.execute("UPDATE activations SET state='failed',updated_at=now() WHERE id=%s AND state='running'",(task["activation_id"],))
        return True
    finally:
        stop.set()
        heartbeat.join(timeout=1)
    with connect() as db:
        # Effects and node completion share one fenced transaction.
        private = {k:result.pop(k) for k in list(result) if k.startswith("_")}
        row = db.execute("""
            UPDATE node_runs SET state='completed', result=%s::jsonb, lease_until=NULL
            WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s
              AND state='leased' AND lease_until >= now()
               AND EXISTS (
                    SELECT 1 FROM activations a
                    JOIN control_principals p ON p.id=a.principal_id
                    WHERE a.id=%s AND a.state='running' AND p.enabled
                      AND p.scope_id=a.scope_id AND p.grants @> a.grants
                  )
            RETURNING activation_id
        """, (json.dumps(result), task["activation_id"], task["node_id"],task["epoch"],task["activation_id"])).fetchone()
        if row is None and _cancel_revoked(db,task["activation_id"]):
            return True
        if row:
            if task["manifest"]["effect"]=="external":
                db.execute("""
                  UPDATE execution_effects SET state='committed',result=%s::jsonb,updated_at=now()
                  WHERE activation_id=%s AND node_id=%s AND state='running'
                """,(json.dumps(result),task["activation_id"],task["node_id"]))
            if "_child_activation" in private:
                spec=private["_child_activation"]
                parent=db.execute("""
                    SELECT principal_id,grants,root_event_id,depth,max_depth
                    FROM activations WHERE id=%s AND state='running' FOR UPDATE
                """,(task["activation_id"],)).fetchone()
                target=visible_graph(db,spec["revision"],task["scope_id"])
                if not parent or not target or parent["depth"]>=parent["max_depth"]:
                    raise ValueError("invalid nested graph activation")
                child_manifest=catalog(db,task["scope_id"])
                validate_graph(target["definition"],child_manifest)
                if target["definition"].get("inputs")!={"text":"text"}:
                    raise ValueError("nested graph needs text input")
                for node in target["definition"]["nodes"]:
                    authorize(task["scope_id"],child_manifest[node["capability"]],set(parent["grants"]))
                child_id=uuid.uuid5(uuid.NAMESPACE_URL,
                        "living-child:"+str(task["activation_id"])+":"+task["node_id"])
                child_event=db.execute("""
                    INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id,principal_id)
                    VALUES('graph.child_started','livingd',%s,%s,%s::jsonb,%s,%s)
                    ON CONFLICT(source,source_id) DO NOTHING RETURNING id
                """,("child:"+str(child_id),task["scope_id"],
                      json.dumps({"parent_activation":str(task["activation_id"]),
                                  "node":task["node_id"]}),task["cause"],parent["principal_id"])).fetchone()
                if child_event is None:
                    child_event=db.execute("SELECT id FROM events WHERE source='livingd' AND source_id=%s",
                                           ("child:"+str(child_id),)).fetchone()
                child_pins={n["capability"]:child_manifest[n["capability"]]
                           for n in target["definition"]["nodes"]}
                db.execute("""
                    INSERT INTO activations(id,event_id,graph_revision,scope_id,inputs,principal_id,
                                            grants,root_event_id,parent_activation_id,parent_node_id,
                                            depth,max_depth,capability_pins)
                    VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT(id) DO NOTHING
                """,(child_id,child_event["id"],spec["revision"],task["scope_id"],
                     json.dumps({"text":spec["text"]}),parent["principal_id"],parent["grants"],
                     parent["root_event_id"],task["activation_id"],task["node_id"],
                     parent["depth"]+1,parent["max_depth"],json.dumps(child_pins)))
                db.execute("""
                    UPDATE node_runs SET state='awaiting_child',result=NULL,child_activation_id=%s
                    WHERE activation_id=%s AND node_id=%s AND lease_epoch=%s
                """,(child_id,task["activation_id"],task["node_id"],task["epoch"]))
            if "_goal_create" in private:
                new_goal=uuid.uuid5(uuid.NAMESPACE_URL,"living-goal:"+str(task["activation_id"]))
                db.execute("""
                  INSERT INTO goal_units(id,scope_id,title)
                  VALUES(%s,%s,%s) ON CONFLICT DO NOTHING
                """,(new_goal,task["scope_id"],private["_goal_create"]["title"]))
                result["view"]["goal_id"]=str(new_goal)
                db.execute("UPDATE node_runs SET result=%s::jsonb WHERE activation_id=%s AND node_id=%s",
                           (json.dumps(result),task["activation_id"],task["node_id"]))
                db.execute("""
                  INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                  VALUES('goal.created','livingd',%s,%s,%s::jsonb,%s)
                  ON CONFLICT(source,source_id) DO NOTHING
                """,("goal:"+str(new_goal),task["scope_id"],
                     json.dumps({"goal_id":str(new_goal),"text":private["_goal_create"]["title"]}),task["cause"]))
            if "_memory_write" in private:
                claim_id=record_memory(db,task["scope_id"],private["_memory_write"],task["cause"])
                db.execute("""
                    INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                    VALUES('memory.updated','livingd',%s,%s,%s::jsonb,%s)
                    ON CONFLICT(source,source_id) DO NOTHING
                """,(f"memory:{task['activation_id']}:{task['node_id']}",task["scope_id"],
                     json.dumps({"claim_id":claim_id}),task["cause"]))
            if "_dispatch" in private:
                db.execute("""
                    INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id,principal_id)
                    VALUES('control.action','livingd',%s,%s,%s::jsonb,%s,
                        (SELECT principal_id FROM events WHERE id=%s))
                    ON CONFLICT(source,source_id) DO NOTHING
                """,(f"dispatch:{task['activation_id']}:{task['node_id']}",task["scope_id"],
                     json.dumps(private["_dispatch"]),task["cause"],task["cause"]))
            if "_attention" in private:
                decision=private["_attention"]
                db.execute("""
                  INSERT INTO memory_decisions(event_id,policy_id,policy_revision,choice,reason)
                  VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """,(task["cause"],decision["policy_id"],decision["policy_revision"],
                     decision["choice"],decision["reason"]))
                if decision["choice"]=="candidate":
                    for index,observation in enumerate(decision["candidates"]):
                        candidate_id=uuid.uuid5(uuid.NAMESPACE_URL,
                           "living-candidate:"+str(task["cause"])+":"+str(index))
                        db.execute("""
                          INSERT INTO memory_candidates(id,scope_id,source_event_id,content,assertion)
                          VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING
                        """,(candidate_id,task["scope_id"],task["cause"],
                             observation["content"],json.dumps(observation["assertion"])))
                        db.execute("""
                            INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                            VALUES('memory.candidate','livingd',%s,%s,%s::jsonb,%s)
                            ON CONFLICT(source,source_id) DO NOTHING
                        """,(f"candidate:{candidate_id}",task["scope_id"],
                             json.dumps({"candidate_id":str(candidate_id)}),task["cause"]))
            if "_approve_memory" in private:
                candidate=db.execute("""
                    SELECT id,content,source_event_id,assertion FROM memory_candidates
                    WHERE id=%s AND scope_id=%s AND state='pending' FOR UPDATE
                """,(private["_approve_memory"],task["scope_id"])).fetchone()
                if candidate is None:raise ValueError("candidate no longer pending")
                claim_id=record_memory(db,task["scope_id"],candidate["content"],candidate["source_event_id"],candidate["assertion"])
                db.execute("UPDATE memory_candidates SET state='approved' WHERE id=%s",(candidate["id"],))
                db.execute("""
                    INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                    VALUES('memory.updated','livingd',%s,%s,%s::jsonb,%s)
                    ON CONFLICT(source,source_id) DO NOTHING
                """,(f"approved:{candidate['id']}",task["scope_id"],
                     json.dumps({"claim_id":claim_id,"candidate_id":str(candidate["id"])}),task["cause"]))
            if "_proposal" in private:
                revision=install_proposal(db,private["_proposal"],task["scope_id"],task["cause"])
                result["value"]="Graph revision "+revision+" validated and stored."
                db.execute("UPDATE node_runs SET result=%s::jsonb WHERE activation_id=%s AND node_id=%s",
                           (json.dumps(result),task["activation_id"],task["node_id"]))
            db.execute("UPDATE activations SET updated_at=now() WHERE id=%s", (task["activation_id"],))
            db.execute("""
                INSERT INTO events(kind,source,source_id,scope_id,payload,causation_event_id)
                VALUES(%s,'livingd',%s,%s,%s::jsonb,%s)
                ON CONFLICT(source,source_id) DO NOTHING
            """, ('node.awaiting_child' if '_child_activation' in private else 'node.completed',
                f"node:{task['activation_id']}:{task['node_id']}", task["scope_id"],
                json.dumps({"activation_id":str(task["activation_id"]),"node_id":task["node_id"],"result":result}),task["cause"]))
    return True


_sequence=itertools.count()
def tick() -> bool:
    from .goals import wake_once
    steps=(wake_once,route_once,deliver_once,node_once)
    start=next(_sequence)%len(steps)
    for shift in range(len(steps)):
        if steps[(start+shift)%len(steps)]():return True
    return False
