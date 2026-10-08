#!/usr/bin/env python3
"""Generic Control client: no baked-in action list; discovers actions live."""
import argparse
import json
import os
import sys
import urllib.request

BASE=os.getenv('LIVING_URL','http://127.0.0.1:8080').rstrip('/')

def api(method, path, data=None):
    req=urllib.request.Request(BASE+path, data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json'},method=method)
    with urllib.request.urlopen(req,timeout=10) as res:
        return json.load(res)

def main():
    parser=argparse.ArgumentParser(prog='living',description='Discover and invoke the running Control API')
    cmd=parser.add_subparsers(dest='command',required=True)
    cmd.add_parser('catalog')
    input_cmd=cmd.add_parser('input'); input_cmd.add_argument('text',nargs='+')
    call=cmd.add_parser('call');call.add_argument('action');call.add_argument('text',nargs='+')
    get=cmd.add_parser('event');get.add_argument('id')
    h=cmd.add_parser('handle');h.add_argument('id')
    cmd.add_parser('watch')
    args=parser.parse_args()
    if args.command=='catalog': result=api('GET','/v1/control/catalog')
    elif args.command=='input': result=api('POST','/v1/inputs',{'text':' '.join(args.text)})
    elif args.command=='call':
        catalog=api('GET','/v1/control/catalog')['actions']
        action=next((a for a in catalog if a['id']==args.action),None)
        if not action: parser.error('unknown action; inspect `living catalog`')
        required=action['inputSchema']['required']
        if len(required)!=1:parser.error('bootstrap CLI supports one text argument per action')
        result=api('POST',action['invoke'],{required[0]:' '.join(args.text)})
    elif args.command=='event': result=api('GET','/v1/events/'+args.id)
    elif args.command=='handle':result=api('GET','/v1/control/handles/'+args.id)
    else:
        with urllib.request.urlopen(BASE+'/v1/control/stream',timeout=None) as response:
            for line in response:
                print(line.decode().rstrip(),flush=True)
        return
    print(json.dumps(result,indent=2,default=str))

if __name__=='__main__':main()
