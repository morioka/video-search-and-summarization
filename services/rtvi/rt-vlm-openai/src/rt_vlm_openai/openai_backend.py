# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenAI multimodal inference adapter."""

import time
from collections.abc import Sequence

from openai import AsyncOpenAI

from .models import GenerateCaptionsRequest, OpenAIResult


class OpenAIBackend:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None,
        timeout: int,
        max_tokens: int,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if client is None:
            kwargs: dict[str, object] = {"api_key": api_key, "timeout": timeout, "max_retries": 3}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncOpenAI(**kwargs)
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def caption(
        self,
        request: GenerateCaptionsRequest,
        images: Sequence[str],
        *,
        start: float,
        end: float,
    ) -> tuple[OpenAIResult, float]:
        temporal_prompt = (
            f"{request.prompt.rstrip()}\n\n"
            f"The images are ordered samples from video time {start:.3f}s through {end:.3f}s. "
            "They are sparse observations, not continuous footage. Preserve their temporal order and report only "
            "actions and objects directly supported by the images. If an object's identity is uncertain, describe "
            "its visible appearance and how it is handled without naming it. Do not offer alternative identities "
            "or use speculative language such as maybe, probably, or possibly unless the user explicitly requests "
            "inference. Do not infer intent, purpose, or an action occurring between samples. Report a transition "
            "verb such as open, close, insert, or remove only when the sampled images directly show both the prior "
            "and resulting states. An object that is merely touched, pointed at, or later hidden from view must not "
            "be described as removed."
        )
        content: list[dict[str, object]] = [{"type": "text", "text": temporal_prompt}]
        content.extend(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}", "detail": "low"}}
            for image in images
        )
        messages: list[dict[str, object]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": content})

        kwargs: dict[str, object] = {
            "model": request.model or self.model,
            "messages": messages,
            "max_tokens": min(request.max_tokens or self._max_tokens, self._max_tokens),
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.seed is not None:
            kwargs["seed"] = request.seed
        if request.response_format.type == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        response = await self._client.chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - started) * 1000
        message_content = response.choices[0].message.content if response.choices else None
        if not message_content:
            raise ValueError("OpenAI returned an empty caption")
        usage = response.usage
        return (
            OpenAIResult(
                content=message_content,
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
            ),
            latency_ms,
        )
