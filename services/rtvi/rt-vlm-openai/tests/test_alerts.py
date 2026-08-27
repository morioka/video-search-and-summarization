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
