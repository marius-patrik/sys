-- Durable, atomic, per-workspace daily call budgets (UTC).
-- Limits count attempted calls, including failed/uncertain external operations.
CREATE TABLE scope_resource_policies (
 scope_id text PRIMARY KEY,
 max_model_calls_per_day integer NOT NULL DEFAULT 100 CHECK(max_model_calls_per_day BETWEEN 0 AND 1000000),
 max_external_calls_per_day integer NOT NULL DEFAULT 10 CHECK(max_external_calls_per_day BETWEEN 0 AND 1000000),
 revision bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE scope_resource_usage (
 scope_id text NOT NULL,
 day_utc date NOT NULL,
 model_calls integer NOT NULL DEFAULT 0 CHECK(model_calls>=0),
 external_calls integer NOT NULL DEFAULT 0 CHECK(external_calls>=0),
 PRIMARY KEY(scope_id,day_utc)
);
