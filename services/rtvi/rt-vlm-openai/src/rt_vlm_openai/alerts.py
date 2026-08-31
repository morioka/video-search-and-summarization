"""Optional keyword-gated bridge to the Alert HTTP contract."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


class AlertSink:
    def __init__(
        self, endpoint: str = "", keywords: str = "", cooldown_seconds: float = 30.0, video_path: str = ""
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.keywords = tuple(k.strip().lower() for k in keywords.split(",") if k.strip())
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.video_path = video_path
        self._last_emit: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint and self.keywords)

    async def emit_if_match(self, *, stream_id: str, content: str, start: str, end: str) -> bool:
        if not self.enabled or not any(k in content.lower() for k in self.keywords):
            return False
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_emit.get(stream_id, 0.0) < self.cooldown_seconds:
            return False
        self._last_emit[stream_id] = now_monotonic
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "id": f"rt-vlm-{uuid4()}",
            "timestamp": now,
            "end": now,
            "sensorId": stream_id,
            "category": "VLM_DETECTED",
            "isAnomaly": True,
            "objectIds": ["999"],
            "info": {"vlm_description": content, "start_time": start, "end_time": end},
        }
        if self.video_path:
            payload["videoPath"] = self.video_path
            payload["info"]["video_path"] = self.video_path
        # Keep this opt-in bridge synchronous; the Alert endpoint is a short
        # local request and asyncio thread execution is unreliable under WSL.
        self._post(payload)
        return True

    def _post(self, payload: dict) -> None:
        request = urllib.request.Request(
            f"{self.endpoint}/api/v1/incidents",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"Alert service returned HTTP {response.status}")
