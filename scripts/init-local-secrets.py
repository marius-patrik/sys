#!/usr/bin/env python3
from pathlib import Path
from secrets import token_urlsafe, token_bytes
from base64 import b64encode
from urllib.parse import quote
import os
root = Path(__file__).resolve().parents[1]/'.private'
root.mkdir(exist_ok=True, mode=0o700)
os.chmod(root, 0o700)
p=root/'postgres_password'
if p.exists(): pw=p.read_text().strip()
else:
 pw=token_urlsafe(30);p.write_text(pw+'\n');os.chmod(p,0o600)
d=root/'living_database_url'
if not d.exists():
 d.write_text(f'postgresql://living:{quote(pw,safe="")}@postgres:5432/living\n');os.chmod(d,0o600)
seal=root/'living_seal_key'
if not seal.exists():
 seal.write_text(b64encode(token_bytes(32)).decode()+'\n')
 os.chmod(seal,0o600)
print('Generated local bootstrap secrets under .private/')
