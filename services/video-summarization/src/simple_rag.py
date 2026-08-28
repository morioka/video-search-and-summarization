# SPDX-License-Identifier: Apache-2.0
"""Small dependency-free RAG adapter for local, license-free smoke runs.

This is intentionally a contract adapter, not a quality replacement for
Context-Aware RAG.  It keeps chunk text in memory and returns the same result
shape used by the LVS aggregation path, allowing the service to run without
``vss-ctx-rag`` or NVIDIA-only runtime images.
"""

import json
from types import SimpleNamespace


class SimpleRagAdapter:
    """In-memory ContextManager-compatible adapter."""

    def __init__(self, process_index=0):
        self._process_index = process_index
        self.process = SimpleNamespace(kill=lambda: None, join=lambda timeout=None: None)
        self._uuid = None
        self._docs = {}

    def configure(self, config):
        self._uuid = config.get("uuid") if isinstance(config, dict) else None

    def add_doc(self, doc, doc_i, doc_meta, callback=None):
        self._docs[int(doc_i)] = str(doc or "")
        if callback:
            callback(SimpleNamespace(result=lambda: None))

    def call(self, config):
        if "summarization" in config:
            text = "\n".join(self._docs[index] for index in sorted(self._docs) if self._docs[index])
            result = json.dumps(
                {"events": [], "video_summary": text}, ensure_ascii=False
            )
            return {"summarization": {"result": result, "metadata": {}}}
        return {}

    def reset(self, expr=None):
        self._docs.clear()

    def drop_collection(self):
        self._docs.clear()

