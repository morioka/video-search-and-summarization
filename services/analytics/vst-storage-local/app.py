from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import os
import json
import subprocess

from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import FileResponse

ROOT = Path(os.getenv("VST_VIDEO_ROOT", "/data/videos"))
ROOT.mkdir(parents=True, exist_ok=True)
INDEX = ROOT / ".vst-index.json"
app = FastAPI(title="Local VST Storage Compatibility API")
try:
    records: dict[str, dict] = json.loads(INDEX.read_text()) if INDEX.exists() else {}
except (OSError, ValueError):
    records = {}

def _persist() -> None:
    INDEX.write_text(json.dumps(records, ensure_ascii=True, indent=2))

def _duration(path: Path) -> float:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=15, check=True)
        return max(0.0, float(result.stdout.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1.0

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/vst/api/v1/storage/file")
async def upload(mediaFile: UploadFile = File(...), filename: str = Form("video.mp4"), nvstreamer_identifier: str | None = Header(None)):
    sensor_id = str(uuid4())
    safe = Path(filename).name or "video.mp4"
    path = ROOT / f"{sensor_id}_{safe}"
    path.write_bytes(await mediaFile.read())
    start = datetime.now(timezone.utc).replace(microsecond=0)
    end = start + timedelta(seconds=1)
    duration = _duration(path)
    records[sensor_id] = {"path": str(path), "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"), "filename": safe, "duration": duration}
    _persist()
    return {"bytes": path.stat().st_size, "filename": safe, "filePath": str(path), "sensorId": sensor_id, "streamId": sensor_id}

@app.get("/vst/api/v1/storage/timelines")
def timelines():
    return {key: [{"startTime": value["start"], "endTime": value["end"]}] for key, value in records.items()}

@app.get("/vst/api/v1/sensor/streams")
def streams():
    return [{key: [{"name": value["filename"], "sensorId": key, "streamId": key, "media_type": "video", "duration": value.get("duration", 1.0)}]} for key, value in records.items()]

@app.get("/vst/api/v1/storage/file/{sensor_id}/url")
def file_url(sensor_id: str):
    value = records[sensor_id]
    return {"videoUrl": f"http://127.0.0.1:31001/vst/api/v1/storage/file/{sensor_id}/download"}

@app.get("/vst/api/v1/storage/file/{sensor_id}/download")
def download(sensor_id: str):
    return FileResponse(records[sensor_id]["path"], media_type="video/mp4")
