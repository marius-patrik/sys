"""Requires a real PostgreSQL server. Run with LIVING_TEST_DATABASE_URL."""
import os
import sys
import time
import unittest
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'/'livingd'))


@unittest.skipUnless(os.getenv('LIVING_TEST_DATABASE_URL'), 'no LIVING_TEST_DATABASE_URL: PostgreSQL test not run')
class PostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from livingd.database import migrate, seed
        os.environ['LIVING_DATABASE_URL']=os.environ['LIVING_TEST_DATABASE_URL']
        os.environ.pop('LIVING_DATABASE_URL_FILE',None)
        migrate(); seed()

    def test_durable_dedup_routing_graph_and_completion(self):
        from livingd.engine import ingest,tick
        from livingd.database import connect
        source_id=uuid.uuid4().hex
        event_id=ingest('control.action', 'integration', source_id, 'dev', {'action_id':'living.upper', 'text':'hello world'})
        self.assertEqual(event_id,ingest('control.action','integration',source_id,'dev',{'action_id':'living.upper','text':'hello world'}))
        # Nodes finish in distinct transactions. Each step is recoverable.
        done=False
        for _ in range(30):
            tick()
            with connect() as db:
                result=db.execute('SELECT state,result FROM activations WHERE event_id=%s',(event_id,)).fetchone()
            if result and result['state']=='completed':
                self.assertEqual(result['result'],{'view':{'type':'text','value':'HELLO WORLD'}})
                done=True
                break
        self.assertTrue(done, 'failed to complete graph within 30 ticks')
        with self.assertRaisesRegex(ValueError,'idempotency key reused'):
            ingest('control.action','integration',source_id,'dev',{'action_id':'living.upper','text':'DIFFERENT'})
        with connect() as db:
            rows=db.execute('SELECT state FROM node_runs WHERE activation_id=(SELECT id FROM activations WHERE event_id=%s)',(event_id,)).fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual([r['state'] for r in rows],['completed','completed'])
            evidence=db.execute("SELECT count(*) AS n FROM events WHERE source='livingd' AND causation_event_id=%s",(event_id,)).fetchone()
            self.assertEqual(evidence['n'],3)  # two nodes and activation completion
        # A process restart cannot cause accepted nodes to run again.
        for _ in range(3):tick()
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM node_runs WHERE activation_id=(SELECT id FROM activations WHERE event_id=%s)',(event_id,)).fetchone()['n'],2)

    def test_dynamic_action_catalog(self):
        from livingd.database import connect
        from livingd.logic import validate_graph
        from livingd.database import SEED_GRAPHS
        action_id='integration.'+uuid.uuid4().hex
        with connect() as db:
            db.execute('INSERT INTO control_actions(id,title,graph_revision,input_name) VALUES(%s,%s,%s,%s)',
                      (action_id,'Temporary integration action',SEED_GRAPHS[0]['id'],'text'))
            self.assertEqual(db.execute('SELECT graph_revision FROM control_actions WHERE id=%s',(action_id,)).fetchone()['graph_revision'],SEED_GRAPHS[0]['id'])
        self.assertTrue(action_id)

if __name__=='__main__':unittest.main()
