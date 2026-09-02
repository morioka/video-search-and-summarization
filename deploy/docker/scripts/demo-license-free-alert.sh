#!/usr/bin/env bash
set -euo pipefail

VIDEO_PATH="${VIDEO_PATH:-/home/morioka/temp/Video-to-SOP-Generator/Videos/konro_inspection.mp4}"
STREAM_ID="${STREAM_ID:-local-konro-inspection-pt2}"
DURATION_SECONDS="${DURATION_SECONDS:-45}"
RTVLM_URL="${RTVLM_URL:-http://127.0.0.1:8018}"
ALERT_BRIDGE_URL="${ALERT_BRIDGE_URL:-http://127.0.0.1:9080}"
RTSP_URL="${RTSP_URL:-rtsp://127.0.0.1:8554/konro_inspection}"

if [[ ! -s "$VIDEO_PATH" ]]; then
  echo "Video not found: $VIDEO_PATH" >&2
  exit 1
fi
curl --max-time 15 -fsS "$ALERT_BRIDGE_URL/health" >/dev/null

# Make repeated demos idempotent when a previous run was interrupted.
curl --max-time 10 -fsS -X DELETE "$RTVLM_URL/v1/streams/delete/$STREAM_ID" >/dev/null 2>&1 || true

mediamtx_was_running=0
if docker ps --format '{{.Names}}' | grep -qx vss-mediamtx; then
  mediamtx_was_running=1
else
  docker start vss-mediamtx >/dev/null
fi

ffmpeg -hide_banner -loglevel error -re -stream_loop -1 -i "$VIDEO_PATH" \
  -an -c:v libx264 -preset ultrafast -tune zerolatency \
  -f rtsp -rtsp_transport tcp "$RTSP_URL" >/tmp/license-free-alert-ffmpeg.log 2>&1 &
ffmpeg_pid=$!

cleanup() {
  curl --max-time 10 -fsS -X DELETE "$RTVLM_URL/v1/streams/delete/$STREAM_ID" >/dev/null 2>&1 || true
  kill "$ffmpeg_pid" >/dev/null 2>&1 || true
  wait "$ffmpeg_pid" >/dev/null 2>&1 || true
  if (( mediamtx_was_running == 0 )); then
    docker stop vss-mediamtx >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

curl --max-time 20 -fsS -X POST "$RTVLM_URL/v1/streams/add" \
  -H 'content-type: application/json' \
  -d "{\"streams\":[{\"id\":\"$STREAM_ID\",\"liveStreamUrl\":\"$RTSP_URL\",\"description\":\"Describe visible activity and mention unsafe behavior.\",\"sensor_name\":\"konro_inspection\"}]}" \
  >/dev/null

echo "Streaming $VIDEO_PATH to $STREAM_ID for ${DURATION_SECONDS}s..."
sleep "$DURATION_SECONDS"
incident_count="$(curl --max-time 15 -fsS "$ALERT_BRIDGE_URL/api/v1/realtime/incidents?sensor_id=$STREAM_ID&limit=100" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("count", 0))')"
echo "Alert demo completed; incidents for $STREAM_ID: $incident_count"
