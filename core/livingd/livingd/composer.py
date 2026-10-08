"""Database-defined intent selection and graph proposal validation."""
from __future__ import annotations
import json
import re
import uuid
from .database import connect
from .logic import validate_graph

def dispatch(scope: str, text: str) -> tuple[str,str]:
    """Deterministic rules live in PostgreSQL and are ordered by priority."""
    with connect() as db:
        rules=db.execute("""
          SELECT prefix,action_id FROM intent_rules WHERE enabled
          ORDER BY priority DESC, length(prefix) DESC, id
        """).fetchall()
        for rule in rules:
            prefix=rule["prefix"]
            if text.casefold().startswith(prefix.casefold()):
                remainder=text[len(prefix):].strip()
                if remainder:return rule["action_id"],remainder
        action=db.execute("SELECT id FROM control_actions WHERE id='living.ask' AND enabled").fetchone()
    if not action: raise ValueError("no enabled generic answering graph")
    return action["id"],text

def candidate_graph(prompt: str) -> dict:
    """Compose a fresh graph from typed existing capabilities; no effects."""
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>2000:
        raise ValueError("invalid composition goal")
    # An answer graph contains memory recall and an independent model invocation
    # joined by typed dependencies, rather than embedding a model/tool loop.
    proposal={
      "inputs":{"text":"text"},
      "nodes":[
        {"id":"recall","capability":"memory.search","inputs":{"query":{"input":"text"}}},
        {"id":"answer","capability":"model.answer","inputs":{
          "question":{"input":"text"},"context":{"node":"recall","port":"value"}}},
        {"id":"result","capability":"view.text","inputs":{"value":{"node":"answer","port":"value"}}}
      ],
      "outputs":{"view":{"node":"result","port":"view"}}
    }
    validate_graph(proposal)
    return proposal

def propose(scope:str,goal:str)->dict:
    """An inspectable graph proposal; not permission to execute it."""
    g=candidate_graph(goal)
    return {"definition":g,"description":goal,
            "required_capabilities":sorted({n["capability"] for n in g["nodes"]})}

def install(db, proposal:dict, scope:str, event_id:int) -> str:
    """Immutable, validated, content-addressed graph revision."""
    import hashlib
    graph=proposal["definition"]
    validate_graph(graph)
    raw=json.dumps(graph,sort_keys=True,separators=(',',':'))
    revision="composed."+hashlib.sha256(raw.encode()).hexdigest()[:32]
    db.execute("INSERT INTO graph_revisions(id,definition) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",(revision,raw))
    db.execute("INSERT INTO graph_catalog(revision_id,description,tags) VALUES (%s,%s,ARRAY['composed']) ON CONFLICT DO NOTHING",
               (revision,proposal["description"][:2000]))
    db.execute("INSERT INTO graph_proposals(id,source_event_id,scope_id,definition,accepted_revision) VALUES (%s,%s,%s,%s::jsonb,%s)",
               (uuid.uuid4(),event_id,scope,raw,revision))
    return revision
