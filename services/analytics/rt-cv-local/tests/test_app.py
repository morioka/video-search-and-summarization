import asyncio
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ["RTCV_EVENT_INTERVAL_SEC"] = "0.01"
import app as module


def test_health_and_rules():
    with TestClient(module.app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/v1/rules").json()["rules"]


def test_stream_add_rejects_missing_file(tmp_path):
    with TestClient(module.app) as client:
        response = client.post("/api/v1/stream/add", json={"value": {"camera_id": "x", "camera_url": str(tmp_path / "missing.mp4")}})
        assert response.status_code == 404
