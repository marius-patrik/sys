"""Graph node dispatcher; model inventory and inference come from LiteLLM."""
from __future__ import annotations
import json
import os
import urllib.request
from .database import connect
from .logic import execute_pure
from .memory import context,normalize
from .composer import dispatch, propose

from .models import generate,read_credential

def model(question:str,ctx:str,scope:str="dev",purpose:str="answer")->str:
    return generate(scope,purpose,question,ctx)

def _worker_key(scope:str)->str:
    with connect() as db:
        secret=read_credential(db,scope,"worker.token")
    if not secret:
        raise RuntimeError("worker broker credential not stored in PostgreSQL")
    return secret

def execute(capability:str,args:dict,scope:str,cause:int|None=None)->dict:
    if capability=="memory.attend":
        if cause is None:raise ValueError("attention requires an event source")
        value=args["value"].strip()
        with connect() as db:
            event=db.execute("SELECT kind FROM events WHERE id=%s AND scope_id=%s",(cause,scope)).fetchone()
            if not event:raise ValueError("invalid attention source")
            kind=event["kind"]
            policy=db.execute("SELECT id,revision,min_length FROM attention_policies WHERE event_kind=%s AND enabled",(kind,)).fetchone()
            if not policy:raise ValueError("no attention policy")
            existing=db.execute("SELECT id FROM memory_claims WHERE scope_id=%s AND normalized_key=%s",(scope,normalize(value))).fetchone()
        choice="ignore" if len(value)<policy["min_length"] or existing else "candidate"
        reason="short-or-known" if choice=="ignore" else "new-observation"
        return {"view":{"type":"text","value":"Memory attention: "+choice},
                "_attention":{"choice":choice,"reason":reason,"content":value,
                              "policy_id":policy["id"],"policy_revision":policy["revision"]}}
    if capability=="memory.approve":
        import uuid
        candidate_id=str(uuid.UUID(args["value"].strip()))
        with connect() as db:
            candidate=db.execute("SELECT id FROM memory_candidates WHERE id=%s AND scope_id=%s AND state='pending'",
                                 (candidate_id,scope)).fetchone()
        if not candidate:raise ValueError("candidate not found or already handled")
        return {"view":{"type":"text","value":"Approved memory candidate "+candidate_id},
                "_approve_memory":candidate_id}
    if capability=="memory.search":
        return {"value":context(scope,args["query"])}
    if capability=="memory.remember":
        return {"value":"Memory committed.","_memory_write":args["value"]}
    if capability=="input.dispatch":
        action,text=dispatch(scope,args["value"])
        return {"view":{"type":"progress","value":"Dispatched to "+action},
                "_dispatch":{"action_id":action,"text":text}}
    if capability=="graph.compose":
        return {"value":"Graph proposal validated.","_proposal":propose(scope,args["value"],generate=lambda q,c: model(q,c,scope,"compose"))}
    if capability=="program.python":
        bridge=os.getenv("LIVING_WORKER_URL","").rstrip("/")
        if not bridge:
            raise RuntimeError("OCI worker broker not configured")
        if not bridge.startswith(("http://127.0.0.1:","http://localhost:","https://","http://worker-oci:")):
            raise RuntimeError("refusing unapproved worker URL")
        req=urllib.request.Request(bridge+"/v1/execute",
            data=json.dumps({"language":"python","code":args["code"]}).encode(),
            headers={"Content-Type":"application/json",
                     "Authorization":"Bearer "+_worker_key(scope)},
            method="POST")
        with urllib.request.urlopen(req,timeout=25) as response:
            result=json.load(response)
        if not isinstance(result,dict) or not isinstance(result.get("stdout"),str):
            raise RuntimeError("OCI broker returned invalid output")
        if result.get("exit_code")!=0:
            raise RuntimeError("OCI program failed: "+str(result.get("stderr",""))[:700])
        return {"value":result["stdout"][:12000]}
    if capability=="model.answer":
        return {"value":model(args["question"],args["context"],scope,"answer")}
    return execute_pure(capability,args)


