# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application implementing the file-captioning RT-VLM contract."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openai import OpenAIError
from starlette.requests import Request
from starlette.responses import Response

from .alerts import AlertSink
from .assets import Asset, AssetStore
from .config import Settings
from .kafka_publisher import VisionLLMKafkaPublisher
from .models import (
    AddStreamsRequest,
    DeleteFileResponse,
    FileInfo,
    GenerateCaptionsRequest,
    JsonDict,
    ListFilesResponse,
    OpenAIResult,
)
from .openai_backend import OpenAIBackend
from .streams import StreamRegistry
from .video import VideoChunk, VideoMetadata, VideoProcessor, chunk_ranges

logger = logging.getLogger(__name__)


class CaptionBackend(Protocol):
    model: str

    async def caption(
        self,
        request: GenerateCaptionsRequest,
        images: list[str],
        *,
        start: float,
        end: float,
    ) -> tuple[OpenAIResult, float]: ...


def _time_string(seconds: float, creation_time: str | None) -> str:
    if not creation_time:
        return f"{seconds:.3f}".rstrip("0").rstrip(".")
    base = datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
    value = base + timedelta(seconds=seconds)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _frame_count(request: GenerateCaptionsRequest, chunk: VideoChunk, settings: Settings) -> int:
    requested = request.num_frames_per_second_or_fixed_frames_chunk
    if requested is None or requested == 0:
        count = settings.default_frames_per_chunk
    elif requested == -1:
        count = settings.max_frames_per_chunk
    elif request.use_fps_for_chunking:
        count = max(1, round((chunk.end - chunk.start) * requested))
    else:
        count = round(requested)
    return min(max(1, count), settings.max_frames_per_chunk)


def _media_info(asset: Asset, start: float, end: float) -> JsonDict:
    if asset.info.creation_time:
        return {
            "type": "timestamp",
            "start_timestamp": _time_string(start, asset.info.creation_time),
            "end_timestamp": _time_string(end, asset.info.creation_time),
        }
    return {"type": "offset", "start_offset": start, "end_offset": end}


