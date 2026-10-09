-- Track scheduler scans separately from externally visible progress timestamps.
ALTER TABLE activations ADD COLUMN scheduler_checked_at timestamptz NOT NULL DEFAULT '-infinity'::timestamptz;
CREATE INDEX activation_scheduler_idx ON activations(scheduler_checked_at,created_at,id)
 WHERE state IN ('pending','running');