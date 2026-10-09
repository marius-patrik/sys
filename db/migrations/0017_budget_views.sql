-- The database-defined Control view exposes the resource budget policy.
UPDATE control_views
SET definition=jsonb_set(definition,'{children}',(definition->'children') ||
  '[
    {"type":"form","title":"Daily Resource Budgets",
     "endpoint":"/v1/settings/budgets",
     "fields":[
       {"name":"model_calls","label":"LiteLLM calls per UTC day","type":"number","required":true},
       {"name":"external_calls","label":"OCI/DSH calls per UTC day","type":"number","required":true}
     ]}
   ]'::jsonb),
    revision=revision+1
WHERE id='interface.root'
 AND jsonb_typeof(definition->'children')='array'
 AND NOT (definition->'children' @> '[{"type":"form","endpoint":"/v1/settings/budgets"}]'::jsonb);
