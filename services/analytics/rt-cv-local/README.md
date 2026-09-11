# Local RT-CV compatibility service

CPU-only compatibility service for the Search and Alerts developer profiles.
It accepts the RT-CV dynamic stream contract, emits normalized detection events,
evaluates small JSON rules, and optionally publishes Kafka/Elasticsearch output.

The default detector is OpenCV's CPU HOG person detector. Set
`RTCV_DETECTOR=demo` to use the deterministic contract-test detector, or keep
`RTCV_DEMO_FALLBACK=true` so a stream with no HOG detection still exercises the
transport and alert path. DeepStream, CUDA, and NVIDIA libraries are not used.

`POST /api/v1/stream/add` accepts the existing `camera_id`, `camera_name`, and
`camera_url` fields. Local files may use `file://`; RTSP URLs are accepted as a
registration contract but are not decoded by this first version.
