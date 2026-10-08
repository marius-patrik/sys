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
        result[r["id"]]={"in":r["input_ports"],"out":r["output_ports"],
           "adapter":r["adapter"],"config":r["adapter_config"],
           "effect":r["effect"],"grants":r["required_grants"],"revision":r["revision"]}
    return result

def authorize(scope:str,entry:dict,grants:set[str])->None:
    missing=set(entry["grants"])-grants
    if missing:raise PermissionError("capability requires grants: "+",".join(sorted(missing)))