async def _process_chunk(
    *,
    asset: Asset,
    chunk: VideoChunk,
    request: GenerateCaptionsRequest,
    settings: Settings,
    video_processor: VideoProcessor,
    backend: CaptionBackend,
    query_id: UUID,
) -> JsonDict:
    chunk_started = time.perf_counter()
    extracted = await video_processor.extract_frames(
        asset.path,
        chunk,
        _frame_count(request, chunk, settings),
        request.vlm_input_width,
        request.vlm_input_height,
    )
    result, vlm_latency_ms = await backend.caption(
        request,
        extracted.images,
        start=chunk.start,
        end=chunk.end,
    )
    chunk_latency_ms = (time.perf_counter() - chunk_started) * 1000
    chunk_response = {
        "chunk_id": chunk.index,
        "start_time": _time_string(chunk.start, asset.info.creation_time),
        "end_time": _time_string(chunk.end, asset.info.creation_time),
        "content": result.content,
        "embeddings": [],
        "reasoning_description": "",
        "decode_latency_ms": extracted.latency_ms,
        "vlm_latency_ms": vlm_latency_ms,
        "chunk_latency_ms": chunk_latency_ms,
        "processing_latency_s": chunk_latency_ms / 1000,
        "frame_count": len(extracted.images),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    return {
        "id": str(query_id),
        "created": int(time.time()),
        "model": request.model or backend.model,
        "media_info": _media_info(asset, chunk.start, chunk.end),
        "usage": None,
        "chunk_responses": [chunk_response],
    }


def create_app(
    settings: Settings | None = None,
    *,
    backend: CaptionBackend | None = None,
    video_processor: VideoProcessor | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    store = AssetStore(runtime_settings.asset_dir, runtime_settings.max_upload_bytes)
    processor = video_processor or VideoProcessor()
    request_semaphore = asyncio.Semaphore(runtime_settings.max_concurrent_requests)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime_settings.validate()
        await store.initialize()
        publisher = VisionLLMKafkaPublisher(runtime_settings.kafka_bootstrap_servers, runtime_settings.kafka_topic)
        app.state.kafka_publisher = publisher
        app.state.backend = backend or OpenAIBackend(
            api_key=runtime_settings.api_key,
            model=runtime_settings.model,
            base_url=runtime_settings.base_url,
            timeout=runtime_settings.request_timeout_seconds,
            max_tokens=runtime_settings.max_tokens,
            max_retries=runtime_settings.max_retries,
        )
        app.state.streams = StreamRegistry(
            processor=processor,
            backend=app.state.backend,
            publisher=publisher,
            semaphore=request_semaphore,
            chunk_seconds=runtime_settings.default_chunk_duration,
            frames=runtime_settings.default_frames_per_chunk,
            alert_sink=AlertSink(runtime_settings.alert_endpoint, runtime_settings.alert_keywords),
        )
        try:
            yield
        finally:
            await app.state.streams.close()
            publisher.close()

    app = FastAPI(title="VSS RT-VLM OpenAI Compatibility Service", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.video_processor = processor

    @app.middleware("http")
    async def request_observability(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%s latency_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.get("/v1/health/ready")
    async def ready() -> JsonDict:
        return {"status": "ready"}

    @app.get("/v1/health/live")
    async def live() -> JsonDict:
        return {"status": "live"}

    @app.post("/v1/streams/add")
    async def add_streams(request: AddStreamsRequest) -> dict[str, Any]:
        results = []
        errors = []
        for stream in request.streams:
            try:
                stream_id = await app.state.streams.add(
                    stream_id=stream.id,
                    url=stream.liveStreamUrl,
                    description=stream.description,
                    sensor_name=stream.sensor_name,
                )
                results.append({"id": stream_id, "liveStreamUrl": stream.liveStreamUrl})
            except ValueError as exc:
                errors.append({"id": stream.id, "error": str(exc)})
        return {"results": results, "errors": errors}

    @app.get("/v1/streams/get-stream-info")
    async def get_stream_info() -> dict[str, Any]:
        return {"results": await app.state.streams.list()}

    @app.get("/v1/stream/get-stream-info")
    async def get_stream_info_compat() -> dict[str, Any]:
        streams = await app.state.streams.list()
        return {"streams": streams, "stream_count": len(streams)}

    @app.delete("/v1/streams/delete/{stream_id}")
    async def delete_stream(stream_id: str) -> dict[str, Any]:
        if not await app.state.streams.remove(stream_id):
            raise HTTPException(status_code=404, detail=f"No such stream {stream_id}")
        return {"id": stream_id, "deleted": True}

    @app.delete("/v1/streams/delete-batch")
    async def delete_stream_batch(payload: Any = Body(...)) -> dict[str, Any]:
        stream_ids = payload if isinstance(payload, list) else payload.get("stream_ids", payload.get("ids", []))
        deleted = [str(stream_id) for stream_id in stream_ids if await app.state.streams.remove(str(stream_id))]
        return {"deleted": deleted, "count": len(deleted)}

    @app.delete("/v1/generate_captions/{stream_id}")
    async def stop_stream_captions(stream_id: str) -> dict[str, Any]:
        """Compatibility stop endpoint used by the NVIDIA client."""
        if not await app.state.streams.remove(stream_id):
            raise HTTPException(status_code=404, detail=f"No such stream {stream_id}")
        return {"id": stream_id, "stopped": True}

    @app.post("/v1/stream/add")
    async def add_stream_compat(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        value = payload.get("value", payload)
        url = value.get("camera_url") or value.get("liveStreamUrl")
        camera_id = value.get("camera_id") or value.get("id")
        if not url or not camera_id:
            raise HTTPException(status_code=422, detail="value.camera_url and value.camera_id are required")
        stream_id = await app.state.streams.add(
            stream_id=str(camera_id),
            url=str(url),
            description=str(value.get("metadata", {}).get("prompt") or value.get("description") or ""),
            sensor_name=str(value.get("sensor_name") or camera_id),
        )
        return {"camera_id": str(camera_id), "asset_id": stream_id, "status": "processing", "inference": True}

    @app.post("/v1/stream/remove")
    async def remove_stream_compat(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        value = payload.get("value", payload)
        stream_id = value.get("camera_id") or value.get("id")
        if not stream_id:
            raise HTTPException(status_code=422, detail="value.camera_id is required")
        if not await app.state.streams.remove(str(stream_id)):
            raise HTTPException(status_code=404, detail=f"No such stream {stream_id}")
        return {"camera_id": str(stream_id), "asset_id": str(stream_id), "status": "removed"}

    @app.get("/v1/models")
    async def models() -> JsonDict:
        return {
            "object": "list",
            "data": [
                {
                    "id": runtime_settings.model,
                    "object": "model",
                    "created": 0,
                    "owned_by": runtime_settings.base_url or "openai",
                    "api_type": "openai",
                }
            ],
        }

    @app.post("/v1/files", response_model=FileInfo)
    async def upload_file(
        file: UploadFile | None = File(default=None),
        purpose: str = Form(default="vision"),
        media_type: str = Form(default="video"),
        creation_time: str | None = Form(default=None),
        id: UUID | None = Form(default=None),
        sensor_name: str = Form(default=""),
        url: str | None = Form(default=None),
    ) -> FileInfo:
        if url:
            raise HTTPException(
                status_code=501, detail="URL ingestion is not implemented; upload the file as multipart data"
            )
        if file is None:
            raise HTTPException(status_code=422, detail="file is required")
        if purpose != "vision" or media_type not in {"video", "image"}:
            raise HTTPException(status_code=422, detail="purpose must be vision and media_type must be video or image")
        try:
            asset = await store.save(
                file,
                file_id=id,
                purpose=purpose,
                media_type=media_type,
                creation_time=creation_time,
                sensor_name=sensor_name,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        if media_type == "video":
            try:
                await processor.probe(asset.path)
            except Exception as exc:
                await store.delete(asset.info.id)
                raise HTTPException(status_code=422, detail=f"Invalid video: {exc}") from exc
        return asset.info

    @app.get("/v1/files", response_model=ListFilesResponse)
    async def list_files(purpose: str = "vision") -> ListFilesResponse:
        assets = await store.list()
        return ListFilesResponse(data=[asset.info for asset in assets if asset.info.purpose == purpose])

    @app.delete("/v1/files/{file_id}", response_model=DeleteFileResponse)
    async def delete_file(file_id: UUID) -> DeleteFileResponse:
        try:
            await store.delete(file_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No such resource {file_id}") from exc
        return DeleteFileResponse(id=file_id, deleted=True)

    @app.post("/v1/generate_captions")
    async def generate_captions(request: GenerateCaptionsRequest):
        try:
            asset = await store.get(request.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No such resource {request.id}") from exc
        if asset.info.media_type != "video":
            raise HTTPException(status_code=422, detail="The initial implementation supports video assets only")
        try:
            metadata: VideoMetadata = await processor.probe(asset.path)
        except Exception as exc:
            logger.exception("Failed to probe video %s", asset.path)
            raise HTTPException(status_code=422, detail=f"Invalid video: {exc}") from exc

        range_start = request.media_info.start_offset if request.media_info else 0.0
        range_end = (
            request.media_info.end_offset if request.media_info and request.media_info.end_offset else metadata.duration
        )
        range_end = min(range_end, metadata.duration)
        duration = request.chunk_duration or runtime_settings.default_chunk_duration
        try:
            chunks = chunk_ranges(range_start, range_end, duration, request.chunk_overlap_duration)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        query_id = uuid4()

        async def events() -> AsyncIterator[str]:
            processed = 0
            try:
                for chunk in chunks:
                    async with request_semaphore:
                        response = await _process_chunk(
                            asset=asset,
                            chunk=chunk,
                            request=request,
                            settings=runtime_settings,
                            video_processor=processor,
                            backend=app.state.backend,
                            query_id=query_id,
                        )
                    app.state.kafka_publisher.publish(
                        stream_id=str(asset.info.id),
                        chunk=response["chunk_responses"][0],
                        model=response["model"],
                        request_id=str(query_id),
                    )
                    processed += 1
                    yield f"data: {json.dumps(response, separators=(',', ':'))}\n\n"
                if request.stream_options and request.stream_options.include_usage:
                    usage = {
                        "id": str(query_id),
                        "created": int(time.time()),
                        "model": request.model or app.state.backend.model,
                        "media_info": None,
                        "usage": {"total_chunks_processed": processed},
                        "chunk_responses": [],
                    }
                    yield f"data: {json.dumps(usage, separators=(',', ':'))}\n\n"
            except Exception as exc:
                logger.exception("Caption generation failed")
                error = {"code": type(exc).__name__, "message": str(exc)}
                yield f"event: error\ndata: {json.dumps(error, separators=(',', ':'))}\n\n"
            finally:
                yield "data: [DONE]\n\n"

        if request.stream:
            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

        responses = []
        try:
            for chunk in chunks:
                async with request_semaphore:
                    response = await _process_chunk(
                        asset=asset,
                        chunk=chunk,
                        request=request,
                        settings=runtime_settings,
                        video_processor=processor,
                        backend=app.state.backend,
                        query_id=query_id,
                    )
                responses.append(response)
                app.state.kafka_publisher.publish(
                    stream_id=str(asset.info.id),
                    chunk=response["chunk_responses"][0],
                    model=response["model"],
                    request_id=str(query_id),
                )
        except OpenAIError as exc:
            logger.warning("OpenAI-compatible VLM request failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"OpenAI-compatible VLM request failed: {exc}") from exc
        return {
            "id": str(query_id),
            "created": int(time.time()),
            "model": request.model or app.state.backend.model,
            "media_info": _media_info(asset, range_start, range_end),
            "usage": {"total_chunks_processed": len(responses)},
            "chunk_responses": [chunk for response in responses for chunk in response["chunk_responses"]],
        }

    return app


app = create_app()
