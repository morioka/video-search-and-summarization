# Local RT-CV compatibility service

CPU-only compatibility service for the Search and Alerts developer profiles.
It accepts the RT-CV dynamic stream contract, emits normalized detection events,
evaluates small JSON rules, and optionally publishes Kafka/Elasticsearch output.

The first implementation intentionally uses a deterministic demo detector. It
exists to validate stream management, event transport, alert rules, and UI/API
integration without DeepStream, CUDA, or NVIDIA libraries. A real detector can
replace `_event` later without changing the external contract.

`POST /api/v1/stream/add` accepts the existing `camera_id`, `camera_name`, and
`camera_url` fields. Local files may use `file://`; RTSP URLs are accepted as a
registration contract but are not decoded by this first version.
