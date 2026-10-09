export LIVING_SEAL_KEY="$(python -c 'import base64;print(base64.b64encode(bytes(range(32))).decode())')"
#!/usr/bin/env bash
set -euo pipefail
python scripts/mock_openai_upstream.py >/tmp/living-upstream.log 2>&1 &
UPSTREAM_PID=$!
litellm --config tests/fixtures/litellm-ci.yaml --port 4000 >/tmp/living-proxy.log 2>&1 &
PROXY_PID=$!
trap 'kill "$UPSTREAM_PID" "$PROXY_PID" 2>/dev/null || true' EXIT
ready=0
for i in $(seq 1 90); do
  if curl -fsS -H "Authorization: Bearer $LIVING_REAL_LITELLM_TOKEN" http://127.0.0.1:4000/v1/models >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then cat /tmp/living-proxy.log; exit 1; fi
python -m unittest discover -s tests -p test_real_litellm_proxy.py -v
