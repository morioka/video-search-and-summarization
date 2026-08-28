#!/usr/bin/env python3
"""Check the VSS startup prerequisites before running an E2E workflow."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def get_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.load(response)


def check(name: str, url: str, timeout: float) -> object:
    try:
        payload = get_json(url, timeout)
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        raise RuntimeError(f"{name} is not ready: {url}: {exc}") from exc
    print(f"OK {name}: {url}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vst", default="http://127.0.0.1:31000", help="VST/NVStreamer base URL")
    parser.add_argument("--rtvi", default="http://127.0.0.1:8018", help="RT-VLM base URL")
    parser.add_argument("--lvs", default="http://127.0.0.1:38111", help="LVS base URL")
    parser.add_argument("--agent", default="", help="Optional Agent base URL")
    parser.add_argument("--require-stream", help="Require this VST stream name in the stream list")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    try:
        streams = check("VST", f"{args.vst.rstrip('/')}/vst/api/v1/sensor/streams", args.timeout)
        if not isinstance(streams, list):
            raise RuntimeError("VST stream response is not a JSON list")
        names = {
            entry[stream_id][0]["name"]
            for entry in streams
            if isinstance(entry, dict)
            for stream_id in entry
            if isinstance(entry[stream_id], list) and entry[stream_id]
            and isinstance(entry[stream_id][0], dict) and entry[stream_id][0].get("name")
        }
        print(f"OK VST stream inventory: {len(names)} stream(s)")
        if args.require_stream and args.require_stream not in names:
            raise RuntimeError(f"required stream is missing: {args.require_stream!r}; available={sorted(names)}")
        check("RT-VLM", f"{args.rtvi.rstrip('/')}/v1/health/ready", args.timeout)
        check("LVS", f"{args.lvs.rstrip('/')}/v1/ready", args.timeout)
        if args.agent:
            check("Agent", f"{args.agent.rstrip('/')}/health", args.timeout)
    except RuntimeError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("Preflight passed: proceed with E2E in the checked order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
