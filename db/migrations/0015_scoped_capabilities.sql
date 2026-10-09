-- Global bootstrap capabilities are distinct from workspace-owned definitions.
-- Previously unscoped custom aliases are disabled and must be republished in
-- the owning scope. Their historical activation snapshots remain executable.
ALTER TABLE capability_registry ADD COLUMN scope_id text NOT NULL DEFAULT '*';
ALTER TABLE capability_registry DROP CONSTRAINT capability_registry_pkey;
ALTER TABLE capability_registry ADD CONSTRAINT capability_registry_pkey
 PRIMARY KEY (scope_id,id,revision);
DROP INDEX capability_current_idx;
CREATE INDEX capability_current_idx ON capability_registry(scope_id,id,revision DESC)
 WHERE active;

ALTER TABLE capability_registry DISABLE TRIGGER capability_revision_no_update;
UPDATE capability_registry SET active=false WHERE scope_id='*' AND id NOT IN (
 'text.echo','text.upper','text.prefix','view.text',
 'memory.search','memory.remember','memory.attend','memory.approve',
 'model.answer','input.dispatch','graph.compose','program.python',
 'dsh.tool','graph.call','goal.create'
);
ALTER TABLE capability_registry ENABLE TRIGGER capability_revision_no_update;
