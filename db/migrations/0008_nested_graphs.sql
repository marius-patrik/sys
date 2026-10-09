-- Nested computational units are durable graph activations, not fixed workers.
ALTER TABLE node_runs DROP CONSTRAINT node_runs_state_check;
ALTER TABLE node_runs ADD CONSTRAINT node_runs_state_check
 CHECK(state IN ('leased','retry_wait','awaiting_child','completed','failed','uncertain'));
ALTER TABLE node_runs ADD COLUMN child_activation_id uuid REFERENCES activations(id);
CREATE INDEX node_runs_awaiting_idx ON node_runs(child_activation_id)
 WHERE state='awaiting_child';
