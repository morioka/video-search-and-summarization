# Local NVStreamer compatibility service

This service replaces the NVIDIA NVStreamer container for the license-free
developer profile. It uses public Python, `ffmpeg`, and a shared MediaMTX
RTSP server; no GPU or NVIDIA runtime is required.

## Run locally

```bash
docker build -t vss-nvstreamer-local:dev services/analytics/nvstreamer-local
docker run --rm --network host \
  -e NVSTREAMER_VIDEO_ROOT=/data/videos \
  -v "$PWD/deploy/docker/developer-profiles/dev-profile-lvs/data-dir/videos/dev-profile-lvs:/data/videos" \
  vss-nvstreamer-local:dev
```

Open `http://127.0.0.1:31000` for the Streams, Media Upload, and Management
views. `POST /api/v1/media` accepts MP4/MKV uploads and creates an RTSP
publisher automatically. VIOS-compatible stream discovery is available at
`/vst/api/v1/live/streams`, `/vst/api/v1/sensor/streams`, and
`/vst/api/v1/sensor/list`.

The service publishes to `rtsp://127.0.0.1:8554/<stream-name>` through
MediaMTX. Browser WebRTC preview, ONVIF discovery, synchronized playback, and
record/replay APIs remain outside this first compatibility layer.
