"""Small, CPU-only VIA-compatible service for stored-video validation."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rt_vlm_openai.assets import Asset, AssetStore
from rt_vlm_openai.models import FileInfo, GenerateCaptionsRequest
from rt_vlm_openai.openai_backend import OpenAIBackend
from rt_vlm_openai.video import VideoProcessor, chunk_ranges


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str | None = None
    id: UUID | str | None = None
    prompt: str = "Describe the events in this video with timestamps."
    model: str = ""
    chunk_duration: int = Field(default=10, ge=1, le=3600)
    num_frames_per_second_or_fixed_frames_chunk: int = Field(default=3, ge=1, le=24)
    max_tokens: int = Field(default=512, ge=1)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = ""
    id: UUID | str
    messages: list[dict[str, Any]] = Field(default_factory=list)


ROOT = Path(os.getenv("VIA_LOCAL_ASSET_DIR", "/tmp/via-assets"))
CAPTION_FILE = ROOT / "captions.jsonl"
MODEL = os.getenv("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", os.getenv("VIA_VLM_MODEL", "gpt-4o"))
BASE_URL = os.getenv("VIA_VLM_ENDPOINT", os.getenv("OPENAI_BASE_URL", "")) or None
API_KEY = os.getenv("VIA_VLM_API_KEY", os.getenv("OPENAI_API_KEY", "dummy"))
MAX_UPLOAD = int(os.getenv("VIA_LOCAL_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024 * 1024)))

app = FastAPI(title="VIA Local Compatibility API", version="0.1.0")
store = AssetStore(ROOT / "files", MAX_UPLOAD)
processor = VideoProcessor()
backend = OpenAIBackend(
    api_key=API_KEY,
    model=MODEL,
    base_url=BASE_URL,
    timeout=int(os.getenv("VIA_VLM_TIMEOUT", "180")),
    max_tokens=int(os.getenv("VIA_VLM_MAX_TOKENS", "4096")),
)


@app.on_event("startup")
async def startup() -> None:
    await store.initialize()
    CAPTION_FILE.parent.mkdir(parents=True, exist_ok=True)


def _completion(asset_id: str, model: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    text = "\n".join(f"[{item['start_time']} - {item['end_time']}] {item['content']}" for item in chunks)
    return {
        "id": asset_id,
        "video_id": asset_id,
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "media_info": {"type": "offset", "start_offset": 0, "end_offset": None},
        "choices": [{"index": 0, "finish_reason": "stop", "text": text}],
        "content": text,
        "usage": {"total_chunks_processed": len(chunks), "query_processing_time": 0},
    }


async def _caption_asset(asset: Asset, request: SummarizeRequest) -> list[dict[str, Any]]:
    metadata = await processor.probe(asset.path)
    ranges = chunk_ranges(0, metadata.duration, request.chunk_duration, 0)
    result: list[dict[str, Any]] = []
    prompt = GenerateCaptionsRequest(
        id=asset.info.id,
        prompt=request.prompt,
        model=request.model or MODEL,
        num_frames_per_second_or_fixed_frames_chunk=request.num_frames_per_second_or_fixed_frames_chunk,
        max_tokens=request.max_tokens,
    )
    for chunk in ranges:
        frames = await processor.extract_frames(asset.path, chunk, request.num_frames_per_second_or_fixed_frames_chunk, 768, 768)
        answer, _ = await backend.caption(prompt, frames.images, start=chunk.start, end=chunk.end)
        item = {
            "video_id": str(asset.info.id),
            "sensor_id": asset.info.sensor_name,
            "chunk_id": chunk.index,
            "start_time": chunk.start,
            "end_time": chunk.end,
            "content": answer.content,
        }
        result.append(item)
        with CAPTION_FILE.open("a", encoding="utf-8") as output:
            output.write(json.dumps(item, ensure_ascii=True) + "\n")
    return result


@app.get("/v1/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/startup")
async def startup_probe() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/ready")
async def ready() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/metadata")
async def metadata() -> dict[str, Any]:
    return {"version": app.version, "implementation": "via-local", "model": MODEL}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "openai-compatible"}]}


@app.post("/v1/files")
async def upload_file(
    purpose: str = Form("vision"),
    media_type: str = Form("video"),
    file: UploadFile = File(...),
    sensor_name: str = Form(""),
) -> dict[str, Any]:
    if purpose != "vision" or media_type not in {"video", "image"}:
        raise HTTPException(422, "purpose must be vision and media_type must be video or image")
    asset = await store.save(file, file_id=None, purpose=purpose, media_type=media_type, creation_time=None, sensor_name=sensor_name)
    return asset.info.model_dump(mode="json")


@app.get("/v1/files")
async def list_files() -> dict[str, Any]:
    return {"object": "list", "data": [asset.info.model_dump(mode="json") for asset in await store.list()]}


@app.delete("/v1/files/{file_id}")
async def delete_file(file_id: UUID) -> dict[str, Any]:
    try:
        await store.delete(file_id)
    except KeyError:
        raise HTTPException(404, "file not found")
    return {"id": str(file_id), "object": "file", "deleted": True}


async def _resolve_asset(request: SummarizeRequest) -> Asset:
    if request.id:
        try:
            return await store.get(UUID(str(request.id)))
        except (ValueError, KeyError):
            pass
    if not request.url:
        raise HTTPException(422, "url or id is required")
    path = Path(request.url.removeprefix("file://"))
    if not path.is_file():
        raise HTTPException(404, f"video not found: {request.url}")
    asset_id = UUID(str(request.id)) if request.id else uuid4()
    info = FileInfo(id=asset_id, bytes=path.stat().st_size, filename=path.name, purpose="vision", media_type="video")
    return Asset(info=info, path=path)


@app.post("/v1/generate_vlm_captions")
@app.post("/v1/summarize")
async def summarize(request: SummarizeRequest) -> dict[str, Any]:
    asset = await _resolve_asset(request)
    try:
        chunks = await _caption_asset(asset, request)
    except Exception as exc:
        raise HTTPException(502, f"video analysis failed: {exc}") from exc
    return _completion(str(asset.info.id), request.model or MODEL, chunks)


@app.post("/v1/chat/completions")
async def chat(request: ChatRequest) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if CAPTION_FILE.exists():
        for line in CAPTION_FILE.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("video_id") == str(request.id):
                    rows.append(item)
            except json.JSONDecodeError:
                continue
    context = "\n".join(f"[{r['start_time']}-{r['end_time']}] {r['content']}" for r in rows)
    question = next((str(m.get("content", "")) for m in reversed(request.messages) if m.get("role") == "user"), "")
    answer = context or "No captions are available for this video."
    if context and BASE_URL and API_KEY != "dummy":
        client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
        response = await client.chat.completions.create(
            model=request.model or MODEL,
            messages=[{"role": "system", "content": "Answer only from the supplied video captions."}, {"role": "user", "content": f"Captions:\n{context}\n\nQuestion: {question}"}],
            max_tokens=512,
        )
        answer = response.choices[0].message.content or answer
    return {"id": str(request.id), "object": "chat.completion", "model": request.model or MODEL, "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}


@app.post("/v1/generate_captions")
@app.post("/v1/stream_summarize")
async def livestream_not_implemented() -> None:
    raise HTTPException(501, "livestream APIs are not implemented by the local VIA engine")
