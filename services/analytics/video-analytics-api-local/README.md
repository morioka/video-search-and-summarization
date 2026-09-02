# License-free Video Analytics API

This small OpenAI/NVIDIA-independent service implements the read-only endpoints
used by the VSS Alerts screen. It queries Elasticsearch directly and is intended
for local evaluation when the proprietary `vss-video-analytics-api` image is not
available.

```bash
docker build -t vss-video-analytics-api-local:dev services/analytics/video-analytics-api-local
docker run -d --name vss-video-analytics-api --network host \
  -e ELASTICSEARCH_URL=http://127.0.0.1:9200 \
  vss-video-analytics-api-local:dev
```

The service listens on port `8081`, matching `VIDEO_ANALYTICS_API_PORT` in the
local HAProxy configuration. Supported endpoints are `GET /health`,
`GET /v1/sensor/list`, `GET /alerts`, `GET /alerts/severe`, `GET /incidents`,
and `GET /frames/alerts`. Sensor listing includes the configured fallback sensor
and sensor IDs discovered from Elasticsearch caption indices. Sensor filters
match both native alert documents and the nested metadata used by raw captions.
The `/incidents` route is the endpoint used by the shipped Alerts UI and returns
an `incidents` array for compatibility. Raw caption documents remain explicitly
marked as `video_event` with no VLM verdict or severity; they are not converted
into formal alerts.
