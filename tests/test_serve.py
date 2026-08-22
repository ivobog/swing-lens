from __future__ import annotations

from types import SimpleNamespace

import app.serve as serve


def test_bind_diagnostic_reports_listener_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        serve,
        "diagnose_listener",
        lambda _host, _port: serve.ListenerOwner(4242, "python.exe"),
    )

    message = serve.explain_bind_error("127.0.0.1", 8000, OSError("bind failed"))

    assert "127.0.0.1:8000" in message
    assert "PID 4242" in message
    assert "python.exe" in message


def test_winerror_10013_is_not_misclassified_without_listener(monkeypatch) -> None:
    monkeypatch.setattr(serve, "diagnose_listener", lambda _host, _port: None)
    error = OSError("permission denied")
    error.winerror = 10013

    message = serve.explain_bind_error("127.0.0.1", 8000, error)

    assert "WSAEACCES/10013" in message
    assert "not being classified as ordinary address-in-use" in message


def test_reload_excludes_all_runtime_directories() -> None:
    excluded = set(serve.RUNTIME_RELOAD_EXCLUDES)

    assert {"logs/**", "output/**", "data/**", "backups/**", ".qa_work/**"} <= excluded


def test_main_passes_reload_exclusions_to_uvicorn(monkeypatch) -> None:
    observed = SimpleNamespace(kwargs=None)
    monkeypatch.setattr(serve, "diagnose_listener", lambda _host, _port: None)
    monkeypatch.setattr(
        serve.uvicorn,
        "run",
        lambda *_args, **kwargs: setattr(observed, "kwargs", kwargs),
    )

    serve.main(["--reload", "--port", "8765"])

    assert observed.kwargs["reload"] is True
    assert "logs/**" in observed.kwargs["reload_excludes"]
