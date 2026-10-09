-- MCP task handles are persistent and scoped, independent of HTTP connections.
CREATE TABLE mcp_tasks(
  id uuid PRIMARY KEY,
  event_id bigint NOT NULL REFERENCES events(id),
  scope_id text NOT NULL,
  principal_id text NOT NULL REFERENCES control_principals(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  cancelled_at timestamptz,
  UNIQUE(event_id,scope_id)
);
CREATE INDEX mcp_tasks_scope_idx ON mcp_tasks(scope_id,created_at DESC);
