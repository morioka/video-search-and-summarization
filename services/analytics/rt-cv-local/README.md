# Local RT-CV compatibility service

CPU-only compatibility service for the Search and Alerts developer profiles.
It accepts the RT-CV dynamic stream contract, emits normalized detection events,
evaluates small JSON rules, and optionally publishes Kafka/Elasticsearch output.

When `RTCV_ONNX_MODEL` points to a YOLO-compatible ONNX model, the service uses
the generic CPU ONNX detector (`RTCV_DETECTOR=onnx` or the default `auto`). The
same adapter accepts the Apache-2.0 RT-DETR R18 COCO graph from
`AnnotateIt/rtdetr-r18vd-coco-onnx` (82 MB; outputs `logits` and `pred_boxes`).
If no model is configured it uses OpenCV's CPU HOG person detector. Set
`RTCV_DETECTOR=demo` to use the deterministic contract-test detector, or keep
`RTCV_DEMO_FALLBACK=true` so a stream with no detection still exercises the
transport and alert path. DeepStream, CUDA, and NVIDIA libraries are not used.

`POST /api/v1/stream/add` accepts the existing `camera_id`, `camera_name`, and
`camera_url` fields. Local files may use `file://`; RTSP URLs are accepted as a
registration contract but are not decoded by this first version.

Example with an externally downloaded model:

```bash
docker run --rm --network host \
  -e RTCV_DETECTOR=onnx -e RTCV_ONNX_MODEL=/models/model.onnx \
  -v "$PWD/model.onnx:/models/model.onnx:ro" \
  vss-rt-cv-local:dev
```

For the developer profile, run
`deploy/docker/scripts/download-rtdetr-model.sh`. It stores the verified model
under `deploy/docker/models/rt-cv-local/`; Compose mounts that directory read-only.
