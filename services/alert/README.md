# Alerts Microservice

**A modular, configuration-driven Alerts microservice for the Video Search and
Summarization (VSS) blueprint — VLM-based alert verification, realtime alert
generation, and on-demand clip verification.**

## Overview

The Alerts Microservice processes alerts and incidents produced by the VSS pipeline and
uses a Vision-Language Model (VLM) to confirm, classify, and enrich them. It
supports three modes:

- **Alert verification** (primary) — alerts generated upstream by real-time CV
  detection and behavior analytics are reviewed by a VLM to reduce false
  positives. For each alert, the service resolves the corresponding video
  segment from the video service using the sensor ID and alert timestamps,
  renders an alert-type-specific prompt, and sends the clip to a VLM backend
  over an OpenAI-compatible API. It returns a structured verdict (confirmed /
  rejected / unverified) with a reasoning trace.
- **Realtime alerts** — register realtime alert rules that run continuous VLM
  processing over input streams (including "always-on" refinement); generated
  alerts are published over Kafka.
- **On-demand verification** — third-party CV applications can request VLM
  verification of a stored video snippet.

Alerts use the NvSchema `nv.Incident` / `nv.Behavior` formats (JSON or
Protobuf) and are ingested over **Kafka** or the **HTTP API**. Verified results
are persisted to **Elasticsearch** and can optionally be re-published to Kafka.
The VLM backend is pluggable — an OpenAI-compatible endpoint such as an NVIDIA
VLM NIM (e.g. Cosmos Reason), the RTVI VLM microservice, or a remote model
endpoint.

> **No Redis required.** Earlier releases used Redis for dedup/filter
> caching and alert-config storage. That dependency has been removed:
> deduplication, the end-time delta filter and the (optional) rate limit
> run as **in-process** state per consumer, while confirmed-verdict
> protection and alert-type configs are stored in **Elasticsearch**.
> Because `mdx-incidents` is partitioned by `sensorId`, every event for a
> dedup cohort is routed to the same consumer, so no cross-pod
> coordination — and therefore no shared cache — is needed. Multi-replica
> deployments work unchanged: each pod owns its Kafka partitions and keeps
> its own in-process state; on restart/rebalance the pod taking over
> rebuilds state from new events (verdict protection survives via ES).

## Project Structure

All importable packages live under `src/` (see [`src/README.md`](src/README.md)
for a detailed layout + data-flow diagram).

| Path | Purpose |
|------|---------|
| `enhance_alert_with_vlm.py` | Alert-verification pipeline orchestrator (entrypoint, repo root) |
| `src/handlers/` | Alert-type config (Elasticsearch-backed), direct-media, and prompt handling |
| `src/vlm/` | VLM client (OpenAI-compatible) and warmup |
| `src/schemas/` | NvSchema request/response entities, VLM response model, and pluggable response parsers |
| `src/realtime/` | Realtime + always-on alert rules and the RTVI VLM client |
| `src/web/` | REST API and on-demand verification service |
| `src/vst/` | VST video-clip resolution (sensor ID + timestamps) |
| `src/clients/` | Elasticsearch client + in-process dedup/verdict-protection state handler |
| `src/persistence/` | Elasticsearch persistence store |
| `src/mdx/` | Alert ingestion sources/sinks (Kafka, Redis Streams, Elasticsearch, console) |
| `blueprint_config/` | Example configs for the warehouse / public-safety / smart-city blueprints |
| `test/` | Unit, functional, and end-to-end tests (see `test/TEST_README.md`) |

## Prerequisites