def execute_registered(capability:str,args:dict,scope:str,cause:int|None,manifest:dict,activation_id=None,node_id=None)->dict:
    """Interpret a versioned DB capability. New instances require no Python dispatch edit."""
    import json
    from .models import read_credential
    from .registry import ADAPTERS
    adapter=manifest["adapter"]
    config=manifest["config"]
    if adapter not in ADAPTERS:raise RuntimeError("unsupported capability adapter")
    if set(args)!=set(manifest["in"]):raise ValueError("invalid capability inputs")
    if adapter=="pure":
        op=config.get("operation")
        if op=="echo":return {"value":args["value"]}
        if op=="upper":return {"value":args["value"].upper()}
        if op=="prefix":return {"value":args["prefix"]+args["value"]}
        if op=="view":return {"view":{"type":"text","value":args["value"]}}
        raise ValueError("unknown pure adapter operation")
    if adapter=="memory":
        op=config.get("operation")
        canonical={"search":"memory.search","remember":"memory.remember",
                   "attend":"memory.attend","approve":"memory.approve"}.get(op)
        if not canonical:raise ValueError("unknown memory operation")
        return execute(canonical,args,scope,cause)
    if adapter=="model":
        return {"value":model(args["question"],args["context"],scope,config.get("purpose","answer"))}
    if adapter=="composer":
        return execute("graph.compose",args,scope,cause)
    if adapter=="control":
        return execute("input.dispatch",args,scope,cause)
    if adapter=="oci":
        if config.get("runtime")!="python":raise ValueError("unsupported OCI runtime")
        return execute("program.python",args,scope,cause)
    if adapter=="goal":
        title=args["title"].strip()
        if not 1<=len(title)<=200:raise ValueError("goal must be 1..200 characters")
        return {"view":{"type":"text","value":"Goal recorded: "+title},
                "_goal_create":{"title":title}}
    if adapter=="graph":
        from .registry import catalog, authorize
        from .logic import validate_graph
        with connect() as db:
            parent=db.execute("SELECT depth,max_depth,grants FROM activations WHERE id=%s AND scope_id=%s",(activation_id,scope)).fetchone()
            target=db.execute("SELECT definition FROM graph_revisions WHERE id=%s",(args["revision"],)).fetchone()
            if not parent or not target:raise ValueError("graph activation not found")
            if parent["depth"]>=parent["max_depth"]:raise ValueError("nested graph depth exceeded")
            manifest=catalog(db)
            validate_graph(target["definition"],manifest)
            if target["definition"].get("inputs")!={"text":"text"}:raise ValueError("child needs a text input")
            for node in target["definition"]["nodes"]:
                authorize(scope,manifest[node["capability"]],set(parent["grants"]))
        return {"_child_activation":{"revision":args["revision"],"text":args["text"]}}
    if adapter=="dsh":
        import urllib.parse
        with connect() as db:
            record=db.execute("""
                SELECT base_url,credential_name FROM integration_endpoints
                WHERE scope_id=%s AND service='dsh'
            """,(scope,)).fetchone()
            if not record:raise RuntimeError("DSH tool bridge not configured in DB")
            secret=read_credential(db,scope,record["credential_name"])
        if not secret:raise RuntimeError("DSH bridge credential not in DB")
        url=record["base_url"]
        if not (url.startswith("https://") or url.startswith("http://127.0.0.1:")
                or url.startswith("http://localhost:") or url.startswith("http://dsh:")):
            raise ValueError("unsafe DSH tool bridge URL")
        arguments=json.loads(args["arguments"])
        if not isinstance(arguments,dict):raise ValueError("DSH tool arguments must be an object")
        req=urllib.request.Request(url.rstrip("/")+"/v1/nodes/tool",
          data=json.dumps({"name":args["tool"],"arguments":arguments,
              "call_id":str(activation_id)+":"+str(node_id)}).encode(),
          headers={"Content-Type":"application/json","Authorization":"Bearer "+secret},
          method="POST")
        with urllib.request.urlopen(req,timeout=45) as response:
            item=json.load(response)
        if "outcome" not in item:raise ValueError("invalid DSH tool outcome")
        return {"value":json.dumps(item["outcome"],default=str)[:12000]}
    raise RuntimeError("capability adapter unavailable")
