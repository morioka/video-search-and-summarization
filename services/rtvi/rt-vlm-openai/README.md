# OpenAI RT-VLM Compatibility Service

日本語の実装状況、検証結果、既知の制約、今後の計画は
[`IMPLEMENTATION_PLAN_JA.md`](IMPLEMENTATION_PLAN_JA.md)を参照してください。

This service is an independently buildable replacement for the stored-video portion of VSS RT-VLM. It uploads video
files, splits them into time-based chunks, extracts ordered JPEG frames with FFmpeg, calls an OpenAI-compatible
multimodal Chat Completions endpoint, and returns the RT-VLM caption response over REST or SSE.

Supported endpoints:

- `GET /v1/health/ready` and `GET /v1/health/live`
- `GET /v1/models`
- `POST /v1/files`, `GET /v1/files`, and `DELETE /v1/files/{id}`
- `POST /v1/generate_captions`

This first version intentionally does not implement RTSP, Kafka/NvSchema publishing, URL ingestion, audio, embeddings,
or the OpenAI-compatible `/v1/chat/completions` facade.

## Run locally

```bash
cd services/rtvi/rt-vlm-openai
export OPENAI_API_KEY=<key>
export VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME=<multimodal-model-id>
uv run --extra dev uvicorn rt_vlm_openai.app:app --host 0.0.0.0 --port 8018
```

For a non-OpenAI provider exposing the same API, also set `VIA_VLM_ENDPOINT` and optionally `VIA_VLM_API_KEY`.

## End-to-end check

Start the service with a real OpenAI API key and an image-capable model, then run the client against a local video:

```bash
UV_CACHE_DIR=/tmp/vss-uv-cache uv run --extra dev python scripts/e2e.py \
  --base-url http://127.0.0.1:8018 \
  --video /path/to/video.mp4 \
  --chunk-duration 30 \
  --frames-per-chunk 8 \
  --stream
```

The client uploads the video, requests captions, prints every SSE payload, and deletes the uploaded asset. Pass
`--keep-file` to retain the server-side copy for debugging.

## CPU-only stored-video validation

The OpenAI compatibility service does not need a local GPU when it sends frames to a remote OpenAI-compatible API.

When it is included through the VSS profile compose files, those files retain
the NVIDIA runtime and device reservation required by the original RT-VLM.
On a host without NVIDIA Container Toolkit, apply
`deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-openai-no-gpu.override.yml` as a
second Compose file. It clears the runtime and GPU device reservation for the
`rtvi-vlm` service; the other VSS services may still require GPU access.
Start the standalone CPU profile (this intentionally excludes VIOS, NVStreamer, and the rest of the VSS stack):

```bash
cd services/rtvi/rt-vlm-openai
set -a; . /path/to/env; set +a
VLM_NAME=gpt-4.1-mini docker compose -f docker-compose.cpu.yml up -d

uv run --extra dev python scripts/e2e.py \
  --base-url http://127.0.0.1:8018 \
  --video /home/morioka/temp/Video-to-SOP-Generator/Videos/konro_inspection.mp4 \
  --chunk-duration 30 \
  --frames-per-chunk 8 \
  --stream

docker compose -f docker-compose.cpu.yml down
```

This validates upload, FFmpeg frame extraction, OpenAI multimodal inference, VSS-compatible JSON/SSE output, and
asset deletion. Real-time input and VIOS/DeepStream processing remain outside this CPU-only check.

## Build and use with the VSS Compose stack

```bash
docker build -t vss-rt-vlm-openai:local services/rtvi/rt-vlm-openai

export RTVI_VLM_IMAGE=vss-rt-vlm-openai:local
export RTVI_VLM_MODEL_TO_USE=openai-compat
export VLM_NAME=<multimodal-model-id>
export OPENAI_API_KEY=<key>
```

Then start the normal `lvs` profile with its remote-VLM configuration. The shared Compose service accepts
`RTVI_VLM_IMAGE` as an image override. The container retains the service name and port expected by LVS.

## Test

```bash
cd services/rtvi/rt-vlm-openai
uv run --extra dev pytest
uv run --extra dev ruff check .
```
