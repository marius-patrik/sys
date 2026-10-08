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

SAFE_CAPABILITIES={"memory.search","model.answer","text.echo","text.upper","text.prefix","view.text"}

def _safe(graph:dict)->bool:
    return (set(graph.get("inputs",{}))=={"text"}
      and graph["inputs"]["text"]=="text"
      and set(graph.get("outputs",{}))=={"view"}
      and all(node["capability"] in SAFE_CAPABILITIES for node in graph["nodes"]))

def propose(scope:str,goal:str,generate=None)->dict:
    """Reuse catalog fragments first; optionally generate a typed graph proposal."""
    if not isinstance(goal,str) or not goal.strip() or len(goal)>2000:
        raise ValueError("invalid graph goal")
    terms=set(re.findall(r"[a-z0-9]{3,}",goal.casefold()))
    with connect() as db:
        records=db.execute("""
            SELECT c.revision_id,c.tags,c.description,g.definition
            FROM graph_catalog c JOIN graph_revisions g ON g.id=c.revision_id
            WHERE c.enabled AND c.risk='read'
        """).fetchall()
    ranked=[]
    for row in records:
        definition=row["definition"]
        validate_graph(definition)
        if not _safe(definition):continue
        vocabulary=set(row["tags"]) | set(re.findall(r"[a-z0-9]{3,}",row["description"].casefold()))
        score=len(terms & vocabulary)
        if score:ranked.append((score,row["revision_id"],definition))
    if ranked:
        ranked.sort(key=lambda r:(-r[0],r[1]))
        graph=ranked[0][2]
        origin="reused:"+ranked[0][1]
    else:
        graph=None
        origin="seed"
        if generate is not None:
            from .logic import CAPABILITIES
            catalog={k:v for k,v in CAPABILITIES.items() if k in SAFE_CAPABILITIES}
            instructions=(
              "Return only a JSON object with inputs, nodes and outputs. "
              "Available typed operations: "+json.dumps(catalog)+
              ". Graph input must be a single text port, output a single view port. "
              "Maximum 16 nodes, no side effects. Goal: "+goal)
            try:
                text=generate(instructions,"Produce a valid bounded graph JSON.")
                proposal=json.loads(text)
                validate_graph(proposal)
                if len(proposal["nodes"])<=16 and _safe(proposal):
                    graph=proposal
                    origin="model"
            except (ValueError,KeyError,TypeError,RuntimeError,OSError):
                pass
        if graph is None:graph=candidate_graph(goal)
    return {"definition":graph,"description":goal,"source":origin,
            "required_capabilities":sorted({n["capability"] for n in graph["nodes"]})}

def install(db, proposal:dict, scope:str, event_id:int) -> str:
    """Immutable, validated, content-addressed graph revision."""
    import hashlib
    graph=proposal["definition"]
    validate_graph(graph)
    if not _safe(graph):
        raise ValueError("composed graph requires read-only, typed input/output ports")
    raw=json.dumps(graph,sort_keys=True,separators=(',',':'))
    revision="composed."+hashlib.sha256(raw.encode()).hexdigest()[:32]
    db.execute("INSERT INTO graph_revisions(id,definition) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",(revision,raw))
    db.execute("INSERT INTO graph_catalog(revision_id,description,tags) VALUES (%s,%s,ARRAY['composed']) ON CONFLICT DO NOTHING",
               (revision,proposal["description"][:2000]))
    # The same operation becomes discoverable in CLI, TUI, GUI and MCP without
    # changing client code. Mutating capabilities are never auto-published.
    action_id="living.composed."+revision.rsplit(".",1)[-1]
    db.execute("""
        INSERT INTO control_actions(id,title,graph_revision,input_name)
        VALUES(%s,%s,%s,'text') ON CONFLICT DO NOTHING
    """,(action_id,"Composed: "+proposal["description"][:160],revision))
    db.execute("INSERT INTO graph_proposals(id,source_event_id,scope_id,definition,accepted_revision) VALUES (%s,%s,%s,%s::jsonb,%s)",
               (uuid.uuid4(),event_id,scope,raw,revision))
    return revision
