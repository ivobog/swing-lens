from __future__ import annotations

from types import SimpleNamespace

import pytest

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
    monkeypatch.setattr(serve, "run_startup_preflight", lambda: None)
    monkeypatch.setattr(
        serve.uvicorn,
        "run",
        lambda *_args, **kwargs: setattr(observed, "kwargs", kwargs),
    )

    serve.main(["--reload", "--port", "8765"])

    assert observed.kwargs["reload"] is True
    assert "logs/**" in observed.kwargs["reload_excludes"]


@pytest.mark.parametrize(
    "failure",
    (
        "database unavailable at 127.0.0.1:5432/swinglens",
        "migration head mismatch at 127.0.0.1:5432/swinglens",
    ),
)
def test_preflight_failure_prevents_uvicorn(monkeypatch, failure: str) -> None:
    invoked = False

    def fail_preflight() -> None:
        raise serve.StartupPreflightError(failure)

    def record_uvicorn(*_args, **_kwargs) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(serve, "diagnose_listener", lambda _host, _port: None)
    monkeypatch.setattr(serve, "run_startup_preflight", fail_preflight)
    monkeypatch.setattr(serve.uvicorn, "run", record_uvicorn)

    with pytest.raises(SystemExit, match=failure):
        serve.main(["--port", "8765"])

    assert invoked is False


def test_successful_preflight_allows_uvicorn(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(serve, "diagnose_listener", lambda _host, _port: None)
    monkeypatch.setattr(serve, "run_startup_preflight", lambda: events.append("preflight"))
    monkeypatch.setattr(serve.uvicorn, "run", lambda *_args, **_kwargs: events.append("uvicorn"))

    serve.main(["--port", "8765"])

    assert events == ["preflight", "uvicorn"]


def test_preflight_error_redacts_database_credentials(monkeypatch) -> None:
    invoked = False

    def fail_preflight() -> None:
        raise serve.StartupPreflightError(
            "database unavailable: postgresql://admin:top-secret@127.0.0.1:5432/swinglens"
        )

    def record_uvicorn(*_args, **_kwargs) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(serve, "diagnose_listener", lambda _host, _port: None)
    monkeypatch.setattr(serve, "run_startup_preflight", fail_preflight)
    monkeypatch.setattr(serve.uvicorn, "run", record_uvicorn)

    with pytest.raises(SystemExit) as exc_info:
        serve.main(["--port", "8765"])

    message = str(exc_info.value)
    assert "top-secret" not in message
    assert "admin" not in message
    assert "<restricted:userinfo>" in message
    assert invoked is False
