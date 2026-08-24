# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API models for the supported RT-VLM compatibility surface."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileInfo(StrictModel):
    id: UUID
    bytes: int
    filename: str
    creation_time: str | None = None
    purpose: Literal["vision"] = "vision"
    sensor_name: str = ""
    media_type: Literal["video", "image"] = "video"


class ListFilesResponse(StrictModel):
    data: list[FileInfo]
    object: Literal["list"] = "list"


class DeleteFileResponse(StrictModel):
    id: UUID
    object: Literal["file"] = "file"
    deleted: bool


class StreamOptions(StrictModel):
    include_usage: bool = False


class MediaInfo(StrictModel):
    type: Literal["offset"] = "offset"
    start_offset: float = Field(default=0, ge=0)
    end_offset: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "MediaInfo":
        if self.end_offset is not None and self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class ResponseFormat(StrictModel):
    type: Literal["text", "json_object"] = "text"


class GenerateCaptionsRequest(BaseModel):
    """Subset consumed by LVS plus harmless compatibility fields."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    prompt: str
    model: str = ""
    stream: bool = False
    chunk_duration: int = Field(default=0, ge=0, le=3600)
    chunk_overlap_duration: int = Field(default=0, ge=-3600, le=3600)
    system_prompt: str = ""
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = None
    response_format: ResponseFormat = Field(default_factory=ResponseFormat)
    stream_options: StreamOptions | None = None
    media_info: MediaInfo | None = None
    num_frames_per_second_or_fixed_frames_chunk: float | None = Field(default=None, ge=-1, le=256)
    use_fps_for_chunking: bool = False
    vlm_input_width: int | None = Field(default=None, ge=0, le=4096)
    vlm_input_height: int | None = Field(default=None, ge=0, le=4096)
    enable_reasoning: bool = False
    enable_audio: bool = False

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be empty")
        return value

    @model_validator(mode="after")
    def validate_chunking(self) -> "GenerateCaptionsRequest":
        if self.chunk_duration and self.chunk_overlap_duration >= self.chunk_duration:
            raise ValueError("chunk_overlap_duration must be less than chunk_duration")
        if self.enable_audio:
            raise ValueError("enable_audio is not supported by the OpenAI compatibility implementation")
        return self


class OpenAIResult(StrictModel):
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


JsonDict = dict[str, Any]
