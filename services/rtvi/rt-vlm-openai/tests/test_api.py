# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import json
from pathlib import Path

import httpx
from openai import APIConnectionError

from rt_vlm_openai.app import create_app
import rt_vlm_openai.app as app_module
from rt_vlm_openai.config import Settings
from rt_vlm_openai.models import GenerateCaptionsRequest, OpenAIResult
from rt_vlm_openai.streams import StreamRegistry
from rt_vlm_openai.video import ExtractedFrames, VideoMetadata, VideoProcessor


class FakeBackend:
    model = "openai-test-vlm"

    def __init__(self) -> None:
        self.calls: list[tuple[GenerateCaptionsRequest, list[str], float, float]] = []

    async def caption(self, request, images, *, start, end):
        self.calls.append((request, list(images), start, end))
        return OpenAIResult(content=f"caption {start:.0f}-{end:.0f}", input_tokens=10, output_tokens=4), 12.5


class FailingBackend:
    model = "openai-test-vlm"

    async def caption(self, request, images, *, start, end):
        raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"))


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def publish(self, **message) -> None:
        self.messages.append(message)


class FakeVideoProcessor:
    async def probe(self, path: Path) -> VideoMetadata:
        assert path.exists()
        return VideoMetadata(duration=25, width=1280, height=720)

    async def extract_frames(self, path, chunk, frame_count, width, height):
        assert path.exists()
        images = [base64.b64encode(f"frame-{index}".encode()).decode() for index in range(frame_count)]
        return ExtractedFrames(images=images, latency_ms=5.0)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        model="openai-test-vlm",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        asset_dir=tmp_path / "assets",
        max_upload_bytes=1024 * 1024,
        default_chunk_duration=10,
        default_frames_per_chunk=2,
        max_frames_per_chunk=4,
        max_tokens=100,
        request_timeout_seconds=10,
        max_concurrent_requests=2,
    )


async def test_file_crud_and_streaming_caption_contract(tmp_path: Path) -> None:
    backend = FakeBackend()
    app = create_app(settings(tmp_path), backend=backend, video_processor=FakeVideoProcessor())

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        upload = await client.post(
            "/v1/files",
            files={"file": ("sample.mp4", b"not-a-real-video", "video/mp4")},
            data={"purpose": "vision", "media_type": "video", "sensor_name": "camera-1"},
        )
        assert upload.status_code == 200
        file_info = upload.json()
        file_id = file_info["id"]
        assert file_info["filename"] == "sample.mp4"
        assert file_info["sensor_name"] == "camera-1"

        listing = await client.get("/v1/files")
        assert listing.json()["data"] == [file_info]

        async with client.stream(
            "POST",
            "/v1/generate_captions",
            json={
                "id": file_id,
                "model": "openai-test-vlm",
                "prompt": "Describe the activity",
                "stream": True,
                "chunk_duration": 10,
                "chunk_overlap_duration": 2,
                "stream_options": {"include_usage": True},
                "num_frames_per_second_or_fixed_frames_chunk": 2,
            },
        ) as response:
            assert response.status_code == 200
            data_lines = [
                line.removeprefix("data: ") async for line in response.aiter_lines() if line.startswith("data: ")
            ]

        assert data_lines[-1] == "[DONE]"
        payloads = [json.loads(line) for line in data_lines[:-1]]
        chunks = [payload for payload in payloads if payload["chunk_responses"]]
        assert [payload["chunk_responses"][0]["content"] for payload in chunks] == [
            "caption 0-10",
            "caption 8-18",
            "caption 16-25",
        ]
        assert payloads[-1]["usage"] == {"total_chunks_processed": 3}
        assert [len(call[1]) for call in backend.calls] == [2, 2, 2]

        deleted = await client.delete(f"/v1/files/{file_id}")
        assert deleted.json() == {"id": file_id, "object": "file", "deleted": True}
        assert (await client.get("/v1/files")).json()["data"] == []


async def test_rejects_audio_and_invalid_overlap(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), backend=FakeBackend(), video_processor=FakeVideoProcessor())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        request = {"id": "00000000-0000-0000-0000-000000000000", "model": "m", "prompt": "p"}
        audio = await client.post("/v1/generate_captions", json=request | {"enable_audio": True})
        overlap = await client.post(
            "/v1/generate_captions",
            json=request | {"chunk_duration": 10, "chunk_overlap_duration": 10},
        )

    assert audio.status_code == 422
    assert overlap.status_code == 422


