"""CPU-only RT-CV compatibility service for the Search and Alerts profiles."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import cv2
import numpy as np

ROOT = Path(os.getenv("RTCV_VIDEO_ROOT", "/data/videos"))
INTERVAL = float(os.getenv("RTCV_EVENT_INTERVAL_SEC", "5"))
DETECTOR = os.getenv("RTCV_DETECTOR", "auto").lower()
ONNX_MODEL = os.getenv("RTCV_ONNX_MODEL", "")
ONNX_INPUT_SIZE = int(os.getenv("RTCV_ONNX_INPUT_SIZE", "640"))
COCO_LABELS = "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush"
LABELS = [label.strip() for label in (os.getenv("RTCV_LABELS") or COCO_LABELS).split(",")]
DEMO_FALLBACK = os.getenv("RTCV_DEMO_FALLBACK", "true").lower() == "true"
KAFKA_SERVERS = os.getenv("RTCV_KAFKA_BOOTSTRAP_SERVERS", "")
RAW_TOPIC = os.getenv("RTCV_RAW_TOPIC", "ds-perception")
ALERT_TOPIC = os.getenv("RTCV_ALERT_TOPIC", "mdx-alerts")
INCIDENT_TOPIC = os.getenv("RTCV_INCIDENT_TOPIC", "mdx-incidents")
ES_URL = os.getenv("RTCV_ELASTICSEARCH_URL", "")
RULES_FILE = Path(os.getenv("RTCV_RULES_FILE", "/etc/rt-cv-local/rules.json"))


class StreamValue(BaseModel):
    camera_id: str
    camera_name: str = ""
    camera_url: str
    change: str = "camera_add"
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamRequest(BaseModel):
    key: str = "sensor"
    value: StreamValue
    headers: dict[str, Any] = Field(default_factory=dict)


class Rule(BaseModel):
    name: str = "default-safety-rule"
    enabled: bool = True
    object_type: str = "person"
    severity: str = "high"
    min_confidence: float = Field(default=0.0, ge=0, le=1)


class RuleUpdate(BaseModel):
    rules: list[Rule]


streams: dict[str, dict[str, Any]] = {}
tasks: dict[str, asyncio.Task[None]] = {}
producer: Any = None
rules: list[Rule] = []
hog = cv2.HOGDescriptor()
hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
onnx_net: cv2.dnn_Net | None = None
onnx_session: Any = None


def _load_rules() -> list[Rule]:
    if RULES_FILE.is_file():
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            values = data.get("rules", data) if isinstance(data, dict) else data
            return [Rule.model_validate(value) for value in values]
        except (OSError, ValueError, TypeError):
            pass
    return [Rule()]


def _path_from_url(url: str) -> Path:
    if url.startswith("file://"):
        return Path(url.removeprefix("file://"))
    return Path(url)


def _duration(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return max(float(result.stdout.strip()), 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _publish(topic: str, payload: dict[str, Any]) -> None:
    if producer is None:
        return
    try:
        producer.send(topic, json.dumps(payload, ensure_ascii=True).encode("utf-8"))
    except Exception:
        # Kafka is an optional sink; Elasticsearch/API verification still works.
        return


def _index(payload: dict[str, Any], kind: str) -> None:
    if not ES_URL:
        return
    import urllib.error
    import urllib.request

    sensor = str(payload["sensorId"]).replace("-", "_")
    index = f"default_{sensor}"
    document = {**payload, "documentType": kind, "@timestamp": payload["timestamp"]}
    request = urllib.request.Request(
        f"{ES_URL.rstrip('/')}/{index}/_doc/{payload['eventId']}",
        data=json.dumps(document).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="PUT",
    )
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except (OSError, urllib.error.URLError):
        return


def _frame(path: Path, offset: float) -> np.ndarray | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(offset), "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, timeout=20, check=True,
        )
        image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _objects(stream: dict[str, Any], offset: float) -> tuple[list[dict[str, Any]], str]:
    if stream["camera_url"].startswith("rtsp://"):
        return ([{"id": "object-0", "type": os.getenv("RTCV_DEMO_OBJECT", "person"), "confidence": 1.0, "bbox": [0, 0, 1, 1]}], "demo")
    image = _frame(Path(stream["path"]), offset)
    if image is not None and DETECTOR in {"onnx", "auto"} and ONNX_MODEL:
        try:
            global onnx_net, onnx_session
            height, width = image.shape[:2]
            detected = []
            if onnx_session is None:
                import onnxruntime
                onnx_session = onnxruntime.InferenceSession(ONNX_MODEL, providers=["CPUExecutionProvider"])
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(rgb, (ONNX_INPUT_SIZE, ONNX_INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
            input_name = onnx_session.get_inputs()[0].name
            outputs = onnx_session.run(None, {input_name: np.transpose(resized.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]})
            logits = next((value for value in outputs if value.ndim == 3 and value.shape[-1] == 80), None)
            boxes = next((value for value in outputs if value.ndim == 3 and value.shape[-1] == 4), None)
            if logits is not None and boxes is not None:
                scores = 1 / (1 + np.exp(-logits[0]))
                for index, score_row in enumerate(scores):
                    class_index = int(np.argmax(score_row))
                    confidence = float(score_row[class_index])
                    if confidence < 0.30:
                        continue
                    cx, cy, box_width, box_height = [float(value) for value in boxes[0][index]]
                    x1, y1 = cx - box_width / 2, cy - box_height / 2
                    x2, y2 = cx + box_width / 2, cy + box_height / 2
                    detected.append({"id": f"object-{index}", "type": LABELS[class_index] if class_index < len(LABELS) else f"class-{class_index}", "confidence": confidence, "bbox": [max(0, x1), max(0, y1), min(1, x2), min(1, y2)]})
            else:
                if onnx_net is None:
                    onnx_net = cv2.dnn.readNetFromONNX(ONNX_MODEL)
                    onnx_net.setInput(cv2.dnn.blobFromImage(image, 1 / 255.0, (ONNX_INPUT_SIZE, ONNX_INPUT_SIZE), swapRB=True, crop=False))
                output = onnx_net.forward()
                rows = output.reshape(output.shape[-2], output.shape[-1]) if output.ndim >= 2 else output.reshape(1, -1)
                if rows.shape[0] < rows.shape[1] and rows.shape[0] <= 256:
                    rows = rows.T
                for index, row in enumerate(rows):
                    if len(row) < 6:
                        continue
                    scores = row[4:]
                    class_index = int(np.argmax(scores))
                    confidence = float(scores[class_index])
                    if confidence < 0.25:
                        continue
                    cx, cy, box_width, box_height = [float(value) for value in row[:4]]
                    scale_x = width if max(cx, cy, box_width, box_height) <= 2 else width / ONNX_INPUT_SIZE
                    scale_y = height if max(cx, cy, box_width, box_height) <= 2 else height / ONNX_INPUT_SIZE
                    x1, y1 = (cx - box_width / 2) * scale_x, (cy - box_height / 2) * scale_y
                    x2, y2 = (cx + box_width / 2) * scale_x, (cy + box_height / 2) * scale_y
                    detected.append({"id": f"object-{index}", "type": LABELS[class_index] if class_index < len(LABELS) else f"class-{class_index}", "confidence": confidence, "bbox": [max(0, x1 / width), max(0, y1 / height), min(1, x2 / width), min(1, y2 / height)]})
            if detected:
                return detected, "onnx"
        except (cv2.error, OSError, ValueError, ImportError, RuntimeError):
            pass
        if DETECTOR == "onnx" and not DEMO_FALLBACK:
            return [], "onnx"
    if image is not None and DETECTOR in {"hog", "auto"}:
        boxes, weights = hog.detectMultiScale(image, winStride=(8, 8), padding=(8, 8), scale=1.05)
        height, width = image.shape[:2]
        detected = []
        for index, (x, y, box_width, box_height) in enumerate(boxes):
            confidence = float(weights[index]) if index < len(weights) else 0.0
            detected.append({"id": f"person-{index}", "type": "person", "confidence": max(0.0, min(confidence, 1.0)), "bbox": [x / width, y / height, (x + box_width) / width, (y + box_height) / height]})
        if detected:
            return detected, "opencv-hog"
    if DEMO_FALLBACK:
        return ([{"id": "object-0", "type": os.getenv("RTCV_DEMO_OBJECT", "person"), "confidence": 1.0, "bbox": [0, 0, 1, 1]}], "demo-fallback")
    return [], "opencv-hog"


def _event(stream: dict[str, Any], offset: float) -> dict[str, Any]:
    objects, detector = _objects(stream, offset)
    # Elasticsearch creates a concrete numeric mapping on first write. Keep
    # every bbox coordinate as a float so boundary values (0/1) do not turn
    # into integer fields and conflict with existing VSS mappings.
    for detected in objects:
        if isinstance(detected.get("bbox"), list):
            detected["bbox"] = [float(value) for value in detected["bbox"]]
    now = datetime.now(timezone.utc).isoformat()
    return {
        "eventId": str(uuid4()),
        "sensorId": stream["camera_id"],
        "streamId": stream["camera_id"],
        "timestamp": now,
        "eventTime": offset,
        "category": "object_detection",
        "objects": objects,
        "source": detector,
        # Keep the media location available to Alert Bridge's local/VST
        # pass-through path without changing the top-level event schema.
        "info": {"video_path": stream["path"]},
    }


def _matches(event: dict[str, Any], rule: Rule) -> bool:
    return rule.enabled and any(
        item.get("type") == rule.object_type and float(item.get("confidence", 0)) >= rule.min_confidence
        for item in event["objects"]
    )


async def _run_stream(stream_id: str) -> None:
    stream = streams[stream_id]
    duration = stream["duration"]
    offset = 0.0
    while stream["status"] == "running":
        event = _event(stream, offset)
        if event["objects"]:
            _publish(RAW_TOPIC, event)
            _index(event, "raw_events")
        for rule in rules:
            if _matches(event, rule):
                alert = {**event, "alertId": event["eventId"], "type": rule.name, "severity": rule.severity, "description": f"{rule.object_type} detected by {rule.name}"}
                _publish(ALERT_TOPIC, alert)
                _publish(INCIDENT_TOPIC, alert)
                _index(alert, "alert")
        offset += INTERVAL
        if duration and offset >= duration:
            stream["status"] = "stopped"
            break
        await asyncio.sleep(INTERVAL)


async def _stop(stream_id: str) -> None:
    stream = streams.get(stream_id)
    if stream:
        stream["status"] = "stopped"
    task = tasks.pop(stream_id, None)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Local RT-CV Compatibility API", version="0.1.0")


@app.on_event("startup")
async def startup() -> None:
    global producer, rules
    rules = _load_rules()
    if KAFKA_SERVERS:
        try:
            from kafka import KafkaProducer
            producer = KafkaProducer(bootstrap_servers=KAFKA_SERVERS.split(","), max_block_ms=3000)
        except Exception:
            producer = None


@app.on_event("shutdown")
async def shutdown() -> None:
    await asyncio.gather(*(_stop(stream_id) for stream_id in list(streams)))
    if producer is not None:
        producer.flush(timeout=3)
        producer.close()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "rt-cv-local", "streams": len(streams)}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/stream/list")
def list_streams() -> list[dict[str, Any]]:
    return list(streams.values())


@app.post("/api/v1/stream/add")
async def add_stream(request: StreamRequest) -> dict[str, Any]:
    value = request.value
    if value.change == "camera_remove":
        return await remove_stream(value.camera_id)
    if value.camera_id in streams:
        await _stop(value.camera_id)
    path = _path_from_url(value.camera_url)
    if not value.camera_url.startswith("rtsp://") and not path.is_file():
        raise HTTPException(404, f"stream file not found: {value.camera_url}")
    stream = {"camera_id": value.camera_id, "camera_name": value.camera_name or value.camera_id, "camera_url": value.camera_url, "path": str(path), "status": "running", "duration": _duration(path) if path.is_file() else 0.0, "source": "rt-cv-local"}
    streams[value.camera_id] = stream
    tasks[value.camera_id] = asyncio.create_task(_run_stream(value.camera_id))
    return stream


@app.post("/api/v1/stream/remove")
async def remove_stream(camera_id: str | None = None, request: dict[str, Any] | None = None) -> dict[str, Any]:
    body = request or {}
    value = body.get("value") if isinstance(body.get("value"), dict) else body
    camera_id = camera_id or str(value.get("camera_id", ""))
    if camera_id not in streams:
        raise HTTPException(404, "stream not found")
    await _stop(camera_id)
    return streams[camera_id]


@app.get("/api/v1/rules")
def get_rules() -> dict[str, Any]:
    return {"rules": [rule.model_dump() for rule in rules]}


@app.put("/api/v1/rules")
def set_rules(update: RuleUpdate) -> dict[str, Any]:
    global rules
    rules = update.rules
    return get_rules()
