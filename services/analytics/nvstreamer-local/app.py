"""License-free NVStreamer-compatible video catalog and RTSP publisher."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(os.getenv("NVSTREAMER_VIDEO_ROOT", "/data/videos"))
DB_PATH = Path(os.getenv("NVSTREAMER_DB", str(ROOT / "nvstreamer.db")))
RTSP_HOST = os.getenv("NVSTREAMER_RTSP_HOST", "127.0.0.1")
RTSP_PORT = int(os.getenv("NVSTREAMER_RTSP_PORT", "8554"))
MAX_UPLOAD = int(os.getenv("NVSTREAMER_MAX_UPLOAD_BYTES", str(10_000 * 1024 * 1024)))
ALLOWED = {".mp4", ".mkv"}
processes: dict[str, asyncio.subprocess.Process] = {}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, duration_sec REAL NOT NULL DEFAULT 0,
                codec TEXT, fps REAL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS streams (
                id TEXT PRIMARY KEY, media_id TEXT NOT NULL UNIQUE,
                stream_name TEXT NOT NULL UNIQUE, rtsp_url TEXT NOT NULL,
                status TEXT NOT NULL, pid INTEGER, loop_playback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(media_id) REFERENCES media(id) ON DELETE CASCADE
            );"""
        )


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) if row else {}


def metadata(path: Path) -> tuple[float, str | None, float | None]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "format=duration:stream=codec_name,r_frame_rate", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        payload = json.loads(result.stdout)
        duration = float(payload.get("format", {}).get("duration") or 0)
        stream = (payload.get("streams") or [{}])[0]
        rate = stream.get("r_frame_rate", "0/1")
        num, den = (rate.split("/", 1) + ["1"])[:2]
        fps = float(num) / float(den or 1)
        return duration, stream.get("codec_name"), fps
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return 0.0, None, None


def media_payload(row: sqlite3.Row) -> dict[str, Any]:
    return row_dict(row)


def stream_payload(row: sqlite3.Row) -> dict[str, Any]:
    data = row_dict(row)
    data["loop_playback"] = bool(data.get("loop_playback"))
    data["running"] = data.get("status") == "running"
    return data


async def publish(stream_id: str, path: Path, stream_name: str, loop: bool) -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re"]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(path), "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp",
             f"rtsp://127.0.0.1:{RTSP_PORT}/{stream_name}"]
    proc = await asyncio.create_subprocess_exec(*args)
    processes[stream_id] = proc
    with db() as conn:
        conn.execute("UPDATE streams SET status='running', pid=? WHERE id=?", (proc.pid, stream_id))
    code = await proc.wait()
    processes.pop(stream_id, None)
    with db() as conn:
        conn.execute("UPDATE streams SET status=? , pid=NULL WHERE id=?", ("stopped" if code == 0 else "error", stream_id))


async def stop_stream(stream_id: str) -> None:
    proc = processes.pop(stream_id, None)
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
    with db() as conn:
        conn.execute("UPDATE streams SET status='stopped', pid=NULL WHERE id=?", (stream_id,))


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    await asyncio.gather(*(stop_stream(stream_id) for stream_id in list(processes)))


app = FastAPI(title="Local NVStreamer Compatibility API", lifespan=lifespan)


class StartRequest(BaseModel):
    loop_playback: bool = False


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "nvstreamer-local", "streams": len(processes)}


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/v1/media")
def list_media() -> list[dict[str, Any]]:
    with db() as conn:
        return [media_payload(row) for row in conn.execute("SELECT * FROM media ORDER BY created_at DESC")]


@app.get("/api/v1/media/{media_id}/file")
def media_file(media_id: str) -> FileResponse:
    with db() as conn:
        row = conn.execute("SELECT path, filename FROM media WHERE id=?", (media_id,)).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(404, "media not found")
    media_type = "video/x-matroska" if row["filename"].lower().endswith(".mkv") else "video/mp4"
    return FileResponse(row["path"], media_type=media_type, filename=row["filename"])


