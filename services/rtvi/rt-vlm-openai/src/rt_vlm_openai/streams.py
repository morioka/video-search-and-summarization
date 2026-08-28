"""Small RTSP/file stream worker for the OpenAI-compatible RT-VLM."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .alerts import AlertSink
from .assets import Asset
from .models import FileInfo, GenerateCaptionsRequest
from .video import VideoChunk, VideoProcessor

logger = logging.getLogger(__name__)


def _capture_chunk(command: list[str]) -> None:
    """Run FFmpeg outside the API event loop."""
    subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


@dataclass
class StreamEntry:
    stream_id: str
    url: str
    description: str
    sensor_name: str
    task: asyncio.Task[None]


class StreamRegistry:
    """Manage best-effort continuous capture and caption tasks."""

    def __init__(
        self,
        *,
        processor: VideoProcessor,
        backend: object,
        publisher: object,
        semaphore: asyncio.Semaphore,
        chunk_seconds: int,
        frames: int,
        alert_sink: AlertSink | None = None,
    ) -> None:
        self._processor = processor
        self._backend = backend
        self._publisher = publisher
        self._semaphore = semaphore
        self._chunk_seconds = chunk_seconds
        self._frames = frames
        self._alert_sink = alert_sink or AlertSink()
        self._entries: dict[str, StreamEntry] = {}
        self._lock = asyncio.Lock()

    async def add(self, *, stream_id: str | None, url: str, description: str, sensor_name: str) -> str:
        key = stream_id or str(uuid4())
        async with self._lock:
            if key in self._entries:
                raise ValueError(f"stream already exists: {key}")
            task = asyncio.create_task(self._run(key, url, description, sensor_name), name=f"rt-vlm-{key}")
            self._entries[key] = StreamEntry(key, url, description, sensor_name, task)
        return key

    async def remove(self, stream_id: str) -> bool:
        async with self._lock:
            entry = self._entries.pop(stream_id, None)
        if entry is None:
            return False
        entry.task.cancel()
        await asyncio.gather(entry.task, return_exceptions=True)
        return True

    async def list(self) -> list[dict[str, object]]:
        async with self._lock:
            return [
                {
                    "id": e.stream_id,
                    "liveStreamUrl": e.url,
                    "description": e.description,
                    "sensor_name": e.sensor_name,
                    "inference_active": not e.task.done(),
                }
                for e in self._entries.values()
            ]

    async def close(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.task.cancel()
        if entries:
            await asyncio.gather(*(e.task for e in entries), return_exceptions=True)

    async def _run(self, stream_id: str, url: str, description: str, sensor_name: str) -> None:
        offset = 0.0
        chunk_index = 0
        retry_delay = 2.0
        while True:
            try:
                with tempfile.TemporaryDirectory(prefix=f"rt-vlm-{stream_id}-") as directory:
                    path = Path(directory) / "chunk.mp4"
                    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                    if url.startswith("rtsp://"):
                        command += ["-rtsp_transport", "tcp"]
                    # Allow RTSP startup/keyframe latency; very short captures
                    # can exit successfully while producing an empty MP4.
                    capture_seconds = max(self._chunk_seconds, 10) if url.startswith("rtsp://") else self._chunk_seconds
                    if url.startswith("rtsp://"):
                        command += ["-analyzeduration", "10M", "-probesize", "10M"]
                    command += ["-i", url, "-t", str(capture_seconds), "-an"]
                    command += ["-c:v", "libx264" if url.startswith("rtsp://") else "copy", str(path)]
                    process = multiprocessing.Process(target=_capture_chunk, args=(command,), daemon=True)
                    process.start()
                    try:
                        while process.is_alive():
                            await asyncio.sleep(0.1)
                    except asyncio.CancelledError:
                        process.terminate()
                        process.join(timeout=5)
                        raise
                    process.join()
                    if process.exitcode != 0 or not path.exists() or path.stat().st_size == 0:
                        raise RuntimeError(f"FFmpeg capture failed with exit code {process.exitcode}")
                    metadata = await self._processor.probe(path)
                    duration = metadata.duration
                    info = FileInfo(
                        id=uuid4(),
                        bytes=path.stat().st_size,
                        filename=f"{stream_id}-{chunk_index}.mp4",
                        sensor_name=sensor_name,
                        media_type="video",
                    )
                    asset = Asset(info=info, path=path)
                    request = GenerateCaptionsRequest(
                        id=info.id,
                        prompt=description or "Describe events and safety hazards.",
                        num_frames_per_second_or_fixed_frames_chunk=self._frames,
                    )
                    async with self._semaphore:
                        from .app import _process_chunk

                        response = await _process_chunk(
                            asset=asset,
                            chunk=VideoChunk(chunk_index, 0.0, duration),
                            request=request,
                            settings=_settings_for_worker(self._frames),
                            video_processor=self._processor,
                            backend=self._backend,
                            query_id=uuid4(),
                        )
                    chunk = response["chunk_responses"][0]
                    chunk["start_time"] = f"{offset:.3f}".rstrip("0").rstrip(".")
                    chunk["end_time"] = f"{offset + duration:.3f}".rstrip("0").rstrip(".")
                    self._publisher.publish(
                        stream_id=stream_id, chunk=chunk, model=response["model"], request_id=response["id"]
                    )
                    try:
                        await self._alert_sink.emit_if_match(
                            stream_id=stream_id,
                            content=str(chunk.get("content", "")),
                            start=str(chunk.get("start_time", "")),
                            end=str(chunk.get("end_time", "")),
                        )
                    except Exception:
                        logger.exception("alert bridge request failed for %s", stream_id)
                    offset += duration
                    chunk_index += 1
                    retry_delay = 2.0
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("stream worker failed for %s; retrying in %.1fs", stream_id, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2.0, 30.0)


def _settings_for_worker(frames: int):
    """Minimal settings object consumed by the shared frame-count helper."""
    return type("WorkerSettings", (), {"default_frames_per_chunk": frames, "max_frames_per_chunk": frames})()
