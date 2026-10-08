-- Upgrade existing database-owned workspace definitions in place.
UPDATE control_views
SET definition=jsonb_set(definition,'{children}',(definition->'children') ||
'[
 {"type":"form","title":"LiteLLM Connection","endpoint":"/v1/settings/litellm",
  "fields":[{"name":"base_url","label":"Gateway URL","type":"url","required":true},
            {"name":"api_key","label":"Virtual API key","type":"password"}]},
 {"type":"model-picker","title":"LiteLLM Models","models":"/v1/models",
  "selection":"/v1/models/selection","endpoint":"/v1/models/selection"}
]'::jsonb), revision=revision+1
WHERE id='interface.root'
  AND jsonb_typeof(definition->'children')='array'
  AND NOT (definition->'children' @> '[{"type":"form","endpoint":"/v1/settings/litellm"}]'::jsonb);
