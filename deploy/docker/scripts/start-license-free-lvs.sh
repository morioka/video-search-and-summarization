#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DEPLOY_DIR="$ROOT_DIR/deploy/docker"
PROFILE_DIR="$DEPLOY_DIR/developer-profiles/dev-profile-lvs"
ENV_FILE="${VSS_ENV_FILE:-$PROFILE_DIR/generated.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 1
fi

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts=30
  until curl --max-time 3 -fsS "$url" >/dev/null 2>&1; do
    attempts=$((attempts - 1))
    if (( attempts == 0 )); then
      echo "$label did not become healthy: $url" >&2
      exit 1
    fi
    sleep 2
  done
}

echo "Starting Kafka caption transport..."
(
  cd "$DEPLOY_DIR"
  docker compose \
    --env-file "$ENV_FILE" \
    -f services/infra/compose.yml \
    up -d kafka kafka-topic-init-container
)

echo "Starting OpenAI-compatible RT-VLM..."
if curl --max-time 3 -fsS http://127.0.0.1:8018/v1/health/ready >/dev/null 2>&1; then
  echo "RT-VLM is already healthy on port 8018; reusing it."
else
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$ROOT_DIR/services/rtvi/rt-vlm-openai/docker-compose.cpu.yml" \
    -f "$ROOT_DIR/services/rtvi/rt-vlm-openai/docker-compose.local.yml" \
    up -d
fi
wait_for_http "http://127.0.0.1:8018/v1/health/ready" "RT-VLM"

echo "Starting local Storage and Agent..."
(
  cd "$DEPLOY_DIR"
  docker compose \
    --env-file "$ENV_FILE" \
    -f compose.yml \
    -f "$PROFILE_DIR/license-free.override.yml" \
    up -d vss-vst-storage-local
  wait_for_http "http://127.0.0.1:31001/health" "local VST storage"
  # The base profile declares the NVIDIA RT-VLM as an optional dependency.
  # Do not let Compose pull that image after the local RT-VLM is ready.
  docker compose \
    --env-file "$ENV_FILE" \
    -f compose.yml \
    -f "$PROFILE_DIR/license-free.override.yml" \
    up -d --no-deps vss-agent
)
wait_for_http "http://127.0.0.1:8001/health" "VSS Agent"

echo "Local license-free LVS services started."
