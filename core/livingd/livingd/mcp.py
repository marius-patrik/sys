"""Database-backed MCP projection. 2026-07-28 stateless protocol and Tasks extension.

Legacy initialize is accepted for clients using the 2025-03-26 transport.
"""
from __future__ import annotations
import json
import uuid
from .database import connect
from .engine import ingest
from .registry import catalog

PROTOCOL="2026-07-28"
LEGACY="2025-03-26"

def actions(principal):
    with connect() as db:
        rows=db.execute("""
          SELECT id,title,input_name,revision,graph_revision FROM control_actions
          WHERE enabled AND (scope_id IS NULL OR scope_id=%s) ORDER BY id
        """,(principal["scope_id"],)).fetchall()
        graphs=db.execute("SELECT id,definition FROM graph_revisions WHERE id=ANY(%s)",
                          ([a["graph_revision"] for a in rows],)).fetchall()
        manifest=catalog(db)
    graphmap={g["id"]:g["definition"] for g in graphs}
    grants=set(principal["grants"])
    return [a for a in rows if all(
      n["capability"] in manifest and set(manifest[n["capability"]]["grants"])<=grants
      for n in graphmap.get(a["graph_revision"],{}).get("nodes",[]))]

def _lineage(db,event_id,scope):
    base="""
      WITH RECURSIVE lineage AS (
        SELECT id,0 depth FROM events WHERE id=%s AND scope_id=%s
        UNION ALL SELECT e.id,l.depth+1 FROM events e
          JOIN lineage l ON e.causation_event_id=l.id
          WHERE e.scope_id=%s AND l.depth<32
      )
    """
    entries=db.execute(base+"""
      SELECT a.id,a.state,a.result,a.updated_at FROM activations a JOIN lineage l ON a.event_id=l.id
      WHERE a.scope_id=%s ORDER BY a.created_at
    """,(event_id,scope,scope,scope)).fetchall()
    pending=db.execute(base+"""
      SELECT (SELECT count(*) FROM event_routing q JOIN lineage l ON l.id=q.event_id
              WHERE q.state!='routed')+
             (SELECT count(*) FROM event_deliveries d JOIN lineage l ON l.id=d.event_id
              WHERE d.state IN ('pending','leased')) n
    """,(event_id,scope,scope)).fetchone()
    return entries,pending["n"]

def _task(db,task_id,principal):
    try:uid=uuid.UUID(task_id)
    except (ValueError,TypeError):return None
    return db.execute("""
      SELECT id,event_id,created_at,cancelled_at FROM mcp_tasks
      WHERE id=%s AND scope_id=%s
    """,(uid,principal["scope_id"])).fetchone()

def _state(db,task,principal):
    runs,pending=_lineage(db,task["event_id"],principal["scope_id"])
    failed=any(x["state"]=="failed" for x in runs)
    cancelled=any(x["state"]=="cancelled" for x in runs)
    active=bool(pending) or not runs or any(
        x["state"] in ("pending","running","suspended") for x in runs)
    status="failed" if failed else "cancelled" if cancelled or task["cancelled_at"] else "working" if active else "completed"
    completed=[a["result"] for a in runs if a["state"]=="completed" and a["result"]]
    value=completed[-1] if completed else None
    if value and isinstance(value.get("view"),dict):value=value["view"].get("value",value)
    if value is None:value="Task did not produce a view"
    updated=max((x["updated_at"] for x in runs),default=task["created_at"])
    return {"resultType":"complete","taskId":str(task["id"]),"status":status,
            "createdAt":task["created_at"].isoformat(),"lastUpdatedAt":updated.isoformat(),
            "ttlMs":None,"pollIntervalMs":1000,
            **({"result":{"content":[{"type":"text","text":str(value)}],
                          "isError":failed}} if status in ("completed","failed") else {})}

def response(rid,result=None,error=None,modern=False):
    data={"jsonrpc":"2.0","id":rid}
    if error is not None:data["error"]=error
    else:
        if modern and isinstance(result,dict):
            result={**result,"_meta":{"io.modelcontextprotocol/serverInfo":{
                    "name":"living-sys","version":"0.30-dev"}}}
        data["result"]=result
    return data

