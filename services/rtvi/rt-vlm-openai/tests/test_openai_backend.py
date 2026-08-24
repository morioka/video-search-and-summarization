# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

from rt_vlm_openai.models import GenerateCaptionsRequest
from rt_vlm_openai.openai_backend import OpenAIBackend


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A worker crosses the aisle."))],
            usage=SimpleNamespace(prompt_tokens=123, completion_tokens=9),
        )


async def test_caption_builds_standard_multimodal_chat_request() -> None:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    backend = OpenAIBackend(
        api_key="unused",
        model="openai-test-vlm",
        base_url=None,
        timeout=10,
        max_tokens=500,
        client=client,
    )
    request = GenerateCaptionsRequest(
        id="00000000-0000-0000-0000-000000000001",
        prompt="Describe the activity",
        model="openai-test-vlm",
        system_prompt="Be precise.",
        max_tokens=200,
    )

    result, latency = await backend.caption(request, ["jpeg-a", "jpeg-b"], start=2.0, end=12.0)

    assert result.content == "A worker crosses the aisle."
    assert result.input_tokens == 123
    assert result.output_tokens == 9
    assert latency >= 0
    assert completions.kwargs["model"] == "openai-test-vlm"
    assert completions.kwargs["max_tokens"] == 200
    assert completions.kwargs["messages"][0] == {"role": "system", "content": "Be precise."}
    user_content = completions.kwargs["messages"][1]["content"]
    assert "2.000s through 12.000s" in user_content[0]["text"]
    assert [item["image_url"]["url"] for item in user_content[1:]] == [
        "data:image/jpeg;base64,jpeg-a",
        "data:image/jpeg;base64,jpeg-b",
    ]
