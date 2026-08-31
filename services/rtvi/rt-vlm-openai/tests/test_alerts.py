import pytest

from rt_vlm_openai.alerts import AlertSink


@pytest.mark.asyncio
async def test_alert_sink_disabled_by_default() -> None:
    sink = AlertSink()
    assert not sink.enabled
    assert not await sink.emit_if_match(stream_id="s", content="fire", start="0", end="1")


@pytest.mark.asyncio
async def test_alert_sink_keyword_gate() -> None:
    sink = AlertSink("http://127.0.0.1:1", "hazard, fire")
    assert sink.enabled
    assert not await sink.emit_if_match(stream_id="s", content="normal operation", start="0", end="1")


@pytest.mark.asyncio
async def test_alert_sink_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    sink = AlertSink("http://alert", "fire", cooldown_seconds=60)
    posted: list[dict] = []
    monkeypatch.setattr(sink, "_post", posted.append)
    assert await sink.emit_if_match(stream_id="s", content="fire detected", start="0", end="1")
    assert not await sink.emit_if_match(stream_id="s", content="fire detected again", start="1", end="2")
    assert len(posted) == 1
