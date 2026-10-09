-- Persist due-time wake events with scoped and revocable principals.
CREATE TABLE scheduled_events (
  id uuid PRIMARY KEY,
  scope_id text NOT NULL,
  principal_id text NOT NULL REFERENCES control_principals(id),
  goal_id uuid REFERENCES goal_units(id),
  action_id text NOT NULL REFERENCES control_actions(id),
  text_input text NOT NULL,
  due_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','fired','cancelled')),
  fired_event_id bigint REFERENCES events(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX due_scheduled_events_idx ON scheduled_events(due_at,id) WHERE state='pending';
ALTER TABLE goal_units ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
