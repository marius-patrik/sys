ALTER TABLE control_actions ADD COLUMN scope_id text;
CREATE INDEX control_actions_scope_idx ON control_actions(scope_id,id);
ALTER TABLE graph_catalog ADD COLUMN scope_id text;
CREATE INDEX graph_catalog_scope_idx ON graph_catalog(scope_id,revision_id);
