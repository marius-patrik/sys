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
