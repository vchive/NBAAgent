"""Single-port deployment contract for the bundled web demo."""

from fastapi.testclient import TestClient

from apps.api.src.main import create_app


def test_root_serves_demo_from_same_origin() -> None:
    with TestClient(create_app()) as client:
        page = client.get("/")
        asset = client.get("/styles.css")

    assert page.status_code == 200
    assert "COURTSIDE" in page.text
    assert page.headers["content-type"].startswith("text/html")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/css")


def test_api_routes_keep_precedence_over_static_mount() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_web_demo_exposes_multi_game_highlights_projection() -> None:
    """The mounted shell must keep the multi-game selector contract visible.

    This is intentionally a small static contract: the browser owns selection
    state, while the API already guarantees that ``games[]`` is not truncated.
    Catching a missing mount hook here prevents a deployment from silently
    serving an older single-game page after a backend-only rollout.
    """

    with TestClient(create_app()) as client:
        page = client.get("/")
        script = client.get("/app.js")

    assert page.status_code == 200
    assert 'id="game-list"' in page.text
    assert 'id="games-section"' in page.text
    assert script.status_code == 200
    assert "renderGameList" in script.text
    assert "selectActiveGame" in script.text
    assert "2026-demo-den-gsw" in script.text
    assert "2026-demo-lal-nyk" in script.text
    assert "games[0]" not in script.text


def test_web_demo_keeps_stream_status_hidden_until_a_request_starts() -> None:
    with TestClient(create_app()) as client:
        styles = client.get("/styles.css")

    assert styles.status_code == 200
    assert ".stream-status[hidden]" in styles.text
    assert "display: none" in styles.text[styles.text.index(".stream-status[hidden]"):]
