from fastapi.testclient import TestClient

from app.main import app


def test_ib_gateway_page_is_html_and_not_status_json() -> None:
    response = TestClient(app).get("/ib")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<h1>IB Gateway</h1>" in response.text
    assert "IB Status JSON" in response.text


def test_settings_page_renders_read_only_configuration() -> None:
    response = TestClient(app).get("/settings")

    assert response.status_code == 200
    assert "<h1>Settings</h1>" in response.text
    assert "Read-only" in response.text
    assert "IB Fetch Planning" in response.text


def test_scoring_page_renders_fundamentals_v2_metadata() -> None:
    response = TestClient(app).get("/scoring")

    assert response.status_code == 200
    assert "<h1>Scoring</h1>" in response.text
    assert "fundamentals_v2.0" in response.text
    assert "Growth Quality Score" in response.text


def test_help_page_renders_workflow_and_glossary() -> None:
    response = TestClient(app).get("/help")

    assert response.status_code == 200
    assert "<h1>Help</h1>" in response.text
    assert "Upload the daily TradingView screener CSV." in response.text
    assert "Warning Glossary" in response.text


def test_primary_nav_uses_phase1_labels_and_active_state() -> None:
    response = TestClient(app).get("/help")

    assert response.status_code == 200
    assert 'href="/"' in response.text
    assert ">Dashboard</a>" in response.text
    assert 'href="/ib"' in response.text
    assert 'href="/ib/status">IB</a>' not in response.text
    assert 'href="/health">Health</a>' not in response.text
    assert 'href="/help" class="active"' in response.text