def handle(request,principal):
    if not isinstance(request,dict):raise ValueError("JSON-RPC object required")
    method=request.get("method")
    rid=request.get("id")
    params=request.get("params") or {}
    if not isinstance(params,dict):raise ValueError("invalid params")
    meta=params.get("_meta") or {}
    if not isinstance(meta,dict):raise ValueError("invalid per-request metadata")
    modern=meta.get("io.modelcontextprotocol/protocolVersion")==PROTOCOL
    if method=="notifications/initialized" or (rid is None and str(method).startswith("notifications/")):
        return None
    if method=="initialize":
        return response(rid,{"protocolVersion":LEGACY,
              "serverInfo":{"name":"living-sys","version":"0.30-dev"},
              "capabilities":{"tools":{"listChanged":False},
                              "resources":{"listChanged":False},"prompts":{}}})
    if method=="ping":return response(rid,{},modern=modern)
    if method=="tools/list":
        items=[{"name":a["id"],"title":a["title"],"description":a["title"],
              "inputSchema":{"type":"object",
                "properties":{a["input_name"]:{"type":"string"}},
                "required":[a["input_name"]],"additionalProperties":False}}
              for a in actions(principal)]
        return response(rid,{"tools":items},modern=modern)
    if method=="tools/call":
        name=params.get("name")
        action=next((a for a in actions(principal) if a["id"]==name),None)
        arguments=params.get("arguments") or {}
        if action is None or not isinstance(arguments,dict) or not isinstance(arguments.get(action["input_name"]),str):
            return response(rid,error={"code":-32602,"message":"Unknown action or invalid arguments"})
        event_id=ingest("control.action","mcp."+principal["id"]+"."+name,str(uuid.uuid4()),principal["scope_id"],
              {"action_id":name,"text":arguments[action["input_name"]]},principal_id=principal["id"])
        caps=meta.get("io.modelcontextprotocol/clientCapabilities") or {}
        task_support=isinstance(caps,dict) and isinstance(caps.get("extensions"),dict) and (
            "io.modelcontextprotocol/tasks" in caps["extensions"])
        if modern and task_support:
            task_id=uuid.uuid5(uuid.NAMESPACE_URL,"living-mcp:"+principal["scope_id"]+":"+str(event_id))
            with connect() as db:
                db.execute("""
                  INSERT INTO mcp_tasks(id,event_id,scope_id,principal_id)
                  VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING
                """,(task_id,event_id,principal["scope_id"],principal["id"]))
                row=_task(db,str(task_id),principal)
            return response(rid,{"resultType":"task","taskId":str(task_id),
                "status":"working","createdAt":row["created_at"].isoformat(),
                "lastUpdatedAt":row["created_at"].isoformat(),"ttlMs":None,"pollIntervalMs":1000},modern=True)
        # Old clients receive the durable handle and may retrieve its resource.
        return response(rid,{"content":[{"type":"text","text":"Accepted event "+str(event_id)+
                "; inspect living://event/"+str(event_id)}],
            "structuredContent":{"event_id":event_id,"resource_uri":"living://event/"+str(event_id)},
            "isError":False},modern=modern)
    if method=="tasks/get":
        with connect() as db:
            task=_task(db,params.get("taskId"),principal)
            if not task:return response(rid,error={"code":-32602,"message":"Task not found"})
            status=_state(db,task,principal)
        return response(rid,status,modern=modern)
    if method=="tasks/cancel":
        with connect() as db:
            task=_task(db,params.get("taskId"),principal)
            if not task:return response(rid,error={"code":-32602,"message":"Task not found"})
            current=_state(db,task,principal)
            if current["status"] in ("completed","failed","cancelled"):
                return response(rid,current,modern=modern)
            db.execute("UPDATE mcp_tasks SET cancelled_at=now() WHERE id=%s",(task["id"],))
            runs,_=_lineage(db,task["event_id"],principal["scope_id"])
            for run in runs:
                db.execute("UPDATE activations SET state='cancelled',cancelled_at=now() WHERE id=%s AND state IN ('pending','running','suspended')",(run["id"],))
                db.execute("UPDATE execution_effects SET state='uncertain',updated_at=now() WHERE activation_id=%s AND state='running'",(run["id"],))
            task=_task(db,params.get("taskId"),principal)
            status=_state(db,task,principal)
        return response(rid,status,modern=modern)
    if method=="resources/list":
        with connect() as db:
            views=db.execute("SELECT id FROM control_views ORDER BY id").fetchall()
        return response(rid,{"resources":[{"uri":"living://view/"+v["id"],
            "name":v["id"],"mimeType":"application/json"} for v in views]},modern=modern)
    if method=="resources/read":
        uri=params.get("uri","")
        if not isinstance(uri,str):raise ValueError("invalid resource URI")
        with connect() as db:
            if uri.startswith("living://view/"):
                row=db.execute("SELECT definition FROM control_views WHERE id=%s",(uri.removeprefix("living://view/"),)).fetchone()
                data=row["definition"] if row else None
            elif uri.startswith("living://event/"):
                try:event_id=int(uri.removeprefix("living://event/"))
                except ValueError:raise ValueError("invalid event identifier")
                data=db.execute("""
                  SELECT id,kind,payload,scope_id FROM events WHERE id=%s AND scope_id=%s
                """,(event_id,principal["scope_id"])).fetchone()
                if data:
                    runs,pending=_lineage(db,event_id,principal["scope_id"])
                    data={**data,"activations":runs,"pending":pending}
            else:data=None
        if data is None:return response(rid,error={"code":-32602,"message":"Resource not found"})
        return response(rid,{"contents":[{"uri":uri,"mimeType":"application/json",
            "text":json.dumps(data,default=str)}]},modern=modern)
    if method=="prompts/list":return response(rid,{"prompts":[]},modern=modern)
    return response(rid,error={"code":-32601,"message":"Method not found"})
