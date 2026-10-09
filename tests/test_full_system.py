"""Real PostgreSQL, model-bridge mock, event->graph->memory, MCP and web integration."""
import json
import os
import base64
import sys
import threading
import time
import unittest
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"/"livingd"))

@unittest.skipUnless(os.getenv("LIVING_TEST_DATABASE_URL"),"PostgreSQL is mandatory in CI")
class FullSystemIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIVING_DATABASE_URL"]=os.environ["LIVING_TEST_DATABASE_URL"]
        os.environ.pop("LIVING_DATABASE_URL_FILE",None)
        os.environ["LIVING_DEV_MODE"]="1"
        os.environ["LIVING_BOOTSTRAP_TOKEN"]="test-control-token-0123456789-abcdef"
        os.environ["LIVING_SEAL_KEY"]=base64.b64encode(bytes(range(32))).decode()
        from livingd.database import migrate, seed
        migrate();seed()
        from livingd.auth import authenticate
        cls.principal=authenticate("Bearer "+os.environ["LIVING_BOOTSTRAP_TOKEN"])
        assert cls.principal is not None

        class LiteLLMMock(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,data):
                raw=json.dumps(data).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                assert self.path=="/v1/models",self.path
                assert self.headers.get("Authorization")=="Bearer test-litellm-key"
                self.respond({"object":"list","data":[{"id":"ci-chat-model","object":"model"}]})
            def do_POST(self):
                size=int(self.headers.get("Content-Length","0"))
                data=json.loads(self.rfile.read(size))
                assert self.path=="/v1/chat/completions",self.path
                assert self.headers.get("Authorization")=="Bearer test-litellm-key"
                assert data["model"]=="ci-chat-model"
                full_prompt=data["messages"][-1]["content"]
                memory=full_prompt.split("Relevant context:\n",1)[-1]
                if "Extract up to " in full_prompt:
                    observation=full_prompt.split("Evidence: ",1)[-1].split("\n\nRelevant context:",1)[0]
                    parts=observation.split(" is ",1)
                    subject=parts[0].strip()
                    object_name=parts[1].strip() if len(parts)>1 else "unknown"
                    content=json.dumps({"claims":[{"statement":observation,"subject":subject,
                              "predicate":"is","object":object_name}]})
                else:
                    content="Test model answer. Memory: "+memory
                self.respond({"choices":[{"message":{"role":"assistant","content":content}}]})
        cls.model=ThreadingHTTPServer(("127.0.0.1",0),LiteLLMMock)
        cls.model_thread=threading.Thread(target=cls.model.serve_forever,daemon=True)
        cls.model_thread.start()
        from livingd.models import configure_gateway,select_model
        from livingd.database import connect
        with connect() as db:
            configure_gateway(db,"dev","http://127.0.0.1:"+str(cls.model.server_port),
                              "test-litellm-key")
        with connect() as db:
            select_model(db,"dev","answer","ci-chat-model")
        from livingd.__main__ import Handler
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.http_thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.http_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close()
        cls.model.shutdown();cls.model.server_close()
        os.environ.pop("LIVING_SEAL_KEY",None)
        os.environ.pop("LIVING_BOOTSTRAP_TOKEN",None)

    def pump(self,steps=250):
        from livingd.engine import tick
        for i in range(steps):
            if not tick():break

    def read(self,path):
        req=urllib.request.Request("http://127.0.0.1:"+str(self.http.server_port)+path,
            headers={"Authorization":"Bearer test-control-token-0123456789-abcdef"})
        with urllib.request.urlopen(req,timeout=5) as response:
            return response.headers.get("content-type",""),response.read()

    def post(self,path,data):
        body=json.dumps(data).encode()
        req=urllib.request.Request("http://127.0.0.1:"+str(self.http.server_port)+path,
          data=body,headers={"Content-Type":"application/json","Authorization":"Bearer test-control-token-0123456789-abcdef"},method="POST")
        with urllib.request.urlopen(req,timeout=5) as response:
            return json.load(response)

    def test_memory_routing_scope_provenance_and_model_answer(self):
        from livingd.engine import ingest
        from livingd.database import connect
        from livingd.memory import search
        label="kiwi"+uuid.uuid4().hex[:10]
        first=ingest("surface.input","fulltest",str(uuid.uuid4()),"dev",{"text":"remember "+label+" is an edible fruit"})
        self.pump()
        with connect() as db:
            claims=search(db,"dev",label)
            self.assertEqual(len(claims),1)
            self.assertEqual(search(db,"private",label),[])
            self.assertEqual(claims[0]["sources"],1)
            a=db.execute("SELECT count(*) AS n FROM activations WHERE event_id=%s",(first,)).fetchone()
            self.assertEqual(a["n"],1)
        second=ingest("surface.input","fulltest",str(uuid.uuid4()),"dev",{"text":"recall "+label})
        self.pump()
        with connect() as db:
            acts=db.execute("""
              SELECT a.result FROM activations a
              JOIN events e ON e.id=a.event_id
              WHERE e.causation_event_id=%s AND e.kind='control.action'
            """,(second,)).fetchall()
        self.assertEqual(len(acts),1)
        self.assertIn(label,acts[0]["result"]["view"]["value"])
        third=ingest("surface.input","fulltest",str(uuid.uuid4()),"dev",{"text":"What do we know about "+label+"?"})
        self.pump()
        with connect() as db:
            result=db.execute("""
              SELECT a.state,a.result FROM activations a JOIN events e ON e.id=a.event_id
              WHERE e.causation_event_id=%s AND e.kind='control.action'
            """,(third,)).fetchone()
        self.assertEqual(result["state"],"completed")
        self.assertIn(label,result["result"]["view"]["value"])
        self.assertIn("Test model answer",result["result"]["view"]["value"])

    def test_selective_memory_attention_and_approval(self):
        from livingd.engine import ingest
        from livingd.database import connect
        from livingd.memory import search
        observation="New documented observation "+uuid.uuid4().hex[:12]+" is useful"
        event=ingest("tool.observed","fulltest",str(uuid.uuid4()),"dev",{"text":observation})
        self.pump()
        with connect() as db:
            decision=db.execute("SELECT choice FROM memory_decisions WHERE event_id=%s",(event,)).fetchone()
            self.assertEqual(decision["choice"],"candidate")
            candidate=db.execute("SELECT id,state FROM memory_candidates WHERE source_event_id=%s",(event,)).fetchone()
            self.assertEqual(candidate["state"],"pending")
            self.assertEqual(search(db,"dev",observation.split()[3]),[])
        approval=ingest("control.action","fulltest",str(uuid.uuid4()),"dev",
                        {"action_id":"living.approve-memory","text":str(candidate["id"])})
        self.pump()
        with connect() as db:
            status=db.execute("SELECT state FROM memory_candidates WHERE id=%s",(candidate["id"],)).fetchone()
            self.assertEqual(status["state"],"approved")
            self.assertGreaterEqual(db.execute("SELECT count(*) AS n FROM memory_claims WHERE scope_id='dev' AND content=%s",(observation,)).fetchone()["n"],1)
            e=db.execute("SELECT count(*) AS n FROM memory_evidence WHERE event_id=%s",(event,)).fetchone()
            self.assertEqual(e["n"],1)

    def test_graph_composition_and_mcp(self):
        from livingd.engine import ingest
        from livingd.database import connect
        from livingd.mcp import handle as raw_handle
        handle=lambda data:raw_handle(data,self.principal)
        listed=handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})["result"]["tools"]
        self.assertIn("living.remember",[t["name"] for t in listed])
        eid=handle({"jsonrpc":"2.0","id":2,"method":"tools/call",
                    "params":{"name":"living.compose","arguments":{"text":"analyze a project"}}})["result"]["structuredContent"]["event_id"]
        self.pump()
        with connect() as db:
            state=db.execute("SELECT result,state FROM activations WHERE event_id=%s",(eid,)).fetchone()
            self.assertEqual(state["state"],"completed")
            self.assertIn("Graph revision composed.",state["result"]["view"]["value"])
            count=db.execute("SELECT count(*) AS n FROM graph_proposals").fetchone()["n"]
            self.assertGreater(count,0)
            published=db.execute("SELECT id FROM control_actions WHERE id LIKE 'living.composed.%' ORDER BY id LIMIT 1").fetchone()
            self.assertIsNotNone(published)
            published_id=published["id"]
        names=[a["name"] for a in handle({"jsonrpc":"2.0","id":7,"method":"tools/list"})["result"]["tools"]]
        self.assertIn(published_id,names)
        new_eid=handle({"jsonrpc":"2.0","id":9,"method":"tools/call",
           "params":{"name":published_id,"arguments":{"text":"What is a database?"}}})["result"]["structuredContent"]["event_id"]
        self.pump()
        with connect() as db:
            new_run=db.execute("SELECT state FROM activations WHERE event_id=%s",(new_eid,)).fetchone()
        self.assertEqual(new_run["state"],"completed")
        result=handle({"jsonrpc":"2.0","id":3,"method":"resources/list"})["result"]["resources"]
        self.assertTrue(result)
        self.assertEqual(handle({"jsonrpc":"2.0","id":4,"method":"initialize"})["result"]["serverInfo"]["name"],"living-sys")

    def test_litellm_catalog_and_no_secret_reflection(self):
        from livingd.database import connect
        status=self.post("/v1/settings/litellm",{"base_url":"http://127.0.0.1:"+str(self.model.server_port)})
        self.assertTrue(status["has_api_key"])
        self.assertNotIn("test-litellm-key",str(status))
        _,raw=self.read("/v1/models")
        self.assertEqual(json.loads(raw)["data"][0]["id"],"ci-chat-model")
        selected=self.post("/v1/models/selection",{"purpose":"compose","model":"ci-chat-model"})
        self.assertEqual(selected["selected"]["compose"],"ci-chat-model")
        with connect() as db:
            stored=db.execute("SELECT ciphertext,nonce FROM integration_credentials WHERE scope_id='dev' AND name='litellm.api_key'").fetchone()
        self.assertNotIn(b"test-litellm-key",bytes(stored["ciphertext"]))

    def test_model_extraction_and_explicit_conflict_preservation(self):
        from livingd.engine import ingest
        from livingd.database import connect
        unique="subject"+uuid.uuid4().hex[:12]
        self.post("/v1/memory/attention/policy",
                  {"event_kind":"tool.observed","model_enabled":True,"max_candidates":2})
        try:
            claims=[]
            for color in ("red","blue"):
                sentence=unique+" is "+color
                event=ingest("tool.observed","memory-test",str(uuid.uuid4()),"dev",{"text":sentence})
                self.pump()
                with connect() as db:
                    candidate=db.execute("""
                      SELECT id,assertion,content FROM memory_candidates
                      WHERE source_event_id=%s AND state='pending'
                    """,(event,)).fetchone()
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate["assertion"]["subject"],unique)
                self.assertEqual(candidate["assertion"]["object"],color)
                self.post("/v1/control/actions/living.approve-memory",{"text":str(candidate["id"])})
                self.pump()
                claims.append(candidate["id"])
            with connect() as db:
                contradictions=db.execute("""
                  SELECT a.content AS left_claim,b.content AS right_claim
                  FROM memory_conflicts c JOIN memory_claims a ON a.id=c.claim_a
                  JOIN memory_claims b ON b.id=c.claim_b
                  WHERE c.scope_id='dev' AND a.content LIKE %s
                """,(unique+"%",)).fetchall()
            self.assertEqual(len(contradictions),1)
        finally:
            self.post("/v1/memory/attention/policy",
                      {"event_kind":"tool.observed","model_enabled":False})

    def test_dynamic_web_and_mcp_http(self):
        content,body=self.read("/")
        self.assertIn("text/html",content)
        self.assertIn(b"Living Intelligence",body)
        _,raw=self.read("/v1/control/catalog")
        actions=json.loads(raw)["actions"]
        self.assertIn("living.recall",[a["id"] for a in actions])
        _,raw=self.read("/v1/control/activity")
        self.assertIsInstance(json.loads(raw)["activations"],list)
        self.assertIn("tools",self.post("/mcp",{"jsonrpc":"2.0","id":8,"method":"tools/list"})["result"])
        _,view_data=self.read("/v1/control/views/interface.root")
        view=json.loads(view_data)["definition"]
        self.assertTrue(any(child.get("type")=="table" for child in view["children"]))
        self.assertTrue(any(child.get("endpoint")=="/v1/settings/budgets" for child in view["children"]))
        self.assertTrue(any(child.get("source")=="goals" for child in view["children"]))
        _,budget_response=self.read("/v1/settings/budgets")
        self.assertIn("model_calls",json.loads(budget_response)["limits"])
        _,table=self.read("/v1/control/data/memory.candidates")
        self.assertIn("rowActions",json.loads(table))

if __name__=="__main__":unittest.main()
