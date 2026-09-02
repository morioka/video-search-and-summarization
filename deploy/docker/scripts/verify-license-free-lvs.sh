#!/usr/bin/env bash
set -euo pipefail

request() {
  local label="$1"
  local url="$2"
  if ! curl --max-time 15 -fsS "$url" >/dev/null; then
    echo "FAIL: $label ($url)" >&2
    exit 1
  fi
  echo "OK: $label"
}

request "RT-VLM" "${RTVLM_URL:-http://127.0.0.1:8018}/v1/health/ready"
request "Local VST storage" "${VST_STORAGE_URL:-http://127.0.0.1:31001}/health"
request "VSS Agent" "${AGENT_URL:-http://127.0.0.1:8001}/health"
request "Elasticsearch" "${ELASTICSEARCH_URL:-http://127.0.0.1:9200}/_cluster/health"
request "Caption indices" "${ELASTICSEARCH_URL:-http://127.0.0.1:9200}/_count?index=default_*"
request "Sensor list" "${ALERTS_API_URL:-http://127.0.0.1:7777/video-analytics-api}/v1/sensor/list"
request "Alerts incidents" "${ALERTS_API_URL:-http://127.0.0.1:7777/video-analytics-api}/incidents?maxResultSize=1"

echo "License-free LVS smoke test passed."
