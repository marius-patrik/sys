-- Freeze executable capability contracts for each activation.
ALTER TABLE activations ADD COLUMN capability_pins jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK(jsonb_typeof(capability_pins)='object');
CREATE FUNCTION living_capability_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'capability revisions are immutable; publish a new revision';
END $$;
CREATE TRIGGER capability_revision_no_update BEFORE UPDATE OR DELETE ON capability_registry
FOR EACH ROW EXECUTE FUNCTION living_capability_revision_immutable();
