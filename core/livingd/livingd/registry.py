"""Runtime capability manifest. PostgreSQL is authoritative.

The fixed adapter families are protocol interpreters; the capabilities
bound to them, their typed ports, versions, and grants live in the DB.
"""
from __future__ import annotations
from .database import connect

ADAPTERS={"pure","memory","model","control","composer","oci","dsh","graph","goal"}
SEED_CAPABILITIES=[
  ("text.echo",{"value":"text"},{"value":"text"},"pure",{"operation":"echo"},"read",[]),
  ("text.upper",{"value":"text"},{"value":"text"},"pure",{"operation":"upper"},"read",[]),
  ("text.prefix",{"value":"text","prefix":"text"},{"value":"text"},"pure",{"operation":"prefix"},"read",[]),
  ("view.text",{"value":"text"},{"view":"view"},"pure",{"operation":"view"},"read",[]),
  ("memory.search",{"query":"text"},{"value":"text"},"memory",{"operation":"search"},"read",["memory.read"]),
  ("memory.remember",{"value":"text"},{"value":"text"},"memory",{"operation":"remember"},"write",["memory.write"]),
  ("memory.attend",{"value":"text"},{"view":"view"},"memory",{"operation":"attend"},"write",["memory.write"]),
  ("memory.approve",{"value":"text"},{"view":"view"},"memory",{"operation":"approve"},"write",["memory.write"]),
  ("model.answer",{"question":"text","context":"text"},{"value":"text"},"model",{"purpose":"answer"},"inference",["model.use"]),
  ("input.dispatch",{"value":"text"},{"view":"view"},"control",{"operation":"dispatch"},"read",["control.invoke"]),
  ("graph.compose",{"value":"text"},{"value":"text"},"composer",{"purpose":"compose"},"inference",["graph.compose"]),
  ("program.python",{"code":"text"},{"value":"text"},"oci",{"runtime":"python"},"external",["worker.execute"]),
  ("dsh.tool",{"tool":"text","arguments":"text"},{"value":"text"},"dsh",{},"external",["tool.invoke"]),
  ("graph.call",{"revision":"text","text":"text"},{"value":"text"},"graph",{},"read",["graph.invoke"]),
  ("goal.create",{"title":"text"},{"view":"view"},"goal",{},"write",["goal.manage"]),
]

def validate_adapter_contract(entry:dict)->None:
    """Enforce the interpreter's actual effect and permission contracts."""
    adapter=entry.get("adapter")
    config=entry.get("config")
    if not isinstance(config,dict):
        raise ValueError("adapter config must be a JSON object")
    if adapter=="model":
        if set(config)!={"purpose"} or config["purpose"] not in ("answer","compose","default","memory"):
            raise ValueError("unsupported LiteLLM model purpose")
        example=next(row for row in SEED_CAPABILITIES if row[3]=="model")
    else:
        example=next((row for row in SEED_CAPABILITIES
                      if row[3]==adapter and row[4]==config),None)
    if example is None:raise ValueError("unsupported adapter configuration")
    _,inputs,outputs,_,_,effect,minimum=example
    if entry.get("in")!=inputs or entry.get("out")!=outputs:
        raise ValueError("capability ports differ from the adapter contract")
    if entry.get("effect")!=effect:
        raise ValueError("capability effect differs from the adapter contract")
    grants=entry.get("grants")
    if not isinstance(grants,(list,tuple)) or not set(minimum)<=set(grants):
        raise ValueError("capability omits mandatory adapter grants")

def seeds():
    return {row[0]:{"in":row[1],"out":row[2],
       "adapter":row[3],"config":row[4],"effect":row[5],"grants":row[6],"revision":1}
       for row in SEED_CAPABILITIES}

def catalog(db=None)->dict:
    if db is None:
        with connect() as local: return catalog(local)
    rows=db.execute("""
        SELECT DISTINCT ON (id) id,revision,input_ports,output_ports,adapter,adapter_config,effect,required_grants
        FROM capability_registry WHERE active ORDER BY id,revision DESC
    """).fetchall()
    result={}
    for r in rows:
        if r["adapter"] not in ADAPTERS:continue
        entry={"in":r["input_ports"],"out":r["output_ports"],
           "adapter":r["adapter"],"config":r["adapter_config"],
           "effect":r["effect"],"grants":r["required_grants"],"revision":r["revision"]}
        try:validate_adapter_contract(entry)
        except ValueError:
            # The database may contain a malformed unpublished revision.
            # It must never become an executable or allegedly safe capability.
            continue
        result[r["id"]]=entry
    return result

def authorize(scope:str,entry:dict,grants:set[str])->None:
    missing=set(entry["grants"])-grants
    if missing:raise PermissionError("capability requires grants: "+",".join(sorted(missing)))
