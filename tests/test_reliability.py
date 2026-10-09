"""Fault-injection and live Control tests against PostgreSQL 16."""
import hashlib
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'/'livingd'))

@unittest.skipUnless(os.getenv("LIVING_TEST_DATABASE_URL"),"requires PostgreSQL CI service")
class CorrectnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIVING_DATABASE_URL"]=os.environ["LIVING_TEST_DATABASE_URL"]
        os.environ.pop("LIVING_DATABASE_URL_FILE",None)
        os.environ.setdefault("LIVING_BOOTSTRAP_TOKEN","test-control-token-0123456789-abcdef")
        from livingd.database import migrate,seed
        migrate();seed()
        from livingd.__main__ import Handler
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()

    def drain(self,steps=400):
        from livingd.engine import tick
        for _ in range(steps):
            if not tick():break

    def test_registry_adds_runnable_capability_without_code_changes(self):
        from livingd.database import connect
        from livingd.registry import catalog
        from livingd.logic import validate_graph
        from livingd.engine import ingest
        self.drain()
        key=uuid.uuid4().hex
        cap="test.dynamic."+key
        graph_id="test.graph."+key
        action_id="test.action."+key
        graph={"inputs":{"text":"text"},
            "nodes":[
                {"id":"one","capability":cap,"inputs":{"value":{"input":"text"}}},
                {"id":"view","capability":"view.text","inputs":{"value":{"node":"one","port":"value"}}}
            ],"outputs":{"view":{"node":"view","port":"view"}}}
        with connect() as db:
            db.execute("""
                INSERT INTO capability_registry(id,revision,input_ports,output_ports,adapter,adapter_config,effect)
                VALUES(%s,1,'{"value":"text"}','{"value":"text"}','pure','{"operation":"upper"}','read')
            """,(cap,))
            validate_graph(graph,catalog(db))
            db.execute("INSERT INTO graph_revisions(id,definition) VALUES (%s,%s::jsonb)",
                       (graph_id,json.dumps(graph)))
            db.execute("INSERT INTO control_actions(id,title,graph_revision,input_name) VALUES(%s,'Dynamic test',%s,'text')",
                       (action_id,graph_id))
        event=ingest("control.action","reliability",key,"dev",{"action_id":action_id,"text":"hello"})
        self.drain()
        with connect() as db:
            result=db.execute("SELECT state,result,capability_pins FROM activations WHERE event_id=%s",(event,)).fetchone()
        self.assertEqual(result["state"],"completed",result)
        self.assertEqual(result["result"]["view"]["value"],"HELLO")
        self.assertIn(cap,result["capability_pins"])

    def test_expired_node_fences_old_attempt(self):
        from livingd.engine import ingest,route_once,deliver_once,_claim_node
        from livingd.database import connect
        self.drain()
        key=uuid.uuid4().hex
        event=ingest("control.action","reliability",key,"dev",
                     {"action_id":"living.upper","text":"fence"})
        self.assertTrue(route_once());self.assertTrue(deliver_once())
        first=_claim_node();self.assertEqual(first["node_id"],"upper")
        with connect() as db:
            db.execute("UPDATE node_runs SET lease_until=now()-interval '5 seconds' WHERE activation_id=%s AND node_id='upper'",
                       (first["activation_id"],))
        second=_claim_node();self.assertEqual(second["epoch"],first["epoch"]+1)
        with connect() as db:
            accepted=db.execute("""
                UPDATE node_runs SET state='failed' WHERE activation_id=%s AND node_id=%s
                AND lease_epoch=%s AND state='leased' AND lease_until>=now()
                RETURNING node_id
            """,(first["activation_id"],first["node_id"],first["epoch"])).fetchone()
            state=db.execute("SELECT state FROM activations WHERE id=%s",(first["activation_id"],)).fetchone()
        self.assertIsNone(accepted)
        self.assertEqual(state["state"],"running")
        with connect() as db:
            db.execute("UPDATE node_runs SET result=%s::jsonb,state='completed',lease_until=NULL WHERE activation_id=%s AND node_id='upper'",
                       (json.dumps({"value":"FENCE"}),first["activation_id"]))
        self.drain()
        with connect() as db:
            row=db.execute("SELECT state,result FROM activations WHERE id=%s",(first["activation_id"],)).fetchone()
        self.assertEqual(row["state"],"completed")
        self.assertEqual(row["result"]["view"]["value"],"FENCE")

    def test_capability_cannot_mislabel_mutation_as_read_only(self):
        from livingd.database import connect
        from livingd.registry import catalog
        import urllib.error
        suffix=uuid.uuid4().hex
        base="http://127.0.0.1:"+str(self.server.server_port)
        headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],
                 "Content-Type":"application/json"}
        def publish(body):
            req=urllib.request.Request(base+"/v1/control/capabilities",
                   method="POST",headers=headers,data=json.dumps(body).encode())
            with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
        mutation={"id":"test.mutation."+suffix,"adapter":"memory",
                  "config":{"operation":"remember"},
                  "inputs":{"value":"text"},"outputs":{"value":"text"},
                  "effect":"read","grants":[]}
        with self.assertRaises(urllib.error.HTTPError) as failure:
            publish(mutation)
        self.assertEqual(failure.exception.code,400)
        model={"id":"test.model."+suffix,"adapter":"model",
               "config":{"purpose":"answer"},
               "inputs":{"question":"text","context":"text"},"outputs":{"value":"text"},
               "effect":"read","grants":[]}
        with self.assertRaises(urllib.error.HTTPError) as failure:
            publish(model)
        self.assertEqual(failure.exception.code,400)
        wrong_port={"id":"test.ports."+suffix,"adapter":"pure",
                    "config":{"operation":"upper"},
                    "inputs":{"value":"text"},"outputs":{"view":"view"},
                    "effect":"read","grants":[]}
        with self.assertRaises(urllib.error.HTTPError) as failure:
            publish(wrong_port)
        self.assertEqual(failure.exception.code,400)
        safe={**mutation,"effect":"write","grants":["memory.write"]}
        self.assertEqual(publish(safe)["revision"],1)
        # A malformed direct SQL revision must not enter executable catalog.
        with connect() as db:
            db.execute("""
               INSERT INTO capability_registry(id,revision,input_ports,output_ports,adapter,adapter_config,effect,required_grants)
               VALUES(%s,1,'{"value":"text"}','{"value":"text"}',
                      'memory','{"operation":"remember"}','read',ARRAY[]::text[])
            """,("test.untrusted."+suffix,))
            entries=catalog(db)
        self.assertNotIn("test.untrusted."+suffix,entries)

    def test_control_api_registers_scoped_capabilities_and_graphs(self):
        self.drain()
        suffix=uuid.uuid4().hex
        capability="test.api."+suffix
        body={
          "id":capability,"adapter":"pure","config":{"operation":"upper"},
          "inputs":{"value":"text"},"outputs":{"value":"text"},
          "effect":"read","grants":[]}
        base="http://127.0.0.1:"+str(self.server.server_port)
        headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],
                 "Content-Type":"application/json"}
        def post(path,payload):
            req=urllib.request.Request(base+path,method="POST",headers=headers,
                data=json.dumps(payload).encode())
            with urllib.request.urlopen(req,timeout=5) as res:return json.load(res)
        data=post("/v1/control/capabilities",body)
        self.assertEqual(data["revision"],1)
        with self.assertRaises(urllib.error.HTTPError) as err:
            post("/v1/control/capabilities",body)
        self.assertEqual(err.exception.code,400)
        graph={"inputs":{"text":"text"},"nodes":[
           {"id":"calc","capability":capability,"inputs":{"value":{"input":"text"}}},
           {"id":"view","capability":"view.text","inputs":{"value":{"node":"calc","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}
        published=post("/v1/control/graphs",{"definition":graph,"title":"Test uppercase"})
        self.assertEqual(published["scope_id"],"dev")
        task=post("/v1/control/actions/"+published["action_id"],{"text":"hello"})
        self.drain()
        req=urllib.request.Request(base+"/v1/events/"+str(task["event_id"]),headers=headers)
        with urllib.request.urlopen(req,timeout=5) as response:result=json.load(response)
        self.assertTrue(result["terminal"])
        self.assertEqual(result["activations"][-1]["result"]["view"]["value"],"HELLO")
        with self.assertRaises(urllib.error.HTTPError) as err:
            post("/v1/control/capabilities",{**body,"id":"test.bad."+suffix,
                "grants":["database.superuser"]})
        self.assertEqual(err.exception.code,403)

    def test_external_effect_requires_explicit_reconciliation(self):
        from livingd.engine import ingest
        from livingd.database import connect
        self.drain()
        event=ingest("control.action","reliability",uuid.uuid4().hex,"dev",
                     {"action_id":"living.python","text":"print(2+2)"})
        self.drain()
        with connect() as db:
            a=db.execute("SELECT id,state FROM activations WHERE event_id=%s",(event,)).fetchone()
            effect=db.execute("SELECT id,state FROM execution_effects WHERE activation_id=%s",(a["id"],)).fetchone()
        self.assertEqual(a["state"],"suspended",a)
        self.assertEqual(effect["state"],"uncertain")
        endpoint="http://127.0.0.1:"+str(self.server.server_port)+"/v1/control/effects/"+str(effect["id"])+"/reconcile"
        req=urllib.request.Request(endpoint,
          data=json.dumps({"outcome":"committed","result":{"value":"verified output"}}).encode(),method="POST",
          headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=5) as response:
            self.assertEqual(json.load(response)["outcome"],"committed")
        self.drain()
        with connect() as db:
            a=db.execute("SELECT state,result FROM activations WHERE id=%s",(a["id"],)).fetchone()
        self.assertEqual(a["state"],"completed",a)
        self.assertEqual(a["result"]["view"]["value"],"verified output")

    def test_goals_and_durable_wake_events(self):
        from datetime import datetime,timedelta,timezone
        from livingd.database import connect
        self.drain()
        unique="scheduled goal "+uuid.uuid4().hex
        base="http://127.0.0.1:"+str(self.server.server_port)
        headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],
                 "Content-Type":"application/json"}
        def post(path,payload):
            req=urllib.request.Request(base+path,method="POST",headers=headers,
                data=json.dumps(payload).encode())
            with urllib.request.urlopen(req,timeout=5) as res:return json.load(res)
        result=post("/v1/goals",{"title":unique})
        self.drain()
        with connect() as db:
            goal=db.execute("SELECT id,state FROM goal_units WHERE scope_id='dev' AND title=%s",(unique,)).fetchone()
        self.assertEqual(goal["state"],"active")
        future=datetime.now(timezone.utc)-timedelta(seconds=2)
        due=post("/v1/schedule",{"goal_id":str(goal["id"]),"action_id":"living.upper",
                                "text":"wake me","due_at":future.isoformat()})
        self.drain()
        with connect() as db:
            job=db.execute("SELECT state,fired_event_id FROM scheduled_events WHERE id=%s",(due["id"],)).fetchone()
            activation=db.execute("SELECT state,result FROM activations WHERE event_id=%s",(job["fired_event_id"],)).fetchone()
        self.assertEqual(job["state"],"fired")
        self.assertEqual(activation["state"],"completed")
        self.assertEqual(activation["result"]["view"]["value"],"WAKE ME")
        self.drain()
        with connect() as db:
            num=db.execute("SELECT count(*) n FROM events WHERE source='scheduler' AND source_id=%s",(due["id"],)).fetchone()["n"]
        self.assertEqual(num,1)
        later=datetime.now(timezone.utc)+timedelta(days=1)
        pending=post("/v1/schedule",{"goal_id":str(goal["id"]),"action_id":"living.echo",
                                    "text":"never wake","due_at":later.isoformat()})
        cancelled=post("/v1/goals/"+str(goal["id"])+"/cancel",{})
        self.assertEqual(cancelled["state"],"cancelled")
        with connect() as db:
            status=db.execute("SELECT state FROM scheduled_events WHERE id=%s",(pending["id"],)).fetchone()
        self.assertEqual(status["state"],"cancelled")

    def test_modern_mcp_tasks_and_origin_validation(self):
        self.drain()
        base="http://127.0.0.1:"+str(self.server.server_port)
        meta={"io.modelcontextprotocol/protocolVersion":"2026-07-28",
              "io.modelcontextprotocol/clientCapabilities":{
                "extensions":{"io.modelcontextprotocol/tasks":{}}}}
        def request(method,params,task_name=None,origin=None):
            headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],
                     "Content-Type":"application/json","MCP-Protocol-Version":"2026-07-28",
                     "Mcp-Method":method}
            if task_name:headers["Mcp-Name"]=task_name
            if origin:headers["Origin"]=origin
            payload={"jsonrpc":"2.0","id":uuid.uuid4().hex,"method":method,
                     "params":{**params,"_meta":meta}}
            req=urllib.request.Request(base+"/mcp",method="POST",
                  headers=headers,data=json.dumps(payload).encode())
            with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as exc:
            request("tools/list",{},origin="https://invalid.example")
        self.assertEqual(exc.exception.code,403)
        discovery=request("server/discover",{})["result"]
        self.assertEqual(discovery["resultType"],"complete")
        self.assertIn("2026-07-28",discovery["supportedVersions"])
        self.assertIn("io.modelcontextprotocol/tasks",discovery["capabilities"]["extensions"])
        self.assertEqual(discovery["cacheScope"],"private")
        for method in ("tools/list","resources/list","prompts/list"):
            response=request(method,{})["result"]
            self.assertEqual(response["resultType"],"complete")
            self.assertEqual(response["ttlMs"],0)
            self.assertEqual(response["cacheScope"],"private")
            self.assertIn("io.modelcontextprotocol/serverInfo",response["_meta"])
        read=request("resources/read",{"uri":"living://view/interface.root"})["result"]
        self.assertEqual(read["resultType"],"complete")
        self.assertEqual(read["cacheScope"],"private")
        returned=request("tools/call",{"name":"living.upper","arguments":{"text":"modern mcp"}},
                         task_name="living.upper")
        self.assertEqual(returned["result"]["resultType"],"task")
        task_id=returned["result"]["taskId"]
        self.drain()
        state=request("tasks/get",{"taskId":task_id},task_name=task_id)["result"]
        self.assertEqual(state["status"],"completed")
        self.assertEqual(state["result"]["content"][0]["text"],"MODERN MCP")
        update=request("tasks/update",{"taskId":task_id},task_name=task_id)
        self.assertEqual(update["error"]["code"],-32602)
        pending=request("tools/call",{"name":"living.echo","arguments":{"text":"cancel before delivery"}},
                        task_name="living.echo")["result"]["taskId"]
        cancelled=request("tasks/cancel",{"taskId":pending},task_name=pending)["result"]
        self.assertEqual(cancelled["status"],"cancelled")
        self.drain()
        final=request("tasks/get",{"taskId":pending},task_name=pending)["result"]
        self.assertEqual(final["status"],"cancelled")

    def test_fresh_process_recovers_committed_work(self):
        import subprocess
        from livingd.engine import ingest
        from livingd.database import connect
        self.drain()
        marker="cold"+uuid.uuid4().hex
        event=ingest("control.action","reliability",marker,"dev",
                     {"action_id":"living.upper","text":"restarted"})
        env=dict(os.environ)
        env["LIVING_DATABASE_URL"]=env["LIVING_TEST_DATABASE_URL"]
        env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"core"/"livingd")
        for _ in range(15):
            p=subprocess.run([sys.executable,"-m","livingd","tick"],env=env,
                             capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with connect() as db:
                row=db.execute("SELECT state,result FROM activations WHERE event_id=%s",(event,)).fetchone()
            if row and row["state"]=="completed":break
        self.assertIsNotNone(row)
        self.assertEqual(row["state"],"completed")
        self.assertEqual(row["result"]["view"]["value"],"RESTARTED")

    def test_parallel_workers_avoid_duplicate_accepted_nodes(self):
        from concurrent.futures import ThreadPoolExecutor
        from livingd.engine import ingest,tick
        from livingd.database import connect
        self.drain()
        events=[ingest("control.action","parallel-test",uuid.uuid4().hex,"dev",
                 {"action_id":"living.upper","text":"parallel"+str(i)}) for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as executor:
            for _ in range(50):
                list(executor.map(lambda _: tick(),range(6)))
        with connect() as db:
            rows=db.execute("""
              SELECT a.state,a.result,
                (SELECT count(*) FROM node_runs n WHERE n.activation_id=a.id) AS nodes
              FROM activations a WHERE a.event_id=ANY(%s)
            """,(events,)).fetchall()
        self.assertEqual(len(rows),len(events))
        self.assertTrue(all(row["state"]=="completed" for row in rows),rows)
        self.assertTrue(all(row["nodes"]==2 for row in rows))

    def test_scheduler_does_not_starve_new_work_behind_waiting_page(self):
        from livingd.engine import ingest,route_once,deliver_once,_claim_node
        from livingd.database import connect
        self.drain()
        source=ingest("test.no-op","reliability",uuid.uuid4().hex,"dev",{"text":"not routed"})
        blockers=[uuid.uuid4() for _ in range(40)]
        try:
            with connect() as db:
                for activation_id in blockers:
                    db.execute("""
                      INSERT INTO activations(id,event_id,graph_revision,scope_id,inputs,state,principal_id)
                      VALUES(%s,%s,'bootstrap.upper.1','dev','{"text":"blocked"}','running','local-owner')
                    """,(activation_id,source))
                    db.execute("""
                      INSERT INTO node_runs(activation_id,node_id,state,lease_epoch,lease_until)
                      VALUES(%s,'upper','leased',1,now()+interval '1 hour')
                    """,(activation_id,))
            target=ingest("control.action","reliability",uuid.uuid4().hex,"dev",
                          {"action_id":"living.upper","text":"eventual"})
            for _ in range(20):
                route_once()
                with connect() as db:
                    routing=db.execute("SELECT state FROM event_routing WHERE event_id=%s",(target,)).fetchone()
                if routing["state"]=="routed":break
            self.assertEqual(routing["state"],"routed")
            self.assertTrue(deliver_once())
            # The first scheduling page contains only older waiting nodes.
            self.assertIsNone(_claim_node())
            # Inspecting that page rotates its position; the next claim reaches new work.
            task=_claim_node()
            self.assertIsNotNone(task)
            self.assertEqual(task["node_id"],"upper")
            self.assertEqual(task["cause"],target)
        finally:
            with connect() as db:
                db.execute("DELETE FROM node_runs WHERE activation_id=ANY(%s)",(blockers,))
                db.execute("DELETE FROM activations WHERE id=ANY(%s)",(blockers,))
        with connect() as db:
            db.execute("""
                UPDATE node_runs SET result='{"value":"EVENTUAL"}'::jsonb,
                  state='completed',lease_until=NULL
                WHERE activation_id=%s AND node_id='upper'
            """,(task["activation_id"],))
        self.drain()
        with connect() as db:
            row=db.execute("SELECT state,result FROM activations WHERE event_id=%s",(target,)).fetchone()
        self.assertEqual(row["state"],"completed")
        self.assertEqual(row["result"]["view"]["value"],"EVENTUAL")

    def test_insufficient_grant_fails_delivery_and_mcp_task(self):
        from livingd.engine import ingest,route_once,deliver_once
        from livingd.database import connect
        from livingd.mcp import handle
        self.drain()
        principal_id="limited-"+uuid.uuid4().hex
        with connect() as db:
            db.execute("""
                INSERT INTO control_principals(id,scope_id,grants)
                VALUES(%s,'dev',ARRAY['control.invoke'])
            """,(principal_id,))
        event=ingest("control.action","reliability",uuid.uuid4().hex,"dev",
             {"action_id":"living.remember","text":"should not store"},
             principal_id=principal_id)
        self.assertTrue(route_once())
        self.assertTrue(deliver_once())
        with connect() as db:
            delivery=db.execute("SELECT state,reason FROM event_deliveries WHERE event_id=%s",(event,)).fetchone()
        self.assertEqual(delivery["state"],"failed")
        self.assertIn("grant",delivery["reason"])
        url="http://127.0.0.1:"+str(self.server.server_port)+"/v1/events/"+str(event)
        req=urllib.request.Request(url,headers={
            "Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"]})
        with urllib.request.urlopen(req,timeout=5) as res:result=json.load(res)
        self.assertEqual(result["status"],"failed")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["failures"][0]["reason"],delivery["reason"])
        with connect() as db:
            task_id=uuid.uuid4()
            db.execute("""
                INSERT INTO mcp_tasks(id,event_id,scope_id,principal_id)
                VALUES(%s,%s,'dev','local-owner')
            """,(task_id,event))
            owner=db.execute("SELECT id,scope_id,grants FROM control_principals WHERE id='local-owner'").fetchone()
        reply=handle({"jsonrpc":"2.0","id":1,"method":"tasks/get",
                      "params":{"taskId":str(task_id)}},owner)
        self.assertEqual(reply["result"]["status"],"failed")
        self.assertTrue(reply["result"]["result"]["isError"])

    def test_private_nested_graph_cannot_run_in_another_scope(self):
        from livingd.database import connect
        from livingd.engine import ingest, route_once, deliver_once, node_once
        from livingd.control import register_graph
        from livingd.registry import catalog, visible_graph
        from livingd.logic import validate_graph
        self.drain()
        unique=uuid.uuid4().hex
        scope="private-scope-"+unique
        private_revision="private.graph."+unique
        parent_revision="foreign.child."+unique
        parent_action="test.foreign."+unique
        private_graph={
            "inputs":{"text":"text"},
            "nodes":[{"id":"upper","capability":"text.upper",
                      "inputs":{"value":{"input":"text"}}},
                     {"id":"view","capability":"view.text",
                      "inputs":{"value":{"node":"upper","port":"value"}}}],
            "outputs":{"view":{"node":"view","port":"view"}}}
        caller_graph={
            "inputs":{"text":"text"},
            "nodes":[{"id":"child","capability":"graph.call",
                      "inputs":{"revision":{"literal":private_revision},
                                "text":{"input":"text"}}},
                     {"id":"view","capability":"view.text",
                      "inputs":{"value":{"node":"child","port":"value"}}}],
            "outputs":{"view":{"node":"view","port":"view"}}}
        with connect() as db:
            db.execute("INSERT INTO graph_revisions(id,definition) VALUES(%s,%s::jsonb)",
                       (private_revision,json.dumps(private_graph)))
            db.execute("""
                INSERT INTO graph_catalog(revision_id,description,scope_id)
                VALUES(%s,'Private workspace procedure',%s)
            """,(private_revision,scope))
            self.assertIsNone(visible_graph(db,private_revision,"dev"))
            self.assertIsNotNone(visible_graph(db,private_revision,scope))
            self.assertIsNotNone(visible_graph(db,"bootstrap.upper.1","dev"))
            with self.assertRaisesRegex(ValueError,"unavailable in this scope"):
                register_graph(db,{"id":"local-owner","scope_id":"dev",
                    "grants":["graph.invoke","control.admin"]},
                    {"title":"Inaccessible graph","definition":caller_graph})
            # Simulate a dynamic revision supplied by a node at runtime. A graph
            # can be structurally valid without permission to invoke its target.
            validate_graph(caller_graph,catalog(db))
            db.execute("INSERT INTO graph_revisions(id,definition) VALUES(%s,%s::jsonb)",
                       (parent_revision,json.dumps(caller_graph)))
            db.execute("""
                INSERT INTO control_actions(id,title,graph_revision,input_name,scope_id)
                VALUES(%s,'Foreign graph attempt',%s,'text','dev')
            """,(parent_action,parent_revision))
        event=ingest("control.action","scope-escape",unique,"dev",
                     {"action_id":parent_action,"text":"private"})
        self.assertTrue(route_once())
        self.assertTrue(deliver_once())
        self.assertTrue(node_once())
        with connect() as db:
            activation=db.execute("SELECT id FROM activations WHERE event_id=%s",(event,)).fetchone()
            child=db.execute("SELECT count(*) AS n FROM activations WHERE parent_activation_id=%s",
                             (activation["id"],)).fetchone()
            node=db.execute("SELECT state,error FROM node_runs WHERE activation_id=%s AND node_id='child'",
                            (activation["id"],)).fetchone()
        self.assertEqual(child["n"],0)
        self.assertEqual(node["state"],"retry_wait")
        self.assertIn("unavailable in this scope",node["error"])

    def test_nested_graph_continuation(self):
        from livingd.engine import ingest
        from livingd.database import connect
        from livingd.registry import catalog
        from livingd.logic import validate_graph
        self.drain()
        ident=uuid.uuid4().hex
        revision="test.nested."+ident
        action="test.nested.action."+ident
        graph={"inputs":{"text":"text"},"nodes":[
            {"id":"child","capability":"graph.call","inputs":{
                "revision":{"literal":"bootstrap.upper.1"},"text":{"input":"text"}}},
            {"id":"view","capability":"view.text","inputs":{"value":{"node":"child","port":"value"}}}
        ],"outputs":{"view":{"node":"view","port":"view"}}}
        with connect() as db:
            validate_graph(graph,catalog(db))
            db.execute("INSERT INTO graph_revisions(id,definition) VALUES(%s,%s::jsonb)",
                       (revision,json.dumps(graph)))
            db.execute("INSERT INTO control_actions(id,title,graph_revision,input_name) VALUES(%s,'Nested graph',%s,'text')",
                       (action,revision))
        event=ingest("control.action","reliability",ident,"dev",
                     {"action_id":action,"text":"nested works"})
        self.drain()
        with connect() as db:
            parent=db.execute("SELECT id,state,result FROM activations WHERE event_id=%s AND parent_activation_id IS NULL",(event,)).fetchone()
            child=db.execute("SELECT state,depth FROM activations WHERE parent_activation_id=%s",(parent["id"],)).fetchone()
            state=db.execute("SELECT state FROM node_runs WHERE activation_id=%s AND node_id='child'",(parent["id"],)).fetchone()
        self.assertEqual(parent["state"],"completed",parent)
        self.assertEqual(parent["result"]["view"]["value"],"NESTED WORKS")
        self.assertEqual(child["state"],"completed")
        self.assertEqual(child["depth"],1)
        self.assertEqual(state["state"],"completed")

    def test_unauthorized_and_cross_scope_control(self):
        from livingd.engine import ingest
        from livingd.database import connect
        self.drain()
        event=ingest("control.action","reliability",uuid.uuid4().hex,"dev",
                     {"action_id":"living.echo","text":"secret"})
        self.drain()
        outside="test-other-scope-"+uuid.uuid4().hex
        token="cross-scope-token-"+uuid.uuid4().hex+"-secure"
        with connect() as db:
            db.execute("INSERT INTO control_principals(id,scope_id,grants) VALUES(%s,%s,ARRAY['control.invoke'])",
                       (outside,outside))
            db.execute("INSERT INTO control_tokens(token_hash,principal_id) VALUES(%s,%s)",
                       (hashlib.sha256(token.encode()).hexdigest(),outside))
        url="http://127.0.0.1:"+str(self.server.server_port)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url+"/v1/control/catalog",timeout=5)
        self.assertEqual(error.exception.code,401)
        req=urllib.request.Request(url+"/v1/events/"+str(event),
                                   headers={"Authorization":"Bearer "+token})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req,timeout=5)
        self.assertEqual(error.exception.code,404)
        req=urllib.request.Request(url+"/v1/control/catalog",
                                   headers={"Authorization":"Bearer "+token})
        with urllib.request.urlopen(req,timeout=5) as response:
            data=json.load(response)
        self.assertTrue(data["actions"])
        self.assertTrue(all(a["id"] not in ('living.remember','living.python','living.ask') for a in data["actions"]))

    def test_freeform_response_follows_child_events(self):
        from livingd.engine import ingest
        self.drain()
        event=ingest("surface.input","reliability",uuid.uuid4().hex,"dev",{"text":"uppercase expected"})
        self.drain()
        url="http://127.0.0.1:"+str(self.server.server_port)
        req=urllib.request.Request(url+"/v1/events/"+str(event),
                                   headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"]})
        with urllib.request.urlopen(req,timeout=5) as response:
            data=json.load(response)
        self.assertTrue(data["terminal"],data)
        self.assertGreaterEqual(len(data["activations"]),2)
        last=data["activations"][-1]
        self.assertEqual(last["result"]["view"]["value"],"EXPECTED")

    def test_scoped_cancellation_prevents_new_nodes(self):
        from livingd.engine import ingest,route_once,deliver_once,_claim_node
        from livingd.database import connect
        self.drain()
        event=ingest("control.action","reliability",uuid.uuid4().hex,"dev",
                     {"action_id":"living.upper","text":"cancel"})
        self.assertTrue(route_once());self.assertTrue(deliver_once())
        with connect() as db:
            activation=db.execute("SELECT id FROM activations WHERE event_id=%s",(event,)).fetchone()
        body=json.dumps({}).encode()
        req=urllib.request.Request("http://127.0.0.1:"+str(self.server.server_port)+
              "/v1/control/handles/"+str(activation["id"])+"/cancel",data=body,method="POST",
              headers={"Authorization":"Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"],"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=5) as response:
            self.assertEqual(json.load(response)["state"],"cancelled")
        self.drain()
        with connect() as db:
            state=db.execute("SELECT state FROM activations WHERE id=%s",(activation["id"],)).fetchone()
            nodes=db.execute("SELECT count(*) n FROM node_runs WHERE activation_id=%s",(activation["id"],)).fetchone()
        self.assertEqual(state["state"],"cancelled")
        self.assertEqual(nodes["n"],0)

if __name__=="__main__":unittest.main()
