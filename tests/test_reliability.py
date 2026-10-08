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
