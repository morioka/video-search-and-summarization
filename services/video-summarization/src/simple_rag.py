# SPDX-License-Identifier: Apache-2.0
"""Small dependency-free RAG adapter for local, license-free smoke runs.

This is intentionally a contract adapter, not a quality replacement for
Context-Aware RAG.  It keeps chunk text in memory and returns the same result
shape used by the LVS aggregation path, allowing the service to run without
``vss-ctx-rag`` or NVIDIA-only runtime images.
"""

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace


class SimpleRagAdapter:
    """In-memory ContextManager-compatible adapter."""

    def __init__(self, process_index=0):
        self._process_index = process_index
        self.process = SimpleNamespace(kill=lambda: None, join=lambda timeout=None: None)
        self._uuid = None
        self._docs = {}

    @staticmethod
    def _llm_summary(text):
        """Ask an OpenAI-compatible endpoint for a summary, if configured."""
        if os.environ.get("LVS_SIMPLE_RAG_LLM", "false").lower() not in ("1", "true", "yes"):
            return None
        base_url = os.environ.get("LVS_LLM_BASE_URL", "").rstrip("/")
        model = os.environ.get("LVS_LLM_MODEL_NAME", "")
        if not base_url or not model or not text:
            return None
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize the video captions. Return JSON only with keys "
                        "events (array) and video_summary (string)."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content)
            parsed = json.loads(str(content).strip())
            if isinstance(parsed, dict) and isinstance(parsed.get("video_summary"), str):
                return parsed
        except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError):
            return None
        return None

    def configure(self, config):
        self._uuid = config.get("uuid") if isinstance(config, dict) else None

    def add_doc(self, doc, doc_i, doc_meta, callback=None):
        self._docs[int(doc_i)] = str(doc or "")
        if callback:
            callback(SimpleNamespace(result=lambda: None))

    def call(self, config):
        if "summarization" in config:
            text = "\n".join(self._docs[index] for index in sorted(self._docs) if self._docs[index])
            summary = self._llm_summary(text) or {"events": [], "video_summary": text}
            result = json.dumps(summary, ensure_ascii=False)
            return {"summarization": {"result": result, "metadata": {}}}
        return {}

    def reset(self, expr=None):
        self._docs.clear()

    def drop_collection(self):
        self._docs.clear()
