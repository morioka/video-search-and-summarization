# SPDX-License-Identifier: Apache-2.0
"""Optional Kafka publisher for the VSS VisionLLM protobuf contract."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .protos import nv_pb2

logger = logging.getLogger(__name__)


class VisionLLMKafkaPublisher:
    """Publish one protobuf message per caption chunk when configured."""

    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self.topic = topic
        self._producer = None
        if bootstrap_servers:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","), max_block_ms=5000
            )

    @property
    def enabled(self) -> bool:
        return self._producer is not None

    def publish(self, *, stream_id: str, chunk: dict, model: str, request_id: str) -> None:
        if self._producer is None:
            return
        message = nv_pb2.VisionLLM(version="openai-compatible")
        message.timestamp.FromDatetime(datetime.now(timezone.utc))
        message.sensor.id = stream_id
        message.sensor.type = "video"
        message.llm.info["model"] = model
        query = message.llm.queries.add(
            id=f"{request_id}:{chunk['chunk_id']}",
            response=str(chunk.get("content", "")),
        )
        query.params["start_time"] = str(chunk.get("start_time", ""))
        query.params["end_time"] = str(chunk.get("end_time", ""))
        message.info.update(
            streamId=stream_id,
            sensorId=stream_id,
            chunkIdx=str(chunk.get("chunk_id", 0)),
            frameCount=str(chunk.get("frame_count", 0)),
        )
        try:
            self._producer.send(
                self.topic,
                key=f"{request_id}:{chunk['chunk_id']}".encode(),
                value=message.SerializeToString(),
                headers=[("message_type", b"vision_llm")],
            )
        except Exception as exc:  # Kafka is an optional downstream sink.
            logger.warning("Kafka caption publish failed; continuing without Kafka: %s", exc)

    def close(self) -> None:
        if self._producer is not None:
            self._producer.flush(timeout=5)
            self._producer.close()
