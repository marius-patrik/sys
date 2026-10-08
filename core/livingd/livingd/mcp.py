"""Dynamic MCP adapter over the same Control actions and PostgreSQL state."""
from __future__ import annotations
import uuid
from .database import connect
from .engine import ingest
from .registry import catalog

def actions(principal):
    with connect() as db:
        actions=db.execute("SELECT id,title,input_name,revision,graph_revision FROM control_actions WHERE enabled AND (scope_id IS NULL OR scope_id=%s) ORDER BY id",(principal["scope_id"],)).fetchall()
        graphs=db.execute("SELECT id,definition FROM graph_revisions WHERE id=ANY(%s)",([a["graph_revision"] for a in actions],)).fetchall()
        definitions={r["id"]:r["definition"] for r in graphs}
        manifest=catalog(db)
        grants=set(principal["grants"])
        return [a for a in actions if all(
            cap["capability"] in manifest and set(manifest[cap["capability"]]["grants"])<=grants
            for cap in definitions.get(a["graph_revision"],{}).get("nodes",[]))]

def handle(request,principal):
    if not isinstance(request,dict):raise ValueError("MCP JSON-RPC request must be an object")
    method=request.get("method")
    rid=request.get("id")
    params=request.get("params") or {}
    if method=="notifications/initialized" or (rid is None and method and method.startswith("notifications/")):
        return None
    if not isinstance(params,dict):raise ValueError("bad MCP params")
    result=None
    if method=="initialize":
        result={"protocolVersion":"2025-03-26","serverInfo":{"name":"living-sys","version":"0.29-dev"},
                "capabilities":{"tools":{"listChanged":True},"resources":{"listChanged":True},"prompts":{"listChanged":False}}}
    elif method=="ping":result={}
    elif method=="tools/list":
        result={"tools":[{"name":a["id"],"title":a["title"],"description":a["title"],
             "inputSchema":{"type":"object","properties":{a["input_name"]:{"type":"string"}},
                            "required":[a["input_name"]],"additionalProperties":False}} for a in actions(principal)]}
    elif method=="tools/call":
        name=params.get("name")
        args=params.get("arguments") or {}
        action=next((a for a in actions(principal) if a["id"]==name),None)
        if action is None or not isinstance(args,dict) or not isinstance(args.get(action["input_name"]),str):
            return response(rid,error={"code":-32602,"message":"Unknown action or invalid arguments"})
        text=args[action["input_name"]]
        eid=ingest("control.action","mcp."+principal["id"]+"."+name,str(uuid.uuid4()),principal["scope_id"],
                   {"action_id":name,"text":text},principal_id=principal["id"])
        result={"content":[{"type":"text","text":"Accepted event "+str(eid)+"; inspect living://event/"+str(eid)}],
                "structuredContent":{"event_id":eid,"resource_uri":"living://event/"+str(eid)},"isError":False}
    elif method=="resources/list":
        with connect() as db:
            views=db.execute("SELECT id FROM control_views ORDER BY id").fetchall()
        result={"resources":[{"uri":"living://view/"+v["id"],"name":v["id"],"mimeType":"application/json"} for v in views]}
    elif method=="resources/read":
        uri=params.get("uri","")
        if not isinstance(uri,str):raise ValueError("invalid resource URI")
        with connect() as db:
            if uri.startswith("living://view/"):
                name=uri.removeprefix("living://view/")
                row=db.execute("SELECT definition FROM control_views WHERE id=%s",(name,)).fetchone()
                value=row["definition"] if row else None
            elif uri.startswith("living://event/"):
                try:eid=int(uri.removeprefix("living://event/"))
                except ValueError:raise ValueError("invalid event ID")
                value=db.execute("SELECT id,kind,payload,scope_id FROM events WHERE id=%s AND scope_id=%s",(eid,principal['scope_id'])).fetchone()
            else: value=None
        if value is None:return response(rid,error={"code":-32602,"message":"Resource not found"})
        import json
        result={"contents":[{"uri":uri,"mimeType":"application/json","text":json.dumps(value,default=str)}]}
    elif method=="prompts/list":result={"prompts":[]}
    else:return response(rid,error={"code":-32601,"message":"Method not found"})
    return response(rid,result=result)

def response(rid,result=None,error=None):
    data={"jsonrpc":"2.0","id":rid}
    if error is not None:data["error"]=error
    else:data["result"]=result
    return data
