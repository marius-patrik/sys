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
