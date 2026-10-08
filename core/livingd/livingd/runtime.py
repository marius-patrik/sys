"""One graph-node dispatcher. DSH is the model provider, not an agent loop."""
from __future__ import annotations
import json
import os
import urllib.request
from .database import connect
from .logic import execute_pure
from .memory import context,normalize
from .composer import dispatch, propose

def model(question:str,ctx:str)->str:
    bridge=os.getenv("LIVING_DSH_URL","").rstrip("/")
    if not bridge:
        raise RuntimeError("DSH node bridge unavailable: configure LIVING_DSH_URL")
    if not bridge.startswith(("http://127.0.0.1:","http://localhost:","https://","http://dsh:")):
        raise RuntimeError("refusing unapproved model bridge URL")
    request=urllib.request.Request(
      bridge+"/v1/nodes/model",
      data=json.dumps({"question":question,"context":ctx,"model":os.getenv("LIVING_MODEL","default")}).encode(),
      headers={"Content-Type":"application/json",
               "Authorization":"Bearer "+os.getenv("LIVING_DSH_TOKEN","local-dev-only")},
      method="POST")
    with urllib.request.urlopen(request,timeout=45) as response:
        result=json.load(response)
    if not isinstance(result,dict) or not isinstance(result.get("text"),str):
        raise RuntimeError("DSH bridge returned invalid model output")
    return result["text"][:12000]

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
        return {"value":"Graph proposal validated.","_proposal":propose(scope,args["value"],generate=model)}
    if capability=="program.python":
        bridge=os.getenv("LIVING_WORKER_URL","").rstrip("/")
        if not bridge:
            raise RuntimeError("OCI worker broker not configured")
        if not bridge.startswith(("http://127.0.0.1:","http://localhost:","https://","http://worker-oci:")):
            raise RuntimeError("refusing unapproved worker URL")
        req=urllib.request.Request(bridge+"/v1/execute",
            data=json.dumps({"language":"python","code":args["code"]}).encode(),
            headers={"Content-Type":"application/json",
                     "Authorization":"Bearer "+os.getenv("LIVING_WORKER_TOKEN","local-dev-only")},
            method="POST")
        with urllib.request.urlopen(req,timeout=25) as response:
            result=json.load(response)
        if not isinstance(result,dict) or not isinstance(result.get("stdout"),str):
            raise RuntimeError("OCI broker returned invalid output")
        if result.get("exit_code")!=0:
            raise RuntimeError("OCI program failed: "+str(result.get("stderr",""))[:700])
        return {"value":result["stdout"][:12000]}
    if capability=="model.answer":
        return {"value":model(args["question"],args["context"])}
    return execute_pure(capability,args)