async def test_nonstream_openai_failure_returns_json_502(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), backend=FailingBackend(), video_processor=FakeVideoProcessor())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        upload = await client.post(
            "/v1/files",
            files={"file": ("sample.mp4", b"not-a-real-video", "video/mp4")},
        )
        response = await client.post(
            "/v1/generate_captions",
            json={"id": upload.json()["id"], "prompt": "Describe the activity", "stream": False},
        )

    assert response.status_code == 502
    assert response.json()["detail"].startswith("OpenAI-compatible VLM request failed:")


async def test_chat_completions_video_url_bridge(tmp_path: Path, monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _size):
            return b"video-bytes" if not getattr(self, "done", False) else b""

    def fake_urlopen(*_args, **_kwargs):
        response = FakeResponse()
        response.done = False
        original_read = response.read

        def read_once(size):
            if response.done:
                return b""
            response.done = True
            return original_read(size)

        response.read = read_once
        return response

    monkeypatch.setattr(app_module, "urlopen", fake_urlopen)
    backend = FakeBackend()
    app = create_app(settings(tmp_path), backend=backend, video_processor=FakeVideoProcessor())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "openai-test-vlm",
                "messages": [{"role": "user", "content": [
                    {"type": "video_url", "video_url": {"url": "http://example.test/video.mp4"}},
                    {"type": "text", "text": "Describe the activity"},
                ]}],
            },
        )
        missing = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "text"}]})

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "caption 0-25"
    assert missing.status_code == 422


async def test_stream_lifecycle_contract(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), backend=FakeBackend(), video_processor=FakeVideoProcessor())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        added = await client.post(
            "/v1/streams/add",
            json={"streams": [{"id": "camera-1", "liveStreamUrl": "file:///missing.mp4", "description": "test"}]},
        )
        assert added.status_code == 200
        assert added.json()["results"][0]["id"] == "camera-1"
        listed = await client.get("/v1/streams/get-stream-info")
        assert listed.json()["results"][0]["id"] == "camera-1"
        removed = await client.delete("/v1/streams/delete/camera-1")
        assert removed.json() == {"id": "camera-1", "deleted": True}


async def test_nvidia_stream_alias_contract(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), backend=FakeBackend(), video_processor=FakeVideoProcessor())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        added = await client.post(
            "/v1/stream/add",
            json={"key": "sensor", "value": {"camera_id": "camera-compat", "camera_url": "file:///missing.mp4"}},
        )
        assert added.status_code == 200
        assert added.json()["asset_id"] == "camera-compat"
        listed = await client.get("/v1/stream/get-stream-info")
        assert listed.json()["stream_count"] == 1
        stopped = await client.delete("/v1/generate_captions/camera-compat")
        assert stopped.json() == {"id": "camera-compat", "stopped": True}


async def test_file_stream_worker_processes_real_chunk(tmp_path: Path) -> None:
    source = Path("/home/morioka/temp/Video-to-SOP-Generator/Videos/konro_inspection.mp4")
    if not source.exists():
        return
    publisher = FakePublisher()
    registry = StreamRegistry(
        processor=VideoProcessor(),
        backend=FakeBackend(),
        publisher=publisher,
        semaphore=asyncio.Semaphore(1),
        chunk_seconds=1,
        frames=2,
    )
    stream_id = await registry.add(stream_id="local-file", url=source.as_uri(), description="test", sensor_name="cam")
    for _ in range(20):
        if publisher.messages:
            break
        await asyncio.sleep(0.5)
    await registry.remove(stream_id)
    assert publisher.messages
    assert publisher.messages[0]["stream_id"] == "local-file"


async def test_failed_stream_stays_registered_during_backoff(tmp_path: Path) -> None:
    registry = StreamRegistry(
        processor=VideoProcessor(),
        backend=FakeBackend(),
        publisher=FakePublisher(),
        semaphore=asyncio.Semaphore(1),
        chunk_seconds=1,
        frames=2,
    )
    stream_id = await registry.add(
        stream_id="disconnected-camera",
        url="file:///does-not-exist.mp4",
        description="test",
        sensor_name="cam",
    )
    await asyncio.sleep(0.3)
    listed = await registry.list()
    assert listed[0]["id"] == stream_id
    assert listed[0]["inference_active"] is True
    await registry.remove(stream_id)
    assert await registry.list() == []
