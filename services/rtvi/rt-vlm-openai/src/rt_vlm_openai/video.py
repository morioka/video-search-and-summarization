# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FFmpeg-based metadata probing and deterministic frame selection."""

import base64
import json
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoMetadata:
    duration: float
    width: int
    height: int


@dataclass(frozen=True)
class VideoChunk:
    index: int
    start: float
    end: float


@dataclass(frozen=True)
class ExtractedFrames:
    images: list[str]
    latency_ms: float


def chunk_ranges(start: float, end: float, chunk_duration: int, overlap: int) -> list[VideoChunk]:
    if end <= start:
        raise ValueError("Video range is empty")
    if chunk_duration <= 0 or chunk_duration >= end - start:
        return [VideoChunk(index=0, start=start, end=end)]
    if overlap >= chunk_duration:
        raise ValueError("overlap must be less than chunk duration")

    chunks: list[VideoChunk] = []
    cursor = start
    step = chunk_duration - max(0, overlap)
    while cursor < end:
        chunk_end = min(cursor + chunk_duration, end)
        chunks.append(VideoChunk(index=len(chunks), start=cursor, end=chunk_end))
        if chunk_end >= end:
            break
        cursor += step
    return chunks


class VideoProcessor:
    async def probe(self, path: Path) -> VideoMetadata:
        # Direct execution is intentional: WSL/uv environments can leave
        # subprocesses launched from a worker thread unreaped indefinitely.
        return self.probe_sync(path)

    @staticmethod
    def probe_sync(path: Path) -> VideoMetadata:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        if not streams:
            raise ValueError("No video stream found")
        duration = float(payload.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Video duration is unavailable")
        return VideoMetadata(duration=duration, width=int(streams[0]["width"]), height=int(streams[0]["height"]))

    async def extract_frames(
        self,
        path: Path,
        chunk: VideoChunk,
        frame_count: int,
        width: int | None,
        height: int | None,
    ) -> ExtractedFrames:
        return self.extract_frames_sync(path, chunk, frame_count, width, height)

    @classmethod
    def extract_frames_sync(
        cls,
        path: Path,
        chunk: VideoChunk,
        frame_count: int,
        width: int | None,
        height: int | None,
    ) -> ExtractedFrames:
        started = time.perf_counter()
        duration = chunk.end - chunk.start
        count = max(1, frame_count)
        timestamps = [chunk.start + duration * (index + 0.5) / count for index in range(count)]
        images = [cls._extract_one(path, timestamp, width, height) for timestamp in timestamps]
        return ExtractedFrames(images=images, latency_ms=(time.perf_counter() - started) * 1000)

    @staticmethod
    def _extract_one(path: Path, timestamp: float, width: int | None, height: int | None) -> str:
        command = ["ffmpeg", "-v", "error", "-ss", f"{timestamp:.6f}", "-i", str(path), "-frames:v", "1"]
        if width and height:
            command.extend(["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease"])
        command.extend(["-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"])
        result = subprocess.run(command, check=True, capture_output=True, timeout=30)
        if not result.stdout:
            raise ValueError(f"Could not extract a frame at {timestamp:.3f}s")
        return base64.b64encode(result.stdout).decode("ascii")
