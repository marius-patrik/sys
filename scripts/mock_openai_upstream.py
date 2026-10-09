#!/usr/bin/env python3
"""Fake OpenAI provider, exercised only *through the actual LiteLLM proxy*."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import time

class Provider(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path!="/v1/chat/completions":
            self.send_error(404)
            return
        if self.headers.get("Authorization")!="Bearer ci-upstream-key":
            self.send_error(401)
            return
        try:
            length=int(self.headers["Content-Length"])
            assert 0<length<250000
            request=json.loads(self.rfile.read(length))
            assert isinstance(request["messages"],list)
            assert request["model"]=="sys-ci-upstream"
        except (ValueError,KeyError,AssertionError,TypeError):
            self.send_error(400)
            return
        body=json.dumps({
            "id":"chatcmpl-sys-real-litellm-proxy",
            "object":"chat.completion",
            "created":int(time.time()),
            "model":"sys-ci-upstream",
            "choices":[{"index":0,"message":{
                 "role":"assistant","content":"SYS_REAL_PROXY_VERIFIED"},
                 "finish_reason":"stop","logprobs":None}],
            "usage":{"prompt_tokens":12,"completion_tokens":4,"total_tokens":16}
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--port",type=int,default=8765)
    args=p.parse_args()
    ThreadingHTTPServer(("127.0.0.1",args.port),Provider).serve_forever()
