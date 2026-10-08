import argparse
import json
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .database import connect, migrate, seed
from .engine import ingest, tick
from .mcp import handle as handle_mcp
from .auth import authenticate,need
from .registry import catalog
from .models import configure_gateway,gateway_status,list_models,select_model,selection,ModelGatewayError

LOG = logging.getLogger('livingd')

class Handler(BaseHTTPRequestHandler):
    def principal(self,grant=None):
        p=authenticate(self.headers.get('Authorization'))
        if p is None:raise PermissionError('authentication required')
        if grant:need(p,grant)
        return p
    server_version = 'livingd/0.29-dev'
    def log_message(self, *args):
        pass
    def respond(self, code, payload):
        data = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)
    def read_payload(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 65536 or size <= 0: raise ValueError('body must be 1..65536 bytes')
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict): raise ValueError('expected object body')
        return data
    def do_POST(self):
        try:
            principal=self.principal('control.invoke')
            scope=principal['scope_id']
            body = self.read_payload()
            if self.path in ('/v1/control/capabilities','/v1/control/graphs'):
                need(principal,'control.admin')
                from .control import register_capability,register_graph
                with connect() as db:
                    result=(register_capability(db,principal,body)
                            if self.path.endswith('/capabilities')
                            else register_graph(db,principal,body))
                    db.execute("""
                      INSERT INTO events(kind,source,source_id,scope_id,payload,principal_id)
                      VALUES('control.definition_changed','control.admin',%s,%s,%s::jsonb,%s)
                    """,(uuid.uuid4().hex,scope,json.dumps(result),principal['id']))
                return self.respond(201,result)
            if self.path == '/v1/settings/litellm':
                need(principal,'control.admin')
                with connect() as db:
                    result=configure_gateway(db,scope,body.get('base_url'),
                                             body.get('api_key'),body.get('clear_key',False))
                return self.respond(200,result)
            if self.path == '/v1/settings/credentials/worker':
                need(principal,'control.admin')
                from .models import put_credential
                value=body.get('token')
                with connect() as db:
                    put_credential(db,scope,'worker.token',value)
                return self.respond(200,{'stored':True,'name':'worker.token'})
            if self.path == '/v1/settings/bridges/dsh':
                need(principal,'control.admin')
                from .models import put_credential
                url=body.get('base_url')
                secret=body.get('token')
                if not isinstance(url,str) or not (
                    url.startswith('https://') or url.startswith('http://127.0.0.1:')
                    or url.startswith('http://localhost:') or url.startswith('http://dsh:')):
                    raise ValueError('invalid DSH bridge URL')
                if not isinstance(secret,str) or len(secret)<12:raise ValueError('invalid DSH bridge token')
                with connect() as db:
                    put_credential(db,scope,'dsh.bridge_key',secret)
                    db.execute("""
                      INSERT INTO integration_endpoints(scope_id,service,base_url,credential_name)
                      VALUES(%s,'dsh',%s,'dsh.bridge_key')
                      ON CONFLICT(scope_id,service) DO UPDATE SET
                        base_url=EXCLUDED.base_url,credential_name=EXCLUDED.credential_name,
                        revision=integration_endpoints.revision+1,updated_at=now()
                    """,(scope,url.rstrip('/')))
                return self.respond(200,{'configured':True,'service':'dsh'})
            if self.path == '/v1/models/selection':
                need(principal,'control.admin')
                with connect() as db:
                    result=select_model(db,scope,body.get('purpose'),body.get('model'))
                return self.respond(200,{'selected':result})
            if self.path.startswith('/v1/control/effects/') and self.path.endswith('/reconcile'):
                need(principal,'control.admin')
                effect_id=uuid.UUID(self.path[len('/v1/control/effects/'):-len('/reconcile')].strip('/'))
                outcome=body.get('outcome')
                result=body.get('result')
                if outcome not in ('committed','not_executed'):
                    raise ValueError('outcome must be committed or not_executed')
                with connect() as db:
                    effect=db.execute("""
                      SELECT ef.id,ef.activation_id,ef.node_id,ef.capability_id,
                             a.capability_pins,a.scope_id
                      FROM execution_effects ef JOIN activations a ON a.id=ef.activation_id
                      WHERE ef.id=%s AND ef.state='uncertain' AND a.scope_id=%s FOR UPDATE OF ef
                    """,(effect_id,scope)).fetchone()
                    if effect is None:return self.respond(404,{'error':'uncertain effect not found'})
                    if outcome=='committed':
                        contract=effect["capability_pins"][effect["capability_id"]]["out"]
                        if not isinstance(result,dict) or set(result)!=set(contract):
                            raise ValueError('reconciliation result does not match output schema')
                        if any(not isinstance(result[port],str) for port,kind in contract.items() if kind=='text'):
                            raise ValueError('reconciliation text output must be string')
                        db.execute("UPDATE node_runs SET state='completed',result=%s::jsonb,error=NULL WHERE activation_id=%s AND node_id=%s AND state='uncertain'",
                                   (json.dumps(result),effect["activation_id"],effect["node_id"]))
                        db.execute("UPDATE activations SET state='running',updated_at=now() WHERE id=%s AND state='suspended'",
                                   (effect["activation_id"],))
                        db.execute("UPDATE execution_effects SET state='reconciled',result=%s::jsonb,updated_at=now() WHERE id=%s",
                                   (json.dumps(result),effect_id))
                    else:
                        db.execute("UPDATE node_runs SET state='failed',error='effect verified not executed' WHERE activation_id=%s AND node_id=%s AND state='uncertain'",
                                   (effect["activation_id"],effect["node_id"]))
                        db.execute("UPDATE activations SET state='failed',updated_at=now() WHERE id=%s AND state='suspended'",
                                   (effect["activation_id"],))
                        db.execute("UPDATE execution_effects SET state='cancelled',updated_at=now() WHERE id=%s",(effect_id,))
                    db.execute("""
                       INSERT INTO events(kind,source,source_id,scope_id,payload,principal_id)
                       VALUES('effect.reconciled','control.admin',%s,%s,%s::jsonb,%s)
                    """,(uuid.uuid4().hex,scope,json.dumps({'effect_id':str(effect_id),'outcome':outcome}),principal['id']))
                return self.respond(200,{'effect_id':str(effect_id),'outcome':outcome})
            if self.path.startswith('/v1/control/handles/') and self.path.endswith('/cancel'):
                path=self.path[len('/v1/control/handles/'):-len('/cancel')].strip('/')
                activation_id=uuid.UUID(path)
                with connect() as db:
                    row=db.execute("""
                      UPDATE activations SET state='cancelled',cancelled_at=now(),updated_at=now()
                      WHERE id=%s AND scope_id=%s AND state IN ('pending','running','suspended')
                      RETURNING id
                    """,(activation_id,scope)).fetchone()
                if not row:return self.respond(404,{'error':'activation not found or already terminal'})
                return self.respond(200,{'id':activation_id,'state':'cancelled'})
            if self.path == '/mcp':
                reply=handle_mcp(body,principal)
                if reply is None:
                    self.send_response(202);self.end_headers();return
                return self.respond(200,reply)
            source_id = self.headers.get('Idempotency-Key') or uuid.uuid4().hex
            if not (1 <= len(source_id) <= 200): raise ValueError('bad idempotency key')
            if self.path == '/v1/inputs':
                if not isinstance(body.get('text'), str): raise ValueError('text must be string')
                event = ingest('surface.input','dev.control.input.'+principal['id'],source_id,scope,{'text':body['text']},principal_id=principal['id'])
            elif self.path.startswith('/v1/control/actions/'):
                action_id = self.path[len('/v1/control/actions/'):]
                if not isinstance(body.get('text'),str): raise ValueError('text must be string')
                with connect() as db:
                    action=db.execute('SELECT 1 FROM control_actions WHERE id=%s AND enabled AND (scope_id IS NULL OR scope_id=%s)', (action_id,scope)).fetchone()
                if not action: return self.respond(404, {'error':'unknown action'})
                event = ingest('control.action',f'dev.control.{principal["id"]}.{action_id}',source_id,scope,{'action_id':action_id, 'text':body['text']},principal_id=principal['id'])
            else:
                return self.respond(404,{'error':'not found'})
            return self.respond(202,{'event_id':event,'status':'accepted','lookup':'/v1/events/'+str(event)})
        except PermissionError as exc:
            return self.respond(403 if 'grant' in str(exc) else 401,{'error':str(exc)})
        except ModelGatewayError as exc:
            return self.respond(503,{'error':str(exc)})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.respond(400,{'error':str(exc)})
        except Exception:
            LOG.exception('request failed')
            return self.respond(500,{'error':'internal error'})
    def do_GET(self):
        try:
            if self.path not in ('/','/index.html','/healthz'):
                principal=self.principal('control.invoke')
                scope=principal['scope_id']
            if self.path == '/v1/settings/litellm':
                with connect() as db:result=gateway_status(db,scope)
                return self.respond(200,result)
            if self.path == '/v1/models':
                with connect() as db:result=list_models(db,scope)
                return self.respond(200,{'data':result})
            if self.path == '/v1/models/selection':
                with connect() as db:result=selection(db,scope)
                return self.respond(200,{'selected':result})
            if self.path == '/v1/control/capabilities':
                with connect() as db: entries=catalog(db)
                exposed={k:{'revision':v['revision'],'inputs':v['in'],'outputs':v['out'],
                            'effect':v['effect'],'requiredGrants':v['grants']}
                         for k,v in entries.items() if set(v['grants'])<=set(principal['grants'])}
                return self.respond(200,{'capabilities':exposed})
            if self.path in ('/','/index.html'):
                source=Path(os.getenv("LIVING_WEB_PATH",str(Path(__file__).resolve().parents[3]/"interfaces/web/index.html")))
                if not source.is_file():return self.respond(404,{'error':'web interface not installed'})
                data=source.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(data);return
            if self.path == '/v1/control/data/memory.candidates':
                with connect() as db:
                    rows=db.execute("SELECT id,content,source_event_id FROM memory_candidates WHERE scope_id=%s AND state='pending' ORDER BY created_at DESC LIMIT 50",(scope,)).fetchall()
                return self.respond(200,{'columns':['id','content','source_event_id'],'rows':rows,
                    'rowActions':[{'label':'Approve','actionId':'living.approve-memory','inputField':'id'}]})
            if self.path == '/v1/memory/candidates':
                with connect() as db:
                    candidates=db.execute("SELECT id,content,source_event_id FROM memory_candidates WHERE scope_id=%s AND state='pending' ORDER BY created_at DESC LIMIT 50",(scope,)).fetchall()
                return self.respond(200,{'candidates':candidates})
            if self.path == '/v1/control/effects':
                need(principal,'control.admin')
                with connect() as db:
                    effects=db.execute("""
                       SELECT ef.id,ef.activation_id,ef.node_id,ef.capability_id,
                              ef.state,ef.updated_at FROM execution_effects ef
                       JOIN activations a ON a.id=ef.activation_id
                       WHERE a.scope_id=%s ORDER BY ef.updated_at DESC LIMIT 100
                    """,(scope,)).fetchall()
                return self.respond(200,{'effects':effects})
            if self.path == '/v1/control/activity':
                with connect() as db:
                    rows=db.execute("SELECT id,graph_revision,state,result,updated_at FROM activations WHERE scope_id=%s ORDER BY updated_at DESC LIMIT 20",(scope,)).fetchall()
                return self.respond(200,{'activations':rows})
            if self.path == '/healthz':
                with connect() as db: db.execute('SELECT 1')
                return self.respond(200,{'status':'ok','mode':'development'})
            if self.path == '/v1/control/catalog':
                with connect() as db:
                    actions=db.execute('SELECT id,title,graph_revision,input_name,revision FROM control_actions WHERE enabled AND (scope_id IS NULL OR scope_id=%s) ORDER BY id',(scope,)).fetchall()
                    graph_rows=db.execute("SELECT id,definition FROM graph_revisions WHERE id=ANY(%s)",([a['graph_revision'] for a in actions],)).fetchall()
                    graphs={r['id']:r['definition'] for r in graph_rows}
                    manifest=catalog(db)
                grants=set(principal['grants'])
                actions=[a for a in actions if all(
                    n['capability'] in manifest and set(manifest[n['capability']]['grants'])<=grants
                    for n in graphs.get(a['graph_revision'],{}).get('nodes',[]))]
                return self.respond(200,{'actions':[{'id':a['id'],'title':a['title'],'revision':a['revision'],
                   'inputSchema':{'type':'object','properties':{a['input_name']:{'type':'string'}},'required':[a['input_name']]},
                   'invoke':'/v1/control/actions/'+a['id']} for a in actions]})
            if self.path.startswith('/v1/control/views/'):
                view_id=self.path.removeprefix('/v1/control/views/')
                with connect() as db:
                    view=db.execute('SELECT id,definition,revision FROM control_views WHERE id=%s',(view_id,)).fetchone()
                if view is None:return self.respond(404,{'error':'not found'})
                return self.respond(200,view)
            if self.path.startswith('/v1/events/'):
                event_id=int(self.path.removeprefix('/v1/events/'))
                with connect() as db:
                    e=db.execute('SELECT id,kind,source,scope_id,payload,observed_at FROM events WHERE id=%s AND scope_id=%s',(event_id,scope)).fetchone()
                    if e is None:return self.respond(404,{'error':'not found'})
                    chain="""
                        WITH RECURSIVE lineage AS (
                          SELECT id,0 AS depth FROM events WHERE id=%s AND scope_id=%s
                          UNION ALL
                          SELECT child.id,lineage.depth+1 FROM events child
                          JOIN lineage ON child.causation_event_id=lineage.id
                          WHERE lineage.depth<32 AND child.scope_id=%s
                        )
                    """
                    activations=db.execute(chain+"""
                        SELECT a.id,a.event_id,a.graph_revision,a.state,a.result
                        FROM activations a JOIN lineage ON a.event_id=lineage.id
                        WHERE a.scope_id=%s ORDER BY a.created_at
                    """,(event_id,scope,scope,scope)).fetchall()
                    pending=db.execute(chain+"""
                        SELECT (SELECT count(*) FROM event_routing r JOIN lineage l ON l.id=r.event_id WHERE r.state<>'routed')
                          + (SELECT count(*) FROM event_deliveries d JOIN lineage l ON l.id=d.event_id WHERE d.state IN ('pending','leased')) AS pending
                    """,(event_id,scope,scope)).fetchone()
                terminal=not pending['pending'] and (not activations or all(
                    a['state'] in ('completed','failed','cancelled','suspended') for a in activations))
                return self.respond(200,{'event':e,'activations':activations,'terminal':terminal})
            if self.path.startswith('/v1/control/handles/'):
                activation_id=uuid.UUID(self.path.removeprefix('/v1/control/handles/'))
                with connect() as db:
                    a=db.execute('SELECT id,event_id,graph_revision,state,inputs,result FROM activations WHERE id=%s AND scope_id=%s',(activation_id,scope)).fetchone()
                    rows=db.execute('SELECT node_id,state,lease_epoch,result,error FROM node_runs WHERE activation_id=%s ORDER BY node_id',(activation_id,)).fetchall()
                if a is None:return self.respond(404,{'error':'not found'})
                return self.respond(200,{'activation':a,'nodes':rows})
            if self.path == '/v1/control/stream':
                # Bootstrap: reconnect by snapshot, not event-ID cursor. Single-instance only.
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Cache-Control','no-cache')
                self.send_header('X-Accel-Buffering','no')
                self.end_headers()
                last=None
                for _ in range(120):
                    with connect() as db:
                        a=db.execute('SELECT id,graph_revision,state,result,updated_at FROM activations WHERE scope_id=%s ORDER BY updated_at DESC LIMIT 20',(scope,)).fetchall()
                    data=json.dumps({'activations':a},default=str,sort_keys=True)
                    if data!=last:
                        self.wfile.write(('event: snapshot\ndata: '+data+'\n\n').encode()); self.wfile.flush();last=data
                    time.sleep(1)
                return
            return self.respond(404,{'error':'not found'})
        except PermissionError as exc:
            return self.respond(403 if 'grant' in str(exc) else 401,{'error':str(exc)})
        except ModelGatewayError as exc:
            return self.respond(503,{'error':str(exc)})
        except PermissionError as exc:
            return self.respond(403 if 'grant' in str(exc) else 401,{'error':str(exc)})
        except BrokenPipeError:
            return
        except (ValueError,TypeError):
            return self.respond(400,{'error':'invalid identifier'})
        except Exception:
            LOG.exception('read failed')
            return self.respond(500,{'error':'internal error'})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command', choices=['migrate','seed','serve','tick'],nargs='?',default='serve')
    parser.add_argument('--listen',default=os.getenv('LIVING_LISTEN','127.0.0.1:8080'))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command=='migrate': return migrate()
    if args.command=='seed': return seed()
    if args.command=='tick': return tick()
    if os.environ.get('LIVING_DEV_MODE')!='1':
        raise SystemExit('Bootstrap server has no authentication: set LIVING_DEV_MODE=1 and bind port to loopback only')
    if os.environ.get('LIVING_MIGRATE_ON_BOOT')=='1':
        migrate();seed()
    stop=threading.Event()
    def work():
        while not stop.is_set():
            try:
                if not tick(): stop.wait(0.2)
            except Exception:
                LOG.exception('worker tick failed'); stop.wait(1)
    threading.Thread(target=work,daemon=True).start()
    host,port=args.listen.rsplit(':',1)
    server=ThreadingHTTPServer((host,int(port)),Handler)
    LOG.info('livingd development server on %s (NO AUTH; do not publish publicly)',args.listen)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop.set();server.server_close()

if __name__=='__main__': main()