@app.post("/api/v1/media", status_code=201)
async def upload_media(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "video.mp4").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(415, "only MP4 and MKV files are supported")
    media_id = str(uuid4())
    filename = Path(file.filename or "video.mp4").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "video.mp4"
    path = ROOT / f"{media_id}_{safe_name}"
    size = 0
    with path.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                path.unlink(missing_ok=True)
                raise HTTPException(413, "upload exceeds configured limit")
            target.write(chunk)
    duration, codec, fps = metadata(path)
    now = datetime.now(timezone.utc).isoformat()
    stream_id = str(uuid4())
    stream_name = Path(safe_name).stem + "-" + media_id[:8]
    rtsp_url = f"rtsp://{RTSP_HOST}:{RTSP_PORT}/{stream_name}"
    with db() as conn:
        conn.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?,?)", (media_id, safe_name, str(path), size, duration, codec, fps, now))
        conn.execute("INSERT INTO streams VALUES (?,?,?,?,?,?,?,?)", (stream_id, media_id, stream_name, rtsp_url, "created", None, 0, now))
    await start_stream(stream_id, False)
    with db() as conn:
        return {"media": media_payload(conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()),
                "stream": stream_payload(conn.execute("SELECT * FROM streams WHERE id=?", (stream_id,)).fetchone())}


@app.get("/api/v1/streams")
def list_streams() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT s.*, m.filename, m.duration_sec, m.codec, m.fps FROM streams s JOIN media m ON m.id=s.media_id ORDER BY s.created_at DESC")
        return [stream_payload(row) for row in rows]


@app.post("/api/v1/streams/{stream_id}/start")
async def start_stream_endpoint(stream_id: str, request: StartRequest | None = None) -> dict[str, Any]:
    await start_stream(stream_id, request.loop_playback if request else False)
    with db() as conn:
        row = conn.execute("SELECT s.*, m.filename, m.duration_sec, m.codec, m.fps FROM streams s JOIN media m ON m.id=s.media_id WHERE s.id=?", (stream_id,)).fetchone()
        if not row:
            raise HTTPException(404, "stream not found")
        return stream_payload(row)


async def start_stream(stream_id: str, loop: bool) -> None:
    with db() as conn:
        row = conn.execute("SELECT s.*, m.path FROM streams s JOIN media m ON m.id=s.media_id WHERE s.id=?", (stream_id,)).fetchone()
        if not row:
            raise HTTPException(404, "stream not found")
        if stream_id in processes:
            return
        conn.execute("UPDATE streams SET status='starting', loop_playback=? WHERE id=?", (int(loop), stream_id))
    asyncio.create_task(publish(stream_id, Path(row["path"]), row["stream_name"], loop))


@app.post("/api/v1/streams/{stream_id}/stop")
async def stop_stream_endpoint(stream_id: str) -> dict[str, Any]:
    await stop_stream(stream_id)
    with db() as conn:
        row = conn.execute("SELECT * FROM streams WHERE id=?", (stream_id,)).fetchone()
        if not row:
            raise HTTPException(404, "stream not found")
        return stream_payload(row)


@app.delete("/api/v1/media/{media_id}")
async def delete_media(media_id: str) -> dict[str, bool]:
    with db() as conn:
        rows = conn.execute("SELECT streams.id AS stream_id FROM streams JOIN media ON media.id=streams.media_id WHERE media.id=?", (media_id,)).fetchall()
        media = conn.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
        if not media:
            raise HTTPException(404, "media not found")
    for row in rows:
        await stop_stream(row["stream_id"])
    Path(media["path"]).unlink(missing_ok=True)
    with db() as conn:
        conn.execute("DELETE FROM streams WHERE media_id=?", (media_id,))
        conn.execute("DELETE FROM media WHERE id=?", (media_id,))
    return {"deleted": True}


def vios_streams() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT s.*, m.filename, m.duration_sec FROM streams s JOIN media m ON m.id=s.media_id ORDER BY s.created_at DESC").fetchall()
    return [{"sensorId": row["stream_name"], "streamId": row["id"], "name": row["filename"],
             "sensorName": row["filename"], "rtspUrl": row["rtsp_url"], "url": row["rtsp_url"],
             "media_type": "rtsp", "duration": row["duration_sec"]} for row in rows]


@app.get("/vst/api/v1/live/streams")
@app.get("/vst/api/v1/sensor/streams")
@app.get("/vst/api/v1/sensor/list")
def sensor_streams() -> list[dict[str, Any]]:
    return vios_streams()


@app.get("/vst/api/v1/storage/timelines")
def timelines() -> dict[str, list[dict[str, Any]]]:
    with db() as conn:
        rows = conn.execute("SELECT s.id, m.duration_sec, m.created_at FROM streams s JOIN media m ON m.id=s.media_id").fetchall()
    return {row["id"]: [{"startTime": row["created_at"], "endTime": row["created_at"]}] for row in rows}