- Python 3.12+
- Docker and Docker Compose
- A reachable OpenAI-compatible **VLM backend** (configured in `config.yaml`)
- **Elasticsearch** (durable storage for alert configs + confirmed-verdict protection)
- Depending on your source/sink choice: **Kafka** and/or **Elasticsearch**
- **Redis** only if you opt into the Redis Streams transports (see
  [Event bridge transports](#event-bridge-transports)). Alert MS keeps no state
  in Redis and never deploys it.

## Installation

```bash
pip install -r requirements.txt
```

Or build/run with Docker (see Quick Start).

## Quick Start

1. **Configure** — edit `config.yaml`: set the VLM `base_url`/`model`, the
   Kafka/Elasticsearch endpoints, and the sink type. Optionally override
   request defaults in `alert_request_defaults.yaml` (or point
   `ALERT_AGENT_DEFAULTS_FILE` at a custom file). Dedup / end-time-delta /
   verdict-protection tuning lives under `alert_agent.event_filters`.

2. **Start the stack** (Kafka source/sink is the default; no Redis):

   ```bash
   docker compose -f deploy_docker-compose.yml up -d

   # or with a custom config file
   ALERT_BRIDGE_CONFIG_FILE=./your-config.yaml docker compose -f deploy_docker-compose.yml up -d
   ```

3. **Verify** — the service is available at:
   - Health: `http://localhost:9080/health`
   - API docs (Swagger): `http://localhost:9080/docs`
   - OpenAPI spec: `http://localhost:9080/openapi.json`

To run the verification pipeline directly (without Docker):

```bash
python enhance_alert_with_vlm.py --config config.yaml
```

## Observability

Set `PROMETHEUS_METRICS_ENABLED=true` before starting the service to expose
Prometheus metrics at `http://localhost:9081/metrics`. Kafka pipeline metrics
use the existing `alert_bridge_*` event and latency series. Requests accepted
through `POST /api/v1/verification/ondemand` use a separate
`alert_bridge_ondemand_*` family for request outcomes, completed-event verdicts,
VLM/background/request-to-publish latency, and verification failures.

The scrape endpoint is not a Prometheus query server: configure Prometheus to
scrape port 9081, then use the reporting tool documented in
[`test/latency/README.md`](test/latency/README.md). On-demand metrics are
aggregate-only; `alert_agent.metrics.per_sensor_labels` applies to Kafka
pipeline metrics.

## Configuration

`config.yaml` controls the runtime. Key sections:

- **`vlm`** — `base_url` (OpenAI-compatible VLM endpoint), `model`, generation params.
- **source / sink** — `kafka` (ingestion) and `elasticsearch`/`kafka` (output sink).
- **persistence / elastic** — Elasticsearch host for durable storage.

Per-alert-type verification prompts and VLM parameters are seeded from
`alert_type_config.json` and stored in **Elasticsearch** (index
`ab-alert_configs`). They can be managed at runtime via the Verification
Config API (`POST/PUT/GET /api/v1/verification/config[/{alert_type}]`); the
pipeline reads through to Elasticsearch on each VLM call (an in-process cache
is read-through by default), so updates apply without a restart. Set
`persistence.cache_ttl_seconds > 0` to cache config reads at the cost of
bounded cross-process staleness.

## Pipeline modes & concurrency sizing

`alert_agent.pipeline_mode` selects how per-message processing is dispatched
(invalid values fail startup; unset derives from the legacy
`async_io.enabled` flag):

| Mode | Dispatch | VLM concurrency ceiling | Use |
|---|---|---|---|
| `sync` | inline in the batch worker | `num_workers` | default / rollback |
| `thread_bridge` | dispatch thread pool, blocking wait | `async_dispatch_workers` | legacy async mode, rollback |
| `event_loop` | coroutine-per-message on one persistent loop; async clients per stage (VLM `AsyncOpenAI`, VST `httpx`, sink/verdict `AsyncElasticsearch`) | `async_io.max_vlm_concurrent` | non-blocking mode: Kafka consumption decoupled from VLM latency |

Knob meaning per mode:

- `async_dispatch_workers` — thread_bridge: dispatch-pool thread count (the
  throughput lever). event_loop: no pool is created; the value only serves as
  the default for the per-service caps.
- `async_dispatch_max_in_flight` — both async modes: global in-flight bound;
  when full, hand-off pauses and backpressure reaches the Kafka consume loop.
  It bounds memory, it does not raise the throughput ceiling.
- `async_io.max_vlm_concurrent` / `max_vst_concurrent` — event_loop only:
  per-service concurrency caps (asyncio semaphores).

Sizing rule (event_loop): size against the **survivor rate** (events that
pass dedup and reach the VLM — `rate(alert_bridge_events_after_dedup_total)`,
peak value), not raw ingest:

```
max_vlm_concurrent ≈ peak_survivor_rate × VLM_latency_p95 × 1.4 (headroom)
async_dispatch_max_in_flight ≈ 2–4 × max_vlm_concurrent
```

The sustainable rate ("knee") is `max_vlm_concurrent ÷ VLM_latency`; below it
consumer lag stays flat, above it lag grows by design (bounded backpressure).

### VLM concurrency ceiling benchmark (run before raising `max_vlm_concurrent`)

`max_vlm_concurrent` must never exceed what the VLM backend actually serves
concurrently — beyond that point requests queue inside the backend and its
latency balloons instead of throughput improving. To find the ceiling:

1. Deploy the VLM backend as in production (same GPU, memory-utilization and
   batching settings).
2. Ramp offered concurrency stepwise (e.g. 2 → 4 → 8 → 16 …), ≥60 s per step,
   using representative clips.
3. At each step record wait-excluded per-call latency
   (`alert_bridge_vlm_duration_seconds`, with capacity-wait tracked
   separately in `alert_bridge_capacity_wait_seconds`).
4. The ceiling is the last step where per-call latency stays within ~120% of
   the low-concurrency baseline; the next step marks saturation.
5. Set `max_vlm_concurrent` at or below the ceiling. Watch
   `alert_bridge_event_loop_vlm_in_flight` (never exceeds the cap) and
   capacity-wait growth (backpressure building) in production.

## Usage

Submit an alert over the REST API:

```bash
curl -X POST http://localhost:9080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d @test/protobuf/test_data/sample_alert.json
```

Enriched results are persisted to Elasticsearch and published to the Kafka
sink (`event_bridge.sinkType: kafka`). Consumers receive alerts by subscribing
to the configured sink topic, and can also query stored alerts/incidents over
the REST API (e.g. `GET /api/v1/realtime`, `GET /api/v1/realtime/incidents`).

## Event bridge transports

Three transport selections are made independently, so a deployment can move one
of them off Kafka without touching the others. Kafka is the default source and
sink, and Elasticsearch the default for VLM-enhanced results, so a config that
sets none of these behaves exactly as before.

| Setting | Default | Alternatives | Carries |
|---------|---------|--------------|---------|
| `event_bridge.sourceType` | `kafka` | `redisStream` | Incoming Alert and Incident payloads |
| `event_bridge.sinkType` | `kafka` | `redisStream`, `console` | Validation-error responses |
| `vlm_enhanced_sink.type` | `elastic` | `kafka`, `redisStream`, `console` | VLM-verified Alert and Incident results |

Transport names are matched case-insensitively and ignore `_` and `-`, so
`redisStream`, `redis_stream` and `redis` all select the same implementation. The
resolved name is logged next to the configured one at startup, which is the
quickest way to confirm a value was understood as intended.

One VLM-enhanced sink serves both incidents and alerts, so `vlm_enhanced_sink.type`
is read only at that top level. A `type` nested under `incident:` or `alert:` has
never been read; older configs carry one, and the service now warns when it finds
one that disagrees with the transport actually in use. The per-kind blocks carry
routing — index, stream, topic — not transport selection.

Selecting a `redisStream` transport requires an existing Redis instance —
Alert MS does not deploy one, and none of the service's own state lives there
(dedup state is in-process, durable state is in Elasticsearch). The connection
comes from the top-level `redis` block, the analogue of
`kafka.bootstrap_servers`; the per-component blocks (`event_bridge.redis_source`,
`event_bridge.redis_sink`, `vlm_enhanced_sink.redisStream`) hold the stream names
and may override any connection field. `config.yaml` carries a commented example
of each.

Payloads use the MDX stream envelope — `XADD <stream> * key <sensorId> value
<payload> headers <json>` — which is what vss-behavior-analytics publishes and
what the Logstash `redis_stream` input consumes. The Redis source reads both
encodings the envelope carries (protobuf and JSON text); the Redis sink writes
the same protobuf messages the Kafka sink does, so downstream consumers decode
either transport identically.

The `console` sink renders results to the log instead of a datastore. It needs no
broker, which makes it the quickest way to inspect verdicts while developing, but
output is not durable and nothing downstream can consume it. It writes the whole
document, which includes the VLM's reasoning about the people and vehicles in the
footage, the VST video URL and the GPS fix; where the log is collected somewhere
that should not hold those, list the dotted paths in
`event_bridge.console_sink.redact` or `vlm_enhanced_sink.console.redact` and they
are masked before the line is written. The verdict itself stays readable, so the
sink remains useful with redaction on.

### Delivery semantics

Both sources are **at-most-once**, and deliberately so: the Kafka source commits
offsets inside its poll loop and the Redis source `XACK`s once a batch is decoded
— in both cases before the VLM has verified anything. A crash mid-verification
therefore drops that batch rather than replaying it. The alternative costs more
than it returns here: verification is the expensive step, dedup state is
in-process and does not survive a restart, so replaying a batch would re-run the
VLM and can publish a second verdict for an event already in Elasticsearch.
Choosing Redis Streams does not change this contract in either direction, which
is the point — the transport is swappable without the pipeline's guarantees
moving underneath it.

One consequence worth knowing when reading `XPENDING` output: because the ack
follows the read by milliseconds, a non-empty PEL means a consumer died inside
that window, not that a backlog is waiting to be reclaimed. Nothing sweeps the
PEL on startup.

Read-path drops are counted, not just logged. An entry with no payload field or
one that fails to decode is acked and discarded — the right call for a poison
pill, since leaving it un-acked replays it forever — and shows up under
`alert_bridge_source_dropped_total{transport="redis_stream",reason=...}` with
`reason` in `no_payload` / `undecodable`. A rising count means a producer is
emitting entries this consumer cannot read.

### Scaling the Redis source: dedup needs consumer affinity

**Run one Alert MS replica per Redis consumer group, or shard by sensorId.**

Dedup, the end-time delta filter and the VLM rate limit are all kept
**in-process**, and that is only sound because a given `sensorId` is always seen
by the same instance. On Kafka that holds structurally: `mdx-incidents` is
partitioned by `sensorId`, every dedup cohort key is prefixed with `sensorId`,
and a consumer owns whole partitions — so a cohort never splits across pods
(`test_multi_consumer_dedup` pins this).

A Redis Streams consumer group gives no such guarantee. `XREADGROUP` hands each
entry to whichever consumer asks first, and each replica registers under its own
consumer name (`alert-bridge-<host>-<pid>`). Two replicas on one group therefore
interleave the same sensor's events, each sees only part of the cohort, and
duplicates that in-process dedup would have suppressed reach the VLM instead —
extra verification cost and duplicate verdicts, quietly. Nothing errors.

How bad the duplicate gets depends on the **sink**, and the all-Redis
configuration is the worst case:

| Sink | What a cross-replica duplicate costs |
|---|---|
| `elastic` | The VLM verifies twice (wasted GPU), but the sink indexes by fingerprint (`document["Id"]`), so Elasticsearch still holds **one** document. |
| `redisStream` / `kafka` | The VLM verifies twice **and** both verdicts are appended — `XADD` has no doc-id equivalent, so a genuine duplicate reaches downstream consumers. |

`test_redis_multi_consumer_dedup` demonstrates both: with two replicas on one
group, twelve events for a single sensorId split 6/6 across them, and a repeated
fingerprint produced two publishes that only Elasticsearch's doc id collapsed.

If you must run more than one replica against Redis, either give each replica
its own consumer group over a disjoint set of streams (shard by sensor), or
enable `alert_agent.event_filters.protect_confirmed_verdicts` so the
Elasticsearch-backed verdict marker catches the duplicates that in-process state
no longer can. Note that it is **off by default**, so an unsharded scale-up has
no backstop as shipped.

The write path is where at-most-once bites hardest, so it does not simply give
up. A `redisStream` sink is the payload's only destination — the source has
already acked and nothing upstream will offer the verdict again — so a failed
`XADD` is retried (`publish_retries`, default 2, with a short linear backoff),
rebuilding the connection first when the failure was a dropped one. Retries are
few on purpose: the caller is on the consume path, so blocking there stalls the
batch behind it. When they are exhausted the payload *is* dropped, and that is
visible rather than silent: the sink logs an error naming the stream, and
`alert_bridge_redis_publish_failures_total{outcome="dropped"}` counts it. The
`outcome="recovered"` series counts blips a retry absorbed — a rising
`recovered` with a flat `dropped` means Redis is unstable but nothing was lost.
Alert on `dropped`.

Startup behaviour differs between the two directions, also deliberately. The
Redis sink pings on construction and refuses to start when Redis is unreachable,
because a sink that cannot reach its destination has nowhere to put results and
would discard them silently. The Redis source instead logs and retries with
backoff, because a consumer outliving a broker restart is normal operation. The
Kafka sink does not ping at all, so moving a sink to Redis makes startup stricter
than it was.

In Docker Compose the selections are environment variables
(`ALERT_EVENT_SOURCE_TYPE`, `ALERT_EVENT_SINK_TYPE`, `ALERT_VLM_SINK_TYPE`,
plus `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD` /
`REDIS_STREAM_MAXLEN`); in Helm they are the `eventSourceType`, `eventSinkType`,
`vlmSinkType`, and `redis.*` values. Leaving them unset keeps the Kafka defaults,
so an existing deployment can take the new image without touching its
environment.

## Testing

Unit tests run with `pytest`:

```bash
pip install -r requirements.txt
pytest
```

For functional and end-to-end testing against local simulators (Kafka +
Elasticsearch, sending sample payloads, verifying responses), see
[`test/TEST_README.md`](test/TEST_README.md).

## Contributing

Contributions are welcome. Please see the repository root
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the contribution process, the
required SPDX license headers, and the DCO sign-off requirement.

## License

This module is governed by **two separate licenses**, depending on what you use:

- **The source code in this directory and its subdirectories is licensed under the Apache License,
  Version 2.0.** The full license text is at the repository root: [`LICENSE`](../../LICENSE). If you
  clone, build, modify, or redistribute the source, Apache 2.0 terms apply.

- **The pre-built VSS Alert container images distributed by NVIDIA via NGC**
  (`nvcr.io/nvidia/blueprint/vss-alert-verification` and related tags) **are licensed under the
  NVIDIA Software License Agreement.** If you pull and use NVIDIA's pre-built container
  images, the NVIDIA Software License Agreement governs your use; the agreement is conveyed by the
  distribution channel those images ship through.

Third-party open-source components bundled in the container image are attributed in
[`LICENSE-3rd-party.txt`](./LICENSE-3rd-party.txt).

The container image carries `LICENSE-3rd-party.txt` and `NVIDIA-Software-License-Agreement.pdf`
under `/app`. The agreement is **not** vendored in this source tree — the Dockerfile's `ADD` instruction
fetches it from `nvidia.com` at build time with a pinned SHA-256, which keeps the repository free
of a proprietary EULA and needs no HTTP client in any build stage.
