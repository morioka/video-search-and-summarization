from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "preflight",
    Path(__file__).parents[1] / "scripts" / "preflight.py",
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def test_main_checks_services_and_required_stream(monkeypatch, capsys):
    responses = {
        "http://vst/vst/api/v1/sensor/streams": [{"stream-1": [{"name": "konro_resume4"}]}],
        "http://rtvi/v1/health/ready": {"status": "ready"},
        "http://lvs/v1/ready": {},
        "http://agent/health": {"value": {"isAlive": True}},
        "http://vst/vst/api/v1/storage/timelines": {},
    }
    monkeypatch.setattr(preflight, "get_json", lambda url, timeout: responses[url])
    monkeypatch.setattr(
        "sys.argv",
        [
            "preflight.py",
            "--vst",
            "http://vst",
            "--rtvi",
            "http://rtvi",
            "--lvs",
            "http://lvs",
            "--agent",
            "http://agent",
            "--require-stream",
            "konro_resume4",
            "--check-timeline-api",
        ],
    )

    assert preflight.main() == 0
    assert "Preflight passed" in capsys.readouterr().out


def test_main_fails_when_required_stream_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "get_json", lambda url, timeout: [])
    monkeypatch.setattr("sys.argv", ["preflight.py", "--require-stream", "missing"])

    assert preflight.main() == 1
    assert "required stream is missing" in capsys.readouterr().err


def test_main_fails_on_service_error(monkeypatch, capsys):
    def fail(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(preflight, "get_json", fail)
    monkeypatch.setattr("sys.argv", ["preflight.py"])

    assert preflight.main() == 1
    assert "VST is not ready" in capsys.readouterr().err
