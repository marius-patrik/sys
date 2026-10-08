"""PostgreSQL-owned evidence-linked memory. No in-process memory authority."""
from __future__ import annotations
import re
import uuid
from .database import connect

def normalize(text: str) -> str:
    return " ".join(text.casefold().split())

def record(db, scope: str, text: str, event_id: int) -> str:
    """Execute inside the fenced node-result transaction."""
    content=text.strip()
    if not (1 <= len(content) <= 12000):
        raise ValueError("memory content must contain 1..12000 characters")
    key=normalize(content)
    claim_id=uuid.uuid5(uuid.NAMESPACE_URL, "living-memory:"+scope+":"+key)
    db.execute("""
        INSERT INTO memory_claims(id,scope_id,content,normalized_key)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT(scope_id,normalized_key)
        DO UPDATE SET revision=memory_claims.revision+1, updated_at=now()
    """,(claim_id,scope,content,key))
    db.execute("""
        INSERT INTO memory_evidence(claim_id,event_id,relation)
        VALUES (%s,%s,'supports') ON CONFLICT DO NOTHING
    """,(claim_id,event_id))
    return str(claim_id)

def search(db, scope: str, query: str, limit: int=8) -> list[dict]:
    if not isinstance(query,str) or not query.strip(): return []
    if len(query)>2000: raise ValueError("memory query too large")
    limit=max(1,min(limit,20))
    # Always filter scope in SQL before ranking, including negative terms.
    rows=db.execute("""
      SELECT m.id, m.content, m.revision, m.status,
          COALESCE(ts_rank(m.search_vector,plainto_tsquery('simple',%s)),0) AS score,
          (SELECT count(*) FROM memory_evidence e
           WHERE e.claim_id=m.id AND e.relation='supports') AS sources
      FROM memory_claims m
      WHERE m.scope_id=%s AND m.status<>'retracted'
        AND (m.search_vector @@ plainto_tsquery('simple',%s)
             OR position(lower(%s) in lower(m.content))>0)
      ORDER BY score DESC, m.updated_at DESC, m.id
      LIMIT %s
    """,(query,scope,query,query,limit)).fetchall()
    return [{"id":str(r["id"]),"content":r["content"],
             "revision":r["revision"],"status":r["status"],
             "sources":r["sources"]} for r in rows]

def context(scope:str,query:str)->str:
    with connect() as db: items=search(db,scope,query)
    if not items: return "(No matching verified memory in the selected scope.)"
    return "\n".join(
      "- "+item["content"]+" [revision "+str(item["revision"])
      +"; evidence links "+str(item["sources"])+"]" for item in items)
