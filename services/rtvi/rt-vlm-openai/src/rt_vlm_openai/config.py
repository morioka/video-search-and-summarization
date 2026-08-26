# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration."""

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    """Configuration sourced from the existing RT-VLM environment variables."""

    model: str
    api_key: str
    base_url: str | None
    asset_dir: Path
    max_upload_bytes: int
    default_chunk_duration: int
    default_frames_per_chunk: int
    max_frames_per_chunk: int
    max_tokens: int
    request_timeout_seconds: int
    kafka_bootstrap_servers: str = ""
    kafka_topic: str = "mdx-vlm-captions"

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv("VIA_VLM_ENDPOINT", "").strip() or None
        api_key = os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("VIA_VLM_API_KEY", "").strip()
        return cls(
            model=os.getenv("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", "").strip(),
            api_key=api_key,
            base_url=base_url,
            asset_dir=Path(os.getenv("RTVI_OPENAI_ASSET_DIR", "/tmp/rt-vlm-openai-assets")),
            max_upload_bytes=_positive_int("RTVI_OPENAI_MAX_UPLOAD_BYTES", 10_000_000_000),
            default_chunk_duration=_positive_int("RTVI_OPENAI_DEFAULT_CHUNK_DURATION", 30),
            default_frames_per_chunk=_positive_int("RTVI_OPENAI_DEFAULT_FRAMES_PER_CHUNK", 8),
            max_frames_per_chunk=_positive_int("RTVI_OPENAI_MAX_FRAMES_PER_CHUNK", 24),
            max_tokens=_positive_int("VLM_MAX_GENERATION_TOKENS", 4096),
            request_timeout_seconds=_positive_int("RTVI_OPENAI_REQUEST_TIMEOUT_SECONDS", 180),
            kafka_bootstrap_servers=os.getenv("RTVI_OPENAI_KAFKA_BOOTSTRAP_SERVERS", "").strip(),
            kafka_topic=os.getenv("RTVI_OPENAI_KAFKA_TOPIC", "mdx-vlm-captions").strip(),
        )

    def validate(self) -> None:
        if not self.model:
            raise ValueError("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME is required")
        if not self.api_key:
            raise ValueError("VIA_VLM_API_KEY or OPENAI_API_KEY is required")
        if self.default_frames_per_chunk > self.max_frames_per_chunk:
            raise ValueError("RTVI_OPENAI_DEFAULT_FRAMES_PER_CHUNK cannot exceed RTVI_OPENAI_MAX_FRAMES_PER_CHUNK")
