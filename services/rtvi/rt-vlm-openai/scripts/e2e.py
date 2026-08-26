# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the stored-video RT-VLM API against a running service."""

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8018")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument(
        "--prompt",
        default="Describe the inspection actions in chronological order, including safety-relevant details.",
    )
    parser.add_argument("--chunk-duration", type=int, default=30)
    parser.add_argument("--chunk-overlap", type=int, default=0)
    parser.add_argument("--frames-per-chunk", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--keep-file", action="store_true")
    return parser.parse_args()


def parse_sse(lines: Iterable[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    event = "message"
    for line in lines:
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            payload = json.loads(data)
            if event == "error":
                raise RuntimeError(f"caption stream failed: {payload}")
            payloads.append(payload)
            event = "message"
    return payloads


def main() -> None:
    args = parse_args()
    video = args.video.expanduser().resolve()
    if not video.is_file():
        raise SystemExit(f"video does not exist: {video}")
    if args.chunk_duration < 1:
        raise SystemExit("--chunk-duration must be positive")
    if args.chunk_overlap >= args.chunk_duration:
        raise SystemExit("--chunk-overlap must be less than --chunk-duration")
    if args.frames_per_chunk < 1:
        raise SystemExit("--frames-per-chunk must be positive")

    client = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout)
    file_id: str | None = None
    try:
        with video.open("rb") as stream:
            upload = client.post(
                "/v1/files",
                files={"file": (video.name, stream, "video/mp4")},
                data={"purpose": "vision", "media_type": "video"},
            )
        upload.raise_for_status()
        uploaded = upload.json()
        file_id = uploaded["id"]
        print(f"uploaded id={file_id} bytes={uploaded['bytes']} filename={uploaded['filename']}")

        request = {
            "id": file_id,
            "prompt": args.prompt,
            "stream": args.stream,
            "chunk_duration": args.chunk_duration,
            "chunk_overlap_duration": args.chunk_overlap,
            "num_frames_per_second_or_fixed_frames_chunk": args.frames_per_chunk,
            "stream_options": {"include_usage": True},
        }
        if args.stream:
            with client.stream("POST", "/v1/generate_captions", json=request) as response:
                response.raise_for_status()
                payloads = parse_sse(response.iter_lines())
            result = {"events": payloads}
        else:
            response = client.post("/v1/generate_captions", json=request)
            response.raise_for_status()
            result = response.json()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if file_id and not args.keep_file:
            response = client.delete(f"/v1/files/{file_id}")
            response.raise_for_status()
            print(f"deleted id={file_id}")
        client.close()


if __name__ == "__main__":
    main()
