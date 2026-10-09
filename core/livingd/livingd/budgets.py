"""PostgreSQL-serialized call budgets, independent of executor process lifetime."""
from __future__ import annotations
from .database import connect

class BudgetExceeded(RuntimeError):
    pass

MAX_LIMIT=1_000_000

def _valid(value,name):
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=MAX_LIMIT:
        raise ValueError(name+" must be an integer between 0 and 1000000")
    return value

def configure(db,scope:str,model_calls:int,external_calls:int)->dict:
    _valid(model_calls,"model_calls")
    _valid(external_calls,"external_calls")
    db.execute("""
       INSERT INTO scope_resource_policies(scope_id,max_model_calls_per_day,max_external_calls_per_day)
       VALUES(%s,%s,%s) ON CONFLICT(scope_id) DO UPDATE SET
          max_model_calls_per_day=EXCLUDED.max_model_calls_per_day,
          max_external_calls_per_day=EXCLUDED.max_external_calls_per_day,
          revision=scope_resource_policies.revision+1,updated_at=now()
    """,(scope,model_calls,external_calls))
    return status(db,scope)

def status(db,scope:str)->dict:
    policy=db.execute("""
      SELECT max_model_calls_per_day,max_external_calls_per_day,revision
      FROM scope_resource_policies WHERE scope_id=%s
    """,(scope,)).fetchone()
    usage=db.execute("""
      SELECT model_calls,external_calls FROM scope_resource_usage
      WHERE scope_id=%s AND day_utc=(now() AT TIME ZONE 'UTC')::date
    """,(scope,)).fetchone()
    return {"limits":{
      "model_calls":policy["max_model_calls_per_day"] if policy else 100,
      "external_calls":policy["max_external_calls_per_day"] if policy else 10,
      "revision":policy["revision"] if policy else None},
      "used":{"model_calls":usage["model_calls"] if usage else 0,
              "external_calls":usage["external_calls"] if usage else 0}}

def reserve(db,scope:str,kind:str)->dict:
    if kind not in ("model","external"):raise ValueError("invalid resource budget kind")
    db.execute("""
      INSERT INTO scope_resource_policies(scope_id) VALUES(%s)
      ON CONFLICT(scope_id) DO NOTHING
    """,(scope,))
    policy=db.execute("""
      SELECT max_model_calls_per_day,max_external_calls_per_day
      FROM scope_resource_policies WHERE scope_id=%s FOR UPDATE
    """,(scope,)).fetchone()
    maximum=policy["max_model_calls_per_day"] if kind=="model" else policy["max_external_calls_per_day"]
    if maximum==0:raise BudgetExceeded(kind+" call budget is disabled")
    model_increment=1 if kind=="model" else 0
    external_increment=1 if kind=="external" else 0
    column="model_calls" if kind=="model" else "external_calls"
    sql=f"""
        INSERT INTO scope_resource_usage(scope_id,day_utc,model_calls,external_calls)
        VALUES(%s,(now() AT TIME ZONE 'UTC')::date,%s,%s)
        ON CONFLICT(scope_id,day_utc) DO UPDATE SET
          model_calls=scope_resource_usage.model_calls+EXCLUDED.model_calls,
          external_calls=scope_resource_usage.external_calls+EXCLUDED.external_calls
        WHERE scope_resource_usage.{column}<%s
        RETURNING model_calls,external_calls
    """
    row=db.execute(sql,(scope,model_increment,external_increment,maximum)).fetchone()
    if row is None:raise BudgetExceeded(kind+" daily call limit reached")
    return {"model_calls":row["model_calls"],"external_calls":row["external_calls"]}
