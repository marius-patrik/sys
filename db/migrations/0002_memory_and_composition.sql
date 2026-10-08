-- PostgreSQL remains the sole authoritative memory and policy store.
CREATE TABLE memory_claims (
  id uuid PRIMARY KEY,
  scope_id text NOT NULL,
  content text NOT NULL CHECK (length(content) BETWEEN 1 AND 12000),
  normalized_key text NOT NULL,
  revision integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'asserted' CHECK (status IN ('asserted','disputed','retracted')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  UNIQUE (scope_id, normalized_key)
);
CREATE INDEX memory_search_gin ON memory_claims USING gin(search_vector);
CREATE TABLE memory_evidence (
  claim_id uuid NOT NULL REFERENCES memory_claims(id),
  event_id bigint NOT NULL REFERENCES events(id),
  relation text NOT NULL CHECK (relation IN ('supports','contradicts','derived_from')),
  PRIMARY KEY(claim_id,event_id,relation)
);
CREATE TABLE intent_rules (
  id text PRIMARY KEY,
  prefix text NOT NULL,
  action_id text NOT NULL REFERENCES control_actions(id),
  priority integer NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE graph_catalog (
  revision_id text PRIMARY KEY REFERENCES graph_revisions(id),
  description text NOT NULL,
  tags text[] NOT NULL DEFAULT '{}',
  enabled boolean NOT NULL DEFAULT true,
  risk text NOT NULL DEFAULT 'read' CHECK (risk IN ('read','write','external'))
);
CREATE INDEX graph_catalog_tags ON graph_catalog USING gin(tags);
CREATE TABLE graph_proposals (
  id uuid PRIMARY KEY,
  source_event_id bigint REFERENCES events(id),
  scope_id text NOT NULL,
  definition jsonb NOT NULL,
  accepted_revision text REFERENCES graph_revisions(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
