from pathlib import Path

from fastapi.testclient import TestClient

from fund_research_agent.api.app import _inject_before_last_closing_tag, create_app


def test_session_and_health_api():
    with TestClient(create_app()) as client:
        session = client.post("/api/v1/sessions", json={"title": "API test"})
        assert session.status_code == 200
        session_id = session.json()["session_id"]
        assert client.get(f"/api/v1/sessions/{session_id}").status_code == 200
        health = client.get("/api/v1/dependencies/health").json()
        assert health["healthy"] is True
        assert "report_worker" in health
        assert "ready_for_reports" in health
        assert client.get("/api/v1/report-jobs/missing/view").status_code == 404


def test_web_assets_use_relative_base_path():
    with TestClient(create_app()) as client:
        html = client.get("/").text
        assert 'href="./styles.css?v=' in html
        assert 'href="./report-cards.css"' in html
        assert 'src="./app.js"' in html or 'src="./app.js?v=' in html


def test_workspace_has_independent_session_and_message_scroll_regions():
    css = (Path(__file__).resolve().parents[2] / "web/styles.css").read_text(
        encoding="utf-8"
    )

    assert css.count("grid-template-rows:auto minmax(0,1fr) auto") >= 2
    assert ".sessions-panel{display:grid" in css
    assert ".session-list{min-height:0" in css
    assert ".messages{min-height:0;overflow-y:auto" in css


def test_report_view_injection_does_not_break_inline_javascript():
    source = "<head><script>const template='</head>';</script></head><body>ok</body>"

    result = _inject_before_last_closing_tag(source, "head", "<style>safe</style>")

    assert "const template='</head>'" in result
    assert result.endswith("<style>safe</style></head><body>ok</body>")
