"""Dedicated OCI execution broker: never runs inside livingd or with its database credentials."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

IMAGE=os.getenv("LIVING_OCI_IMAGE","python:3.12-alpine")
TOKEN=os.getenv("LIVING_WORKER_TOKEN","")

def execute_python(code:str)->dict:
    if not isinstance(code,str) or not (1<=len(code)<=8000):
        raise ValueError("Python source must contain 1..8000 characters")
    name="living-run-"+uuid.uuid4().hex[:18]
    argv=["docker","run","--rm","--name",name,
          "--network=none","--read-only","--cap-drop=ALL",
          "--security-opt=no-new-privileges","--pids-limit=64",
          "--memory=192m","--cpus=1","--user=65534:65534",
          "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
          "--workdir=/tmp",IMAGE,"python","-I","-c",code]
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=16,check=False)
        return {"stdout":p.stdout[:12000],"stderr":p.stderr[:2000],"exit_code":p.returncode}
    except subprocess.TimeoutExpired:
        subprocess.run(["docker","rm","-f",name],capture_output=True,timeout=5,check=False)
        raise RuntimeError("worker timed out")

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send_json(self,status,obj):
        data=json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        if self.path!="/v1/execute":return self.send_json(404,{"error":"not found"})
        if not TOKEN or self.headers.get("Authorization")!="Bearer "+TOKEN:
            return self.send_json(401,{"error":"unauthorized"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=12000:raise ValueError("invalid request size")
            body=json.loads(self.rfile.read(size))
            if body.get("language")!="python":raise ValueError("unsupported language")
            value=execute_python(body.get("code"))
            return self.send_json(200,value)
        except (ValueError,RuntimeError,subprocess.SubprocessError) as exc:
            return self.send_json(400,{"error":str(exc)})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--listen",default="127.0.0.1:8091")
    args=parser.parse_args()
    host,port=args.listen.rsplit(":",1)
    if not TOKEN or len(TOKEN)<12:raise SystemExit("LIVING_WORKER_TOKEN must be configured")
    if host not in ("127.0.0.1","::1"):
        raise SystemExit("broker must bind loopback; use a separately secured proxy for remote")
    http=ThreadingHTTPServer((host,int(port)),Handler)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()
if __name__=="__main__":main()
