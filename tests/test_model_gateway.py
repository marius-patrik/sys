"""Database-owned credential and LiteLLM selection tests."""
import base64
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"/"livingd"))

@unittest.skipUnless(os.getenv("LIVING_TEST_DATABASE_URL"),"requires PostgreSQL CI service")
class ModelGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIVING_DATABASE_URL"]=os.environ["LIVING_TEST_DATABASE_URL"]
        os.environ["LIVING_SEAL_KEY"]=base64.b64encode(bytes(range(32))).decode()
        from livingd.database import migrate
        migrate()
        class LiteLLM(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                assert self.path=="/v1/models"
                assert self.headers["Authorization"]=="Bearer secret-limitation-test"
                self.reply({"data":[{"id":"model-beta"},{"id":"model-alpha"}]})
            def do_POST(self):
                assert self.path=="/v1/chat/completions"
                assert self.headers["Authorization"]=="Bearer secret-limitation-test"
                request=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.reply({"choices":[{"message":{"content":"via "+request["model"]}}]})
            def reply(self,obj):
                raw=json.dumps(obj).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers();self.wfile.write(raw)
        cls.proxy=ThreadingHTTPServer(("127.0.0.1",0),LiteLLM)
        threading.Thread(target=cls.proxy.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown();cls.proxy.server_close()
        os.environ.pop("LIVING_SEAL_KEY",None)
    def test_encrypted_credentials_and_litellm_discovery(self):
        from livingd.database import connect
        from livingd.models import (configure_gateway,gateway_status,list_models,
                                    read_credential,select_model,generate)
        scope="test-model-gateway"
        with connect() as db:
            status=configure_gateway(db,scope,"http://127.0.0.1:"+str(self.proxy.server_port),
                                     "secret-limitation-test")
        self.assertEqual(status["has_api_key"],True)
        self.assertNotIn("secret-limitation-test",str(status))
        with connect() as db:
            rows=db.execute("SELECT nonce,ciphertext FROM integration_credentials WHERE scope_id=%s",(scope,)).fetchone()
            self.assertNotIn(b"secret-limitation-test",bytes(rows["ciphertext"]))
            self.assertEqual(read_credential(db,scope,"litellm.api_key"),"secret-limitation-test")
        with connect() as db:
            offered=list_models(db,scope)
        self.assertEqual([item["id"] for item in offered],["model-alpha","model-beta"])
        with connect() as db:
            with self.assertRaisesRegex(ValueError,"not advertised"):
                select_model(db,scope,"answer","invented-model")
            selected=select_model(db,scope,"answer","model-beta")
        self.assertEqual(selected["answer"],"model-beta")
        self.assertEqual(generate(scope,"answer","question","memory"),"via model-beta")
        with connect() as db:
            configure_gateway(db,scope,"http://127.0.0.1:"+str(self.proxy.server_port),clear_key=True)
            self.assertIsNone(read_credential(db,scope,"litellm.api_key"))
    def test_model_requests_respect_atomic_postgresql_budgets(self):
        import uuid
        from concurrent.futures import ThreadPoolExecutor
        from livingd.database import connect
        from livingd.models import configure_gateway,generate
        from livingd.budgets import configure,reserve,status,BudgetExceeded
        scope="test-budget-model-"+uuid.uuid4().hex
        with connect() as db:
            configure_gateway(db,scope,
                              "http://127.0.0.1:"+str(self.proxy.server_port),
                              "secret-limitation-test")
            policy=configure(db,scope,model_calls=1,external_calls=0)
        self.assertEqual(policy["limits"]["model_calls"],1)
        self.assertEqual(generate(scope,"answer","question","memory"),"via model-alpha")
        with self.assertRaisesRegex(BudgetExceeded,"limit reached"):
            generate(scope,"answer","second request","memory")
        with connect() as db:
            counters=status(db,scope)
            self.assertEqual(counters["used"]["model_calls"],1)
            with self.assertRaisesRegex(BudgetExceeded,"disabled"):
                reserve(db,scope,"external")
        concurrent="test-budget-concurrent-"+uuid.uuid4().hex
        with connect() as db:
            configure(db,concurrent,model_calls=3,external_calls=2)
        def attempt(_):
            try:
                with connect() as db:reserve(db,concurrent,"model")
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(attempt,range(20)))
        self.assertEqual(sum(results),3)
        with connect() as db:
            self.assertEqual(status(db,concurrent)["used"]["model_calls"],3)

    def test_ciphertext_is_bound_to_scope_and_name(self):
        from livingd.database import connect
        from livingd.models import put_credential,read_credential,ModelGatewayError
        with connect() as db:
            put_credential(db,"scope-A","private.key","sensitive")
            sealed=db.execute("SELECT nonce,ciphertext FROM integration_credentials WHERE scope_id='scope-A' AND name='private.key'").fetchone()
            db.execute("INSERT INTO integration_credentials(scope_id,name,nonce,ciphertext) VALUES('scope-B','private.key',%s,%s) ON CONFLICT(scope_id,name) DO UPDATE SET nonce=excluded.nonce,ciphertext=excluded.ciphertext",
                       (sealed["nonce"],sealed["ciphertext"]))
            with self.assertRaisesRegex(ModelGatewayError,"unseal"):
                read_credential(db,"scope-B","private.key")
    def test_no_credentials_forwarded_on_redirect(self):
        from livingd.models import _request,ModelGatewayError
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location","https://unexpected-provider.invalid/collect")
                self.end_headers()
        server=ThreadingHTTPServer(("127.0.0.1",0),Redirect)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with self.assertRaisesRegex(ModelGatewayError,"redirection"):
                _request("http://127.0.0.1:"+str(server.server_port)+"/models","secret-token")
        finally:
            server.shutdown();server.server_close()

    def test_no_credentials_in_urls(self):
        from livingd.models import _gateway_url
        with self.assertRaises(ValueError):_gateway_url("https://user:token@litellm.example")
        with self.assertRaises(ValueError):_gateway_url("http://example.com:4000")
if __name__=="__main__":unittest.main()
