"""Small license-free compatibility API for the VSS alerts screen."""

from datetime import datetime, timezone
import os
from typing import Any

from elasticsearch import Elasticsearch
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="VSS Video Analytics API (local)")
es = Elasticsearch(os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200"))


def _value(source: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return None


def _query(sensor_id: str | None, place: str | None, start: str | None, end: str | None) -> dict[str, Any]:
    filters: list[dict[str, Any]] = []
    if sensor_id:
        filters.append({"bool": {"should": [{"term": {"sensorId.keyword": sensor_id}}, {"term": {"sensor_id.keyword": sensor_id}}], "minimum_should_match": 1}})
    if place:
        filters.append({"bool": {"should": [{"term": {"place.keyword": place}}, {"term": {"place.name.keyword": place}}], "minimum_should_match": 1}})
    if start or end:
        range_query: dict[str, str] = {}
        if start:
            range_query["gte"] = start
        if end:
            range_query["lte"] = end
        filters.append({"range": {"@timestamp": range_query}})
    return {"bool": {"filter": filters}} if filters else {"match_all": {}}


def _normalize(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source", {})
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    content = metadata.get("content_metadata") if isinstance(metadata.get("content_metadata"), dict) else {}
    sensor_id = _value(source, "sensorId", "sensor_id") or content.get("sensorId")
    if not isinstance(sensor_id, (str, int, float)):
        sensor_id = None
    alert_type = _value(source, "type", "alertType", "alert_type", "category")
    if not isinstance(alert_type, (str, int, float)):
        alert_type = None
    description = _value(source, "description", "text", "reasoning")
    if not isinstance(description, str):
        description = ""
    category = str(alert_type) if alert_type is not None else "video_event"
    timestamp = _value(source, "@timestamp", "timestamp", "startTime", "start_time", "eventTime")
    return {
        **source,
        "id": source.get("id", hit.get("_id")),
        "alertId": source.get("alertId", source.get("alert_id", hit.get("_id"))),
        "sensorId": str(sensor_id) if sensor_id is not None else "",
        "place": _value(source, "place", "location"),
        "type": category,
        "category": category,
        "analyticsModule": {"description": description, "info": {"triggerModules": "raw_event"}},
        "timestamp": timestamp,
        "severity": _value(source, "severity", "level", "priority"),
    }


def _search(sensor_id: str | None, place: str | None, start: str | None, end: str | None, severe: bool = False, query_string: str | None = None) -> dict[str, Any]:
    query = _query(sensor_id, place, start, end)
    if query_string:
        query = {"bool": {"must": [query, {"query_string": {"query": query_string}}]}}
    if severe:
        query = {"bool": {"must": [query], "should": [{"terms": {"severity.keyword": ["high", "critical", "severe"]}}, {"terms": {"level.keyword": ["high", "critical", "severe"]}}], "minimum_should_match": 1}}
    result = es.search(index="default_*,mdx-vlm-*,alerts-*", query=query, size=100, sort=[{"@timestamp": {"order": "desc", "unmapped_type": "date"}}], ignore_unavailable=True)
    total = result["hits"]["total"]
    total = total.get("value", 0) if isinstance(total, dict) else total
    return {"alerts": [_normalize(hit) for hit in result["hits"]["hits"]], "total": total}


@app.get("/health")
def health() -> dict[str, str]:
    try:
        if not es.ping(request_timeout=2):
            raise RuntimeError("Elasticsearch ping failed")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Elasticsearch unavailable: {exc}") from exc
    return {"status": "ok", "service": "video-analytics-api-local"}


@app.get("/v1/sensor/list")
def sensor_list() -> list[dict[str, str]]:
    """Return configured and discovered sensors for local stored-video evaluation."""
    configured_id = os.getenv("VSS_SENSOR_ID", "local-konro-inspection")
    configured_name = os.getenv("VSS_SENSOR_NAME", "konro_inspection")
    sensors: dict[str, dict[str, str]] = {
        configured_id: {"name": configured_name, "sensorId": configured_id, "state": "online"}
    }
    try:
        result = es.search(
            index="default_*",
            size=0,
            query={"exists": {"field": "metadata.content_metadata.sensorId"}},
            aggs={"sensor_ids": {"terms": {"field": "metadata.content_metadata.sensorId.keyword", "size": 100}}},
            ignore_unavailable=True,
        )
        buckets = result.get("aggregations", {}).get("sensor_ids", {}).get("buckets", [])
        for bucket in buckets:
            sensor_id = str(bucket.get("key", "")).strip()
            if sensor_id and sensor_id not in sensors:
                sensors[sensor_id] = {"name": sensor_id, "sensorId": sensor_id, "state": "online"}
    except Exception:
        # Sensor discovery is an enhancement; retain the configured fallback
        # while Elasticsearch is starting or has an older incompatible mapping.
        pass
    return list(sensors.values())


@app.get("/alerts")
def alerts(sensorId: str | None = None, place: str | None = None, fromTimestamp: str | None = None, toTimestamp: str | None = None):
    return _search(sensorId, place, fromTimestamp, toTimestamp)


@app.get("/alerts/severe")
def severe_alerts(sensorId: str | None = None, place: str | None = None, fromTimestamp: str | None = None, toTimestamp: str | None = None):
    return _search(sensorId, place, fromTimestamp, toTimestamp, severe=True)


@app.get("/incidents")
def incidents(
    sensorId: str | None = None,
    place: str | None = None,
    fromTimestamp: str | None = None,
    toTimestamp: str | None = None,
    maxResultSize: int = Query(100, ge=1, le=5000),
    vlmVerified: bool | None = None,
    vlmVerdict: str | None = None,
    queryString: str | None = None,
):
    """Compatibility shape used by the shipped Alerts UI."""
    result = _search(sensorId, place, fromTimestamp, toTimestamp, query_string=queryString)
    # The UI's normalizer consumes `incidents`; retain the original documents.
    return {"incidents": result["alerts"][:maxResultSize], "total": result["total"]}


@app.get("/frames/alerts")
def frame_alerts(sensorId: str | None = None, place: str | None = None, fromTimestamp: str | None = None, toTimestamp: str | None = None):
    return _search(sensorId, place, fromTimestamp, toTimestamp)
