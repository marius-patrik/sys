UPDATE control_views
SET definition=jsonb_set(definition,'{children}',
  (definition->'children') || '[{"type":"table","title":"Goals","source":"goals"}]'::jsonb),
  revision=revision+1
WHERE id='interface.root'
  AND jsonb_typeof(definition->'children')='array'
  AND NOT (definition->'children' @> '[{"type":"table","source":"goals"}]'::jsonb);
