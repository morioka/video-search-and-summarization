#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MODEL_DIR="${RTCV_MODEL_DIR:-$ROOT_DIR/deploy/docker/models/rt-cv-local}"
MODEL_PATH="$MODEL_DIR/model.onnx"
MODEL_URL="https://huggingface.co/AnnotateIt/rtdetr-r18vd-coco-onnx/resolve/main/model.onnx"
EXPECTED_SHA256="11843b02455cc24009aed24d4c40db721b1093be5ccd6bbe7b9c441abb1d0558"

mkdir -p "$MODEL_DIR"
if [[ -f "$MODEL_PATH" ]]; then
  echo "Checking existing RT-DETR model..."
else
  echo "Downloading Apache-2.0 RT-DETR R18 COCO model..."
  curl -L --fail --retry 2 -o "$MODEL_PATH" "$MODEL_URL"
fi

actual="$(sha256sum "$MODEL_PATH" | awk '{print $1}')"
if [[ "$actual" != "$EXPECTED_SHA256" ]]; then
  echo "SHA-256 mismatch for $MODEL_PATH" >&2
  echo "expected: $EXPECTED_SHA256" >&2
  echo "actual:   $actual" >&2
  exit 1
fi
echo "RT-DETR model verified: $MODEL_PATH"
