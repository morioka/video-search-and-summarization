#!/usr/bin/env bash
set -Eeuo pipefail

# Start the CPU Behavior Analytics path after the shared Kafka topics exist.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/docker/services/analytics/behavior-analytics/compose.local.yml"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-kafka}"
TOPICS_CONTAINER="${TOPICS_CONTAINER:-vss-kafka-topics}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-127.0.0.1:9092}"

if ! docker inspect "$KAFKA_CONTAINER" >/dev/null 2>&1; then
  echo "Kafka container '$KAFKA_CONTAINER' was not found" >&2
  exit 1
fi

echo "Waiting for Kafka ($KAFKA_CONTAINER) ..."
for _ in $(seq 1 60); do
  if docker inspect --format '{{.State.Health.Status}}' "$KAFKA_CONTAINER" 2>/dev/null | grep -q healthy; then
    break
  fi
  sleep 2
done
if ! docker inspect --format '{{.State.Health.Status}}' "$KAFKA_CONTAINER" 2>/dev/null | grep -q healthy; then
  echo "Kafka did not become healthy" >&2
  exit 1
fi

if docker inspect "$TOPICS_CONTAINER" >/dev/null 2>&1; then
  echo "Waiting for Kafka topic initialization ..."
  for _ in $(seq 1 60); do
    status="$(docker inspect --format '{{.State.Status}}' "$TOPICS_CONTAINER" 2>/dev/null || true)"
    if [ "$status" = "exited" ]; then
      code="$(docker inspect --format '{{.State.ExitCode}}' "$TOPICS_CONTAINER")"
      [ "$code" = "0" ] || { echo "Kafka topic initialization failed (exit $code)" >&2; exit 1; }
      break
    fi
    sleep 2
  done
fi

export BA_BRIDGE_KAFKA="${BA_BRIDGE_KAFKA:-$KAFKA_BOOTSTRAP}"
cd "$ROOT_DIR"
docker compose -f "$COMPOSE_FILE" up -d
echo "License-free Behavior Analytics and JSON bridge are running."
