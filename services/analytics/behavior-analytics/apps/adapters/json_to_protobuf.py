"""Bridge JSON detection events from the local RT-CV service to BA protobuf Frames."""

from __future__ import annotations

import json
import logging
import os
import signal

from confluent_kafka import Consumer, Producer

from mdx.analytics.core.utils.schema_util import dict_frame_to_protobuf_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("json-to-protobuf")


def _frame(event: dict) -> dict:
    """Map the local RT-CV event shape to the public BA Frame JSON shape."""
    objects = []
    for item in event.get("objects", []):
        bbox = item.get("bbox") or [0, 0, 1, 1]
        if isinstance(bbox, list) and len(bbox) == 4:
            # RT-CV uses normalized xyxy coordinates; BA expects pixel-like
            # coordinates but accepts numeric values for downstream rules.
            bbox = {
                "leftX": float(bbox[0]),
                "topY": float(bbox[1]),
                "rightX": float(bbox[2]),
                "bottomY": float(bbox[3]),
            }
        object_type = str(item.get("type", "Object"))
        # DeepStream/BA labels are conventionally title-cased (for example,
        # ``Person``), while the local RT-CV API uses COCO lowercase labels.
        if object_type.lower() == "person":
            object_type = "Person"
        objects.append({
            "id": str(item.get("id", "object-0")),
            "type": object_type,
            "confidence": float(item.get("confidence", 0.0)),
            "bbox": bbox,
            "coordinate": item.get("coordinate", {"x": 0.0, "y": 0.0, "z": 0.0}),
            "info": item.get("info", {}),
        })
    return {
        "version": "4.0",
        "id": str(event.get("eventId", event.get("eventTime", "0"))),
        "timestamp": event["timestamp"],
        "sensorId": str(event["sensorId"]),
        "objects": objects,
        "info": {"source": event.get("source", "rt-cv-local")},
    }


def main() -> None:
    servers = os.getenv("BA_BRIDGE_KAFKA", "localhost:9092")
    source = os.getenv("BA_BRIDGE_SOURCE_TOPIC", "ds-perception")
    target = os.getenv("BA_BRIDGE_TARGET_TOPIC", "mdx-raw")
    group = os.getenv("BA_BRIDGE_GROUP", "behavior-analytics-json-bridge")
    consumer = Consumer({
        "bootstrap.servers": servers,
        "group.id": group,
        "auto.offset.reset": os.getenv("BA_BRIDGE_OFFSET_RESET", "latest"),
        "enable.auto.commit": True,
    })
    producer = Producer({"bootstrap.servers": servers})
    consumer.subscribe([source])
    stopping = False

    def stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    LOG.info("bridging %s -> %s via %s", source, target, servers)
    try:
        while not stopping:
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                LOG.warning("Kafka source error: %s", message.error())
                continue
            try:
                event = json.loads(message.value().decode("utf-8"))
                frame = dict_frame_to_protobuf_frame(_frame(event))
                producer.produce(target, key=str(event.get("sensorId", "")), value=frame.SerializeToString())
                producer.poll(0)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                LOG.warning("discarding malformed RT-CV event: %s", exc)
    finally:
        producer.flush(5)
        consumer.close()


if __name__ == "__main__":
    main()
