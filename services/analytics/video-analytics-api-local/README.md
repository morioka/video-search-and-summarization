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
`GET /alerts`, `GET /alerts/severe`, `GET /incidents`, and `GET /frames/alerts`.
The `/incidents` route is the endpoint used by the shipped Alerts UI and returns
an `incidents` array for compatibility.
