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

echo "Starting OpenAI-compatible RT-VLM..."
docker compose \
  --env-file "$ENV_FILE" \
  -f "$ROOT_DIR/services/rtvi/rt-vlm-openai/docker-compose.cpu.yml" \
  -f "$ROOT_DIR/services/rtvi/rt-vlm-openai/docker-compose.local.yml" \
  up -d

echo "Starting local Storage and Agent..."
(
  cd "$DEPLOY_DIR"
  docker compose \
    --env-file "$ENV_FILE" \
    -f compose.yml \
    -f "$PROFILE_DIR/license-free.override.yml" \
    up -d vss-vst-storage-local vss-agent
)

echo "Local license-free LVS services started."
