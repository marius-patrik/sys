#!/usr/bin/env python3
"""Live HTTP smoke test. Requires a running development core."""
import json
import os
import time
import uuid
import urllib.request
BASE=os.getenv("LIVING_URL","http://127.0.0.1:8080").rstrip("/")
def call(method,path,payload=None):
    body=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=body,method=method,
      headers={"Content-Type":"application/json","Idempotency-Key":uuid.uuid4().hex})
    with urllib.request.urlopen(req,timeout=8) as result:
        if path=="/":return result.read().decode()
        return json.load(result)
def wait(event_id):
    for _ in range(100):
        e=call("GET","/v1/events/"+str(event_id))
        acts=e["activations"]
        if acts and all(a["state"] in ("completed","failed") for a in acts):
            if any(a["state"]!="completed" for a in acts):raise AssertionError(e)
            return acts[0]["result"]
        time.sleep(0.1)
    raise TimeoutError("event "+str(event_id)+" did not complete")
def main():
    for attempt in range(100):
        try:
            if call("GET","/healthz")["status"]=="ok":break
        except OSError: time.sleep(0.1)
    else:raise RuntimeError("core not reachable")
    unique="smoke"+uuid.uuid4().hex[:12]
    memory=call("POST","/v1/control/actions/living.remember",{"text":unique+" is a memorable test fact"})
    wait(memory["event_id"])
    retrieved=call("POST","/v1/control/actions/living.recall",{"text":unique})
    text=wait(retrieved["event_id"])["view"]["value"]
    assert unique in text,text
    graph=call("POST","/v1/control/actions/living.compose",{"text":"explain a model and memory"})
    wait(graph["event_id"])
    catalog=call("GET","/v1/control/catalog")["actions"]
    assert any(a["id"].startswith("living.composed.") for a in catalog)
    mcp=call("POST","/mcp",{"jsonrpc":"2.0","id":1,"method":"tools/list"})
    assert any(a["name"]=="living.recall" for a in mcp["result"]["tools"])
    root=call("GET","/v1/control/views/interface.root")
    assert root["definition"]["type"]=="container"
    assert "Living Intelligence" in call("GET","/")
    print("PASS: HTTP -> PostgreSQL -> graph -> memory -> catalog -> MCP -> GUI")
if __name__=="__main__":main()
