# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM python:3.13-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 vss
WORKDIR /app
COPY --from=builder --chown=vss:vss /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    RTVI_OPENAI_ASSET_DIR=/var/lib/rt-vlm-openai/assets
RUN mkdir -p /var/lib/rt-vlm-openai/assets && chown -R vss:vss /var/lib/rt-vlm-openai
USER vss
EXPOSE 8000
ENTRYPOINT ["uvicorn", "rt_vlm_openai.app:app", "--host", "0.0.0.0", "--port", "8000"]
