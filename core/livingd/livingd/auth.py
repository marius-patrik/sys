"""Database-backed scoped Control authentication."""
import hashlib
import hmac
import os
from pathlib import Path
from .database import connect

def _token_from_bootstrap():
    path=os.getenv("LIVING_BOOTSTRAP_TOKEN_FILE")
    return Path(path).read_text().strip() if path else os.getenv("LIVING_BOOTSTRAP_TOKEN","")

def install_owner(db):
    token=_token_from_bootstrap()
    if token and len(token)<32:raise ValueError("bootstrap token too short")
    db.execute("""
      INSERT INTO control_principals(id,scope_id,grants) VALUES
       ('local-owner','dev',ARRAY['control.invoke','memory.read','memory.write','model.use','graph.compose','graph.invoke','worker.execute','tool.invoke','control.admin'])
       ON CONFLICT(id) DO NOTHING
    """)
    if token:
        digest=hashlib.sha256(token.encode()).hexdigest()
        db.execute("INSERT INTO control_tokens(token_hash,principal_id) VALUES(%s,'local-owner') ON CONFLICT DO NOTHING",(digest,))

def authenticate(header:str|None)->dict|None:
    if not header or not header.startswith("Bearer "):return None
    token=header.removeprefix("Bearer ")
    if len(token)<32 or len(token)>512:return None
    digest=hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        record=db.execute("""
            SELECT p.id,p.scope_id,p.grants FROM control_tokens t
            JOIN control_principals p ON p.id=t.principal_id
            WHERE t.token_hash=%s AND t.revoked_at IS NULL AND p.enabled
        """,(digest,)).fetchone()
    return record

def need(principal:dict|None,grant:str):
    if principal is None:raise PermissionError("authentication required")
    if grant not in principal["grants"]:raise PermissionError("grant required: "+grant)
