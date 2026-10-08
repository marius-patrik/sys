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

LOG = logging.getLogger('livingd')

class Handler(BaseHTTPRequestHandler):
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
            body = self.read_payload()
            if self.path == '/mcp':
                reply=handle_mcp(body)
                if reply is None:
                    self.send_response(202);self.end_headers();return
                return self.respond(200,reply)
            source_id = self.headers.get('Idempotency-Key') or uuid.uuid4().hex
            if not (1 <= len(source_id) <= 200): raise ValueError('bad idempotency key')
            if self.path == '/v1/inputs':
                if not isinstance(body.get('text'), str): raise ValueError('text must be string')
                event = ingest('surface.input','dev.control.input',source_id,'dev',{'text':body['text']})
            elif self.path.startswith('/v1/control/actions/'):
                action_id = self.path[len('/v1/control/actions/'):]
                if not isinstance(body.get('text'),str): raise ValueError('text must be string')
                with connect() as db:
                    action=db.execute('SELECT 1 FROM control_actions WHERE id=%s AND enabled', (action_id,)).fetchone()
                if not action: return self.respond(404, {'error':'unknown action'})
                event = ingest('control.action',f'dev.control.{action_id}',source_id,'dev',{'action_id':action_id, 'text':body['text']})
            else:
                return self.respond(404,{'error':'not found'})
            return self.respond(202,{'event_id':event,'status':'accepted','lookup':'/v1/events/'+str(event)})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            return self.respond(400,{'error':str(exc)})
        except Exception:
            LOG.exception('request failed')
            return self.respond(500,{'error':'internal error'})
    def do_GET(self):
        try:
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
            if self.path == '/v1/control/activity':
                with connect() as db:
                    rows=db.execute("SELECT id,graph_revision,state,result,updated_at FROM activations WHERE scope_id='dev' ORDER BY updated_at DESC LIMIT 20").fetchall()
                return self.respond(200,{'activations':rows})
            if self.path == '/healthz':
                with connect() as db: db.execute('SELECT 1')
                return self.respond(200,{'status':'ok','mode':'development'})
            if self.path == '/v1/control/catalog':
                with connect() as db:
                    actions=db.execute('SELECT id,title,graph_revision,input_name,revision FROM control_actions WHERE enabled ORDER BY id').fetchall()
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
                    e=db.execute('SELECT id,kind,source,scope_id,payload,observed_at FROM events WHERE id=%s',(event_id,)).fetchone()
                    activations=db.execute('SELECT id,graph_revision,state,result FROM activations WHERE event_id=%s ORDER BY created_at',(event_id,)).fetchall()
                if e is None: return self.respond(404,{'error':'not found'})
                return self.respond(200,{'event':e,'activations':activations})
            if self.path.startswith('/v1/control/handles/'):
                activation_id=uuid.UUID(self.path.removeprefix('/v1/control/handles/'))
                with connect() as db:
                    a=db.execute('SELECT id,event_id,graph_revision,state,inputs,result FROM activations WHERE id=%s',(activation_id,)).fetchone()
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
                        a=db.execute('SELECT id,graph_revision,state,result,updated_at FROM activations ORDER BY updated_at DESC LIMIT 20').fetchall()
                    data=json.dumps({'activations':a},default=str,sort_keys=True)
                    if data!=last:
                        self.wfile.write(('event: snapshot\ndata: '+data+'\n\n').encode()); self.wfile.flush();last=data
                    time.sleep(1)
                return
            return self.respond(404,{'error':'not found'})
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
