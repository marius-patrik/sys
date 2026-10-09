"""Real LiteLLM proxy HTTP integration, with an isolated fake upstream."""
import os
import sys
import unittest
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"/"livingd"))

@unittest.skipUnless(os.getenv("LIVING_REAL_LITELLM_URL") and
                     os.getenv("LIVING_TEST_DATABASE_URL"),"needs real gateway and PostgreSQL")
class RealLiteLLMProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["LIVING_DATABASE_URL"]=os.environ["LIVING_TEST_DATABASE_URL"]
        os.environ.pop("LIVING_DATABASE_URL_FILE",None)
        from livingd.database import migrate,seed
        migrate();seed()

    def test_actual_proxy_model_catalog_credentials_and_graph(self):
        from livingd.database import connect
        from livingd.models import configure_gateway,list_models,select_model,generate
        from livingd.engine import ingest,tick
        scope="dev"
        with connect() as db:
            info=configure_gateway(db,scope,os.environ["LIVING_REAL_LITELLM_URL"],
                    os.environ["LIVING_REAL_LITELLM_TOKEN"])
            self.assertTrue(info["has_api_key"])
            row=db.execute("""
                SELECT ciphertext FROM integration_credentials
                WHERE scope_id=%s AND name='litellm.api_key'
            """,(scope,)).fetchone()
            self.assertNotIn(os.environ["LIVING_REAL_LITELLM_TOKEN"].encode(),
                             bytes(row["ciphertext"]))
        with connect() as db:
            models={m["id"] for m in list_models(db,scope)}
            self.assertIn("sys-ci-model",models)
            selected=select_model(db,scope,"answer","sys-ci-model")
            self.assertEqual(selected["answer"],"sys-ci-model")
        self.assertIn("SYS_REAL_PROXY_VERIFIED",generate(scope,"answer","Say hello",""))
        event=ingest("control.action","real-litellm-ci",uuid.uuid4().hex,scope,
                    {"action_id":"living.ask","text":"What is verified?"})
        state=None
        for _ in range(250):
            tick()
            with connect() as db:
                state=db.execute("SELECT state,result FROM activations WHERE event_id=%s",(event,)).fetchone()
            if state and state["state"] in ("completed","failed"):break
        self.assertIsNotNone(state)
        self.assertEqual(state["state"],"completed",state)
        self.assertIn("SYS_REAL_PROXY_VERIFIED",str(state["result"]))

if __name__=="__main__":unittest.main()
