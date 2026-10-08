-- Vertical slice: independent, clean bootstrap schema. Not a migration on top
-- of the unimplemented v0.22 schema. PostgreSQL 16+; no extensions required.
CREATE TABLE graph_revisions (
  id text PRIMARY KEY,
  definition jsonb NOT NULL CHECK (jsonb_typeof(definition)='object'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE control_actions (
  id text PRIMARY KEY,
  title text NOT NULL,
  graph_revision text NOT NULL REFERENCES graph_revisions(id),
  input_name text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  revision integer NOT NULL DEFAULT 1
);
CREATE TABLE control_views (
  id text PRIMARY KEY,
  definition jsonb NOT NULL CHECK (jsonb_typeof(definition)='object'),
  revision integer NOT NULL DEFAULT 1
);
CREATE TABLE subscriptions (
  id text PRIMARY KEY,
  event_kind text NOT NULL,
  action_id text REFERENCES control_actions(id),
  revision integer NOT NULL DEFAULT 1,
  enabled boolean NOT NULL DEFAULT true
);
CREATE INDEX subscriptions_kind_idx ON subscriptions(event_kind) WHERE enabled;
CREATE TABLE events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL,
  source text NOT NULL,
  source_id text NOT NULL,
  scope_id text NOT NULL,
  payload jsonb NOT NULL,
  causation_event_id bigint REFERENCES events(id),
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(source, source_id)
);
CREATE TABLE event_routing (
  event_id bigint PRIMARY KEY REFERENCES events(id),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','routed')),
  lease_epoch bigint NOT NULL DEFAULT 0,
  lease_until timestamptz
);
CREATE FUNCTION living_route_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO event_routing(event_id) VALUES (NEW.id);
  RETURN NEW;
END $$;
CREATE TRIGGER event_insert_enqueue AFTER INSERT ON events FOR EACH ROW EXECUTE FUNCTION living_route_insert();
CREATE TABLE event_deliveries (
  event_id bigint NOT NULL REFERENCES events(id),
  subscription_id text NOT NULL REFERENCES subscriptions(id),
  subscription_revision integer NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','done','failed')),
  lease_epoch bigint NOT NULL DEFAULT 0,
  lease_until timestamptz,
  reason text,
  target_action_id text,
  target_action_revision integer,
  target_graph_revision text REFERENCES graph_revisions(id),
  target_input_name text,
  activation_id uuid,
  PRIMARY KEY(event_id,subscription_id,subscription_revision)
);
CREATE TABLE activations (
  id uuid PRIMARY KEY,
  event_id bigint NOT NULL REFERENCES events(id),
  graph_revision text NOT NULL REFERENCES graph_revisions(id),
  scope_id text NOT NULL,
  inputs jsonb NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','completed','failed')),
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE node_runs (
  activation_id uuid NOT NULL REFERENCES activations(id),
  node_id text NOT NULL,
  state text NOT NULL DEFAULT 'leased' CHECK(state IN ('leased','completed','failed')),
  lease_epoch bigint NOT NULL DEFAULT 1,
  lease_until timestamptz,
  result jsonb,
  error text,
  PRIMARY KEY(activation_id,node_id)
);
CREATE INDEX active_routing_idx ON event_routing(state, lease_until) WHERE state <> 'routed';
CREATE INDEX active_deliveries_idx ON event_deliveries(state, lease_until) WHERE state IN ('pending','leased');
CREATE INDEX activation_scope_idx ON activations(scope_id, updated_at DESC);

-- Graph revisions are immutable. Updating capabilities requires a new revision.
CREATE FUNCTION living_graph_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'graph revisions are immutable';
END $$;
CREATE TRIGGER graph_revision_no_update BEFORE UPDATE OR DELETE ON graph_revisions
FOR EACH ROW EXECUTE FUNCTION living_graph_revision_immutable();
ALTER TABLE event_deliveries ADD CONSTRAINT delivery_activation_fk
  FOREIGN KEY(activation_id) REFERENCES activations(id);
