-- Database-owned selective memory attention and candidate evidence.
CREATE TABLE attention_policies (
  id text PRIMARY KEY,
  event_kind text NOT NULL UNIQUE,
  min_length integer NOT NULL DEFAULT 16 CHECK (min_length>=1),
  revision integer NOT NULL DEFAULT 1,
  enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE memory_decisions (
  event_id bigint PRIMARY KEY REFERENCES events(id),
  policy_id text NOT NULL,
  policy_revision integer NOT NULL,
  choice text NOT NULL CHECK(choice IN('ignore','candidate','defer')),
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memory_candidates (
  id uuid PRIMARY KEY,
  scope_id text NOT NULL,
  source_event_id bigint NOT NULL UNIQUE REFERENCES events(id),
  content text NOT NULL CHECK(length(content) BETWEEN 1 AND 12000),
  state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','approved','rejected')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pending_memory_candidates_idx ON memory_candidates(scope_id,created_at) WHERE state='pending';
