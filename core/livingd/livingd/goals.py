"""Durable goal and due-event operations."""
from __future__ import annotations
import uuid
from datetime import datetime
from .database import connect

def schedule(db,principal,goal_id,action_id,text,due_at):
    if not isinstance(text,str) or not (1<=len(text)<=12000):
        raise ValueError("scheduled input must be 1..12000 characters")
    when=datetime.fromisoformat(due_at.replace("Z","+00:00")) if isinstance(due_at,str) else None
    if not when or not when.tzinfo:raise ValueError("due_at must have a timezone")
    action=db.execute("""
      SELECT id,graph_revision FROM control_actions
      WHERE id=%s AND enabled AND (scope_id IS NULL OR scope_id=%s)
    """,(action_id,principal["scope_id"])).fetchone()
    if not action:raise ValueError("action unavailable in scope")
    from .registry import catalog,authorize
    from .logic import validate_graph
    graph=db.execute("SELECT definition FROM graph_revisions WHERE id=%s",(action["graph_revision"],)).fetchone()
    manifest=catalog(db,principal["scope_id"])
    validate_graph(graph["definition"],manifest)
    for node in graph["definition"]["nodes"]:
        authorize(principal["scope_id"],manifest[node["capability"]],set(principal["grants"]))
    if goal_id:
        goal=db.execute("SELECT id,state FROM goal_units WHERE id=%s AND scope_id=%s",(goal_id,principal["scope_id"])).fetchone()
        if not goal or goal["state"]!="active":raise ValueError("goal is not active in scope")
    timer_id=uuid.uuid4()
    db.execute("""
      INSERT INTO scheduled_events(id,scope_id,principal_id,goal_id,action_id,text_input,due_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s)
    """,(timer_id,principal["scope_id"],principal["id"],goal_id,action_id,text,when))
    return {"id":str(timer_id),"action_id":action_id,"due_at":when.isoformat()}

def wake_once()->bool:
    with connect() as db:
        row=db.execute("""
          SELECT t.* FROM scheduled_events t
          JOIN control_principals p ON p.id=t.principal_id AND p.enabled
          LEFT JOIN goal_units g ON g.id=t.goal_id
          WHERE t.state='pending' AND t.due_at<=now()
            AND (t.goal_id IS NULL OR g.state='active')
          ORDER BY t.due_at,t.id LIMIT 1
          FOR UPDATE OF t SKIP LOCKED
        """).fetchone()
        if row is None:return False
        payload={"action_id":row["action_id"],"text":row["text_input"],"timer_id":str(row["id"])}
        event=db.execute("""
          INSERT INTO events(kind,source,source_id,scope_id,payload,principal_id)
          VALUES('wake.due','scheduler',%s,%s,%s::jsonb,%s)
          ON CONFLICT(source,source_id) DO NOTHING RETURNING id
        """,(str(row["id"]),row["scope_id"],__import__("json").dumps(payload),row["principal_id"])).fetchone()
        if event is None:
            event=db.execute("SELECT id FROM events WHERE source='scheduler' AND source_id=%s",(str(row["id"]),)).fetchone()
        db.execute("UPDATE scheduled_events SET state='fired',fired_event_id=%s WHERE id=%s",(event["id"],row["id"]))
        return True
