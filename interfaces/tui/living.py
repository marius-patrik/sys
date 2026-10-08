#!/usr/bin/env python3
"""Minimal generic terminal renderer; derives actions from Control catalog."""
import json
import os
import urllib.request
BASE=os.getenv("LIVING_URL","http://127.0.0.1:8080").rstrip("/")
def api(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(BASE+path,data=data,
        headers={"Content-Type":"application/json"},method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req,timeout=20) as response:return json.load(response)
def main():
    while True:
        catalog=api("/v1/control/catalog")["actions"]
        print("\nLiving Intelligence · available actions")
        for i,a in enumerate(catalog,1):print(f"{i:>2}. {a['title']} [{a['id']}]")
        print(" 0. Free-form input     q. Quit")
        selection=input("Choose: ").strip()
        if selection=="q":break
        if selection=="0":path="/v1/inputs"
        else:
            try:path=catalog[int(selection)-1]["invoke"]
            except (ValueError,IndexError):continue
        text=input("Input: ")
        print(json.dumps(api(path,{"text":text}),indent=2))
if __name__=="__main__":main()
