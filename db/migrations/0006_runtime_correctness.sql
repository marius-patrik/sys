-- Durable logical units, capability registry, fair retry state, and scoped control auth.
ALTER TABLE node_runs DROP CONSTRAINT node_runs_state_check;
ALTER TABLE node_runs ADD CONSTRAINT node_runs_state_check
  CHECK (state IN ('leased','retry_wait','completed','failed','uncertain'));
ALTER TABLE node_runs ADD COLUMN next_attempt_at timestamptz;
ALTER TABLE node_runs ADD COLUMN attempts integer NOT NULL DEFAULT 1;
ALTER TABLE activations DROP CONSTRAINT activations_state_check;
ALTER TABLE activations ADD CONSTRAINT activations_state_check
  CHECK (state IN ('pending','running','completed','failed','cancelled','suspended'));
ALTER TABLE activations ADD COLUMN parent_activation_id uuid REFERENCES activations(id);
ALTER TABLE activations ADD COLUMN parent_node_id text;
ALTER TABLE activations ADD COLUMN root_event_id bigint REFERENCES events(id);
ALTER TABLE activations ADD COLUMN max_nodes integer NOT NULL DEFAULT 64;
ALTER TABLE activations ADD COLUMN max_depth integer NOT NULL DEFAULT 8;
ALTER TABLE activations ADD COLUMN depth integer NOT NULL DEFAULT 0;
ALTER TABLE activations ADD COLUMN cancelled_at timestamptz;
CREATE INDEX node_runs_retry_idx ON node_runs(next_attempt_at) WHERE state='retry_wait';

CREATE TABLE capability_registry (
 id text NOT NULL,
 revision integer NOT NULL DEFAULT 1,
 input_ports jsonb NOT NULL CHECK(jsonb_typeof(input_ports)='object'),
 output_ports jsonb NOT NULL CHECK(jsonb_typeof(output_ports)='object'),
 adapter text NOT NULL,
 adapter_config jsonb NOT NULL DEFAULT '{}'::jsonb,
 effect text NOT NULL CHECK(effect IN ('read','inference','write','external')),
 required_grants text[] NOT NULL DEFAULT '{}',
 active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(id,revision)
);
CREATE INDEX capability_current_idx ON capability_registry(id,revision DESC) WHERE active;

CREATE TABLE control_principals (
 id text PRIMARY KEY,
 scope_id text NOT NULL,
 grants text[] NOT NULL DEFAULT '{}',
 enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE control_tokens (
 token_hash text PRIMARY KEY CHECK(length(token_hash)=64),
 principal_id text NOT NULL REFERENCES control_principals(id),
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE execution_effects (
 id uuid PRIMARY KEY,
 activation_id uuid NOT NULL REFERENCES activations(id),
 node_id text NOT NULL,
 capability_id text NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','running','committed','uncertain','reconciled','cancelled')),
 idempotency_key text NOT NULL UNIQUE,
 result jsonb,
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(activation_id,node_id)
);
CREATE TABLE goal_units (
 id uuid PRIMARY KEY,
 scope_id text NOT NULL,
 title text NOT NULL,
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused','completed','failed','cancelled')),
 parent_id uuid REFERENCES goal_units(id),
 created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE events ADD COLUMN principal_id text REFERENCES control_principals(id);
ALTER TABLE activations ADD COLUMN principal_id text REFERENCES control_principals(id);
ALTER TABLE activations ADD COLUMN grants text[] NOT NULL DEFAULT '{}';
CREATE INDEX event_causation_idx ON events(causation_event_id) WHERE causation_event_id IS NOT NULL;
