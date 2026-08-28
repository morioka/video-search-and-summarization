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
- `POST /v1/chat/completions` (Alert/OpenAI Chat互換。`video_url`を受け取り、同じフレーム抽出・VLM推論を実行)
- `POST /v1/streams/add`, `GET /v1/streams/get-stream-info`, and `DELETE /v1/streams/delete/{id}` (best-effort RTSP/file worker)
- NVIDIA client compatibility aliases: `POST /v1/stream/add`, `POST /v1/stream/remove`, `GET /v1/stream/get-stream-info`,
  `DELETE /v1/generate_captions/{id}`, and `DELETE /v1/streams/delete-batch`

The stream worker captures short FFmpeg chunks from RTSP (or `file://` test sources), sends them through the same
OpenAI multimodal path, and publishes optional Kafka captions. It is intentionally a minimal compatibility layer:
reconnection policy, audio, URL asset ingestion, and embeddings remain outside this service. The Chat Completions
facade is provided for Alert interoperability and is limited to one `video_url` per request.

Optional alert bridge: set `RTVI_OPENAI_ALERT_ENDPOINT` to the Alert service base URL and
`RTVI_OPENAI_ALERT_KEYWORDS` to a comma-separated keyword list. Only matching stream captions
are posted to `/api/v1/incidents`; both variables are empty by default.

## Backend selection

VSS components use OpenAI-compatible HTTP interfaces, so inference can be
placed locally or remotely without changing the pipeline. Select the Agent/LVS
LLM independently from the RT-VLM VLM:

| Component | Local NIM | Local vLLM/Ollama | Hosted API |
|---|---|---|---|
| LLM | `LLM_MODEL_TYPE=nim` | `LLM_MODEL_TYPE=openai` | `LLM_MODEL_TYPE=openai` |
| VLM | `VLM_MODEL_TYPE=nim` | `VLM_MODEL_TYPE=openai` | `VLM_MODEL_TYPE=openai` |

For the latter two modes set `LLM_BASE_URL`/`VLM_BASE_URL`, the model names
(`LLM_NAME`/`VLM_NAME`), and `OPENAI_API_KEY`. Alert also accepts
`VLM_BASE_URL`, `VLM_MODEL`, and `VLM_API_KEY` overrides. NIM is optional for
the local replacement images.

## Run locally

```bash
cd services/rtvi/rt-vlm-openai
export OPENAI_API_KEY=<key>
export VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME=<multimodal-model-id>
uv run --extra dev uvicorn rt_vlm_openai.app:app --host 0.0.0.0 --port 8018
```

For a non-OpenAI provider exposing the same API, also set `VIA_VLM_ENDPOINT` and optionally `VIA_VLM_API_KEY`.

VSSのLLMとVLMは独立して切り替えられます。例えば、VLMだけOpenAI互換サーバーへ向ける場合は
`VLM_MODEL_TYPE=openai`、`VLM_BASE_URL=https://<server>`（`/v1`はCompose側で付与）、`VLM_NAME=<model>`を設定し、
LLM側は`LLM_MODEL_TYPE`、`LLM_BASE_URL`、`LLM_NAME`で指定します。OpenAI本体を使う場合は
`OPENAI_API_KEY`を共用できます。保存動画の要約を有効にする場合だけ
`LVS_CAPTION_GENERATE_SUMMARY=true`を設定します（既定値は`false`）。

## End-to-end check

Before an E2E run, check the startup prerequisites in order. The default ports use
the direct NVStreamer endpoint (31000), not the legacy VST ingress port (30888):

```bash
uv run python scripts/preflight.py \
  --vst http://127.0.0.1:31000 \
  --rtvi http://127.0.0.1:8018 \
  --lvs http://127.0.0.1:38111 \
  --require-stream konro_resume4 \
  --check-timeline-api
```

Use `--agent http://127.0.0.1:8001` when the Agent is moved off port 8000 because
VST occupies that port. Do not proceed to Agent search if the VST inventory is empty.
For stored-video completion, `--check-timeline-api` catches the common case where a
standalone NVStreamer accepts uploads but does not expose the storage adaptor APIs.

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
