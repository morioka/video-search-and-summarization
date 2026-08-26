# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import subprocess
from pathlib import Path

from rt_vlm_openai.video import VideoChunk, VideoProcessor, chunk_ranges


def test_chunk_ranges_with_overlap() -> None:
    chunks = chunk_ranges(0, 25, chunk_duration=10, overlap=2)

    assert [(chunk.index, chunk.start, chunk.end) for chunk in chunks] == [
        (0, 0, 10),
        (1, 8, 18),
        (2, 16, 25),
    ]


def test_chunk_ranges_uses_one_chunk_when_duration_is_zero() -> None:
    chunks = chunk_ranges(5, 12, chunk_duration=0, overlap=0)

    assert [(chunk.start, chunk.end) for chunk in chunks] == [(5, 12)]


def test_ffmpeg_probe_and_frame_extraction(tmp_path: Path) -> None:
    video = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=5:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    processor = VideoProcessor()

    metadata = processor.probe_sync(video)
    frames = processor.extract_frames_sync(
        video,
        VideoChunk(index=0, start=0, end=metadata.duration),
        frame_count=3,
        width=80,
        height=45,
    )

    assert metadata.width == 160
    assert metadata.height == 90
    assert 1.9 <= metadata.duration <= 2.1
    assert len(frames.images) == 3
    assert all(base64.b64decode(image).startswith(b"\xff\xd8") for image in frames.images)
