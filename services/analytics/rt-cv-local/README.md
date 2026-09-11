# Local RT-CV compatibility service

CPU-only compatibility service for the Search and Alerts developer profiles.
It accepts the RT-CV dynamic stream contract, emits normalized detection events,
evaluates small JSON rules, and optionally publishes Kafka/Elasticsearch output.

When `RTCV_ONNX_MODEL` points to a YOLO-compatible ONNX model, the service uses
the generic CPU ONNX detector (`RTCV_DETECTOR=onnx` or the default `auto`). If no
model is configured it uses OpenCV's CPU HOG person detector. Set
`RTCV_DETECTOR=demo` to use the deterministic contract-test detector, or keep
`RTCV_DEMO_FALLBACK=true` so a stream with no detection still exercises the
transport and alert path. DeepStream, CUDA, and NVIDIA libraries are not used.

`POST /api/v1/stream/add` accepts the existing `camera_id`, `camera_name`, and
`camera_url` fields. Local files may use `file://`; RTSP URLs are accepted as a
registration contract but are not decoded by this first version.
