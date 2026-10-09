"""Database-native, versioned capabilities and graph publishing."""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from .logic import validate_graph
from .registry import ADAPTERS,catalog,authorize,validate_adapter_contract

IDENT=re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
PORTS={"text","view"}
PURE={"echo","upper","prefix","view"}

def _input_schema(obj,label):
    if not isinstance(obj,dict) or len(obj)>20:
        raise ValueError(label+" ports must be an object with at most 20 entries")
    for key,val in obj.items():
        if not isinstance(key,str) or not IDENT.fullmatch("p."+key) or val not in PORTS:
            raise ValueError("invalid typed port: "+str(key))
    return obj

def register_capability(db,principal:dict,data:dict)->dict:
    name=data.get("id")
    if not isinstance(name,str) or not IDENT.fullmatch(name):
        raise ValueError("invalid capability id")
    adapter=data.get("adapter")
    if adapter not in ADAPTERS:raise ValueError("unsupported capability adapter")
    inputs=_input_schema(data.get("inputs"),"input")
    outputs=_input_schema(data.get("outputs"),"output")
    config=data.get("config",{})
    if not isinstance(config,dict):raise ValueError("adapter config must be an object")
    effect=data.get("effect","read")
    if effect not in {"read","inference","write","external"}:raise ValueError("invalid effect")
    if adapter=="pure" and config.get("operation") not in PURE:raise ValueError("unknown pure operation")
    if adapter=="model" and config.get("purpose") not in {"answer","compose","default"}:raise ValueError("invalid model purpose")
    if adapter=="oci" and config.get("runtime")!="python":raise ValueError("unsupported sandbox runtime")
    grants=data.get("grants",[])
    if not isinstance(grants,list) or len(grants)>20 or any(not isinstance(g,str) for g in grants):
        raise ValueError("invalid grants")
    validate_adapter_contract({"adapter":adapter,"config":config,
                               "in":inputs,"out":outputs,
                               "effect":effect,"grants":grants})
    if not set(grants)<=set(principal["grants"]):raise PermissionError("requested capability escalates privileges")
    row=db.execute("SELECT max(revision) version FROM capability_registry WHERE id=%s",(name,)).fetchone()
    expected=(row["version"] or 0)+1
    if row["version"] is not None and "revision" not in data:
        raise ValueError("an explicit revision is required to publish an update")
    version=data.get("revision",expected)
    if version!=expected:raise ValueError("capability revision must be next version")
    db.execute("""
      INSERT INTO capability_registry(id,revision,input_ports,output_ports,adapter,adapter_config,effect,required_grants)
      VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s)
    """,(name,version,json.dumps(inputs),json.dumps(outputs),adapter,json.dumps(config),effect,grants))
    return {"id":name,"revision":version}

def register_graph(db,principal:dict,data:dict)->dict:
    graph=data.get("definition")
    if not isinstance(graph,dict):raise ValueError("graph definition required")
    if graph.get("inputs")!={"text":"text"} or set(graph.get("outputs",{}))!={"view"}:
        raise ValueError("Control actions require text input and view output")
    manifest=catalog(db)
    validate_graph(graph,manifest)
    for node in graph["nodes"]:
        authorize(principal["scope_id"],manifest[node["capability"]],set(principal["grants"]))
    bound=graph["outputs"]["view"]
    producer=next(n for n in graph["nodes"] if n["id"]==bound["node"])
    if manifest[producer["capability"]]["out"].get(bound["port"])!="view":
        raise ValueError("Control graph output must be a view")
    title=data.get("title")
    if not isinstance(title,str) or not (1<=len(title)<=160):raise ValueError("invalid title")
    raw=json.dumps(graph,sort_keys=True,separators=(",",":"))
    rev="registered."+hashlib.sha256((principal["scope_id"]+":"+raw).encode()).hexdigest()[:32]
    action_id="living.registered."+rev.rsplit(".",1)[-1]
    effects={manifest[node["capability"]]["effect"] for node in graph["nodes"]}
    risk="external" if "external" in effects else "write" if "write" in effects else "read"
    db.execute("INSERT INTO graph_revisions(id,definition) VALUES(%s,%s::jsonb) ON CONFLICT DO NOTHING",(rev,raw))
    db.execute("""
      INSERT INTO graph_catalog(revision_id,description,tags,scope_id,risk)
      VALUES(%s,%s,ARRAY['registered'],%s,%s) ON CONFLICT DO NOTHING
    """,(rev,title,principal["scope_id"],risk))
    db.execute("""
      INSERT INTO control_actions(id,title,graph_revision,input_name,scope_id)
      VALUES(%s,%s,%s,'text',%s) ON CONFLICT DO NOTHING
    """,(action_id,title,rev,principal["scope_id"]))
    return {"action_id":action_id,"graph_revision":rev,"scope_id":principal["scope_id"]}
