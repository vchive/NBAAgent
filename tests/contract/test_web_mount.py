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
