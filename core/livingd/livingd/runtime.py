"""One graph-node dispatcher. DSH is the model provider, not an agent loop."""
from __future__ import annotations
import json
import os
import urllib.request
from .database import connect
from .logic import execute_pure
from .memory import context
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

def execute(capability:str,args:dict,scope:str)->dict:
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
