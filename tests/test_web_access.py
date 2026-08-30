from fastapi import FastAPI
from fastapi.testclient import TestClient

from xvsx import register_web_routes


def test_register_web_routes_mounts_static_files():
    app = FastAPI()
    register_web_routes(app)

    client = TestClient(app)

    support_response = client.get("/web/support")
    assert support_response.status_code == 200
    assert "support" in support_response.text.lower()

    privacy_response = client.get("/web/privacy.html")
    assert privacy_response.status_code == 200
    assert "privacy" in privacy_response.text.lower()
