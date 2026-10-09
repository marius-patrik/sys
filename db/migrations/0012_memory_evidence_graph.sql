-- Optional model-assisted candidate extraction; still no implicit truth promotion.
ALTER TABLE attention_policies ADD COLUMN model_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE attention_policies ADD COLUMN max_candidates integer NOT NULL DEFAULT 5
 CHECK(max_candidates BETWEEN 1 AND 8);
ALTER TABLE memory_candidates DROP CONSTRAINT memory_candidates_source_event_id_key;
ALTER TABLE memory_candidates ADD COLUMN assertion jsonb NOT NULL DEFAULT '{}'::jsonb
 CHECK(jsonb_typeof(assertion)='object');
ALTER TABLE memory_candidates ADD CONSTRAINT memory_candidates_origin_content_unique
 UNIQUE(source_event_id,content);
ALTER TABLE memory_claims ADD COLUMN assertion jsonb NOT NULL DEFAULT '{}'::jsonb
 CHECK(jsonb_typeof(assertion)='object');
CREATE INDEX memory_claim_assertion_idx ON memory_claims USING gin(assertion);
CREATE TABLE memory_entities(
  id uuid PRIMARY KEY,scope_id text NOT NULL,name text NOT NULL,normalized text NOT NULL,
  UNIQUE(scope_id,normalized)
);
CREATE TABLE memory_relations(
  claim_id uuid NOT NULL REFERENCES memory_claims(id),
  subject_id uuid NOT NULL REFERENCES memory_entities(id),
  predicate text NOT NULL,
  object_id uuid NOT NULL REFERENCES memory_entities(id),
  PRIMARY KEY(claim_id,subject_id,predicate,object_id)
);
CREATE INDEX memory_relations_subject_idx ON memory_relations(subject_id,predicate);
CREATE TABLE memory_conflicts(
  scope_id text NOT NULL,
  claim_a uuid NOT NULL REFERENCES memory_claims(id),
  claim_b uuid NOT NULL REFERENCES memory_claims(id),
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(claim_a,claim_b),
  CHECK(claim_a <> claim_b)
);
ALTER TABLE model_selections DROP CONSTRAINT model_selections_purpose_check;
ALTER TABLE model_selections ADD CONSTRAINT model_selections_purpose_check
 CHECK(purpose IN ('answer','compose','default','memory'));
