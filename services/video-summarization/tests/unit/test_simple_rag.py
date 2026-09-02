import json

from simple_rag import SimpleRagAdapter


def test_simple_rag_preserves_lvs_summarization_shape():
    rag = SimpleRagAdapter()
    rag.configure({"uuid": "asset-1"})
    rag.add_doc("first caption", 1, {})
    rag.add_doc("second caption", 2, {})

    response = rag.call({"summarization": {"start_index": 0, "end_index": 2}})
    payload = json.loads(response["summarization"]["result"])

    assert payload["events"] == []
    assert payload["video_summary"] == "first caption\nsecond caption"


def test_simple_rag_reset_and_drop_are_idempotent():
    rag = SimpleRagAdapter()
    rag.add_doc("caption", 0, {})
    rag.reset()
    rag.drop_collection()
    assert rag.call({"summarization": {}})["summarization"]["result"]


def test_simple_rag_llm_is_opt_in(monkeypatch):
    monkeypatch.delenv("LVS_SIMPLE_RAG_LLM", raising=False)
    rag = SimpleRagAdapter()
    rag.add_doc("caption", 0, {})
    payload = json.loads(rag.call({"summarization": {}})["summarization"]["result"])
    assert payload["video_summary"] == "caption"
