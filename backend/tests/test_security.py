import pytest
from app.config import Settings
from app.main import create_app
from app.storage import Store
from fastapi.testclient import TestClient
from test_api import FakeAnalyzer, wait


def test_portal_inventory_cannot_be_enabled_without_instance_auth():
    with pytest.raises(ValueError, match="AUDIT_ACCESS_TOKEN"):
        Settings(portal_keys={"ru.ecode": "fake-private-key"}, access_token="")


def test_instance_token_protects_history_and_result_and_uses_httponly_cookie(tmp_path):
    settings = Settings(db_path=tmp_path / "auth.db", access_token="test-secret")
    with TestClient(create_app(settings, FakeAnalyzer())) as client:
        assert client.get("/api/analyses").status_code == 401
        assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
        login = client.post("/api/session", json={"token": "test-secret"})
        assert login.status_code == 200
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()
        assert "test-secret" not in login.headers["set-cookie"]
        assert client.get("/api/analyses").status_code == 200
        job = client.post("/api/analyses", json={"regid": "ru.ecode"}).json()["id"]
        wait(client, job)
        assert "test-secret" not in client.get("/api/analyses/" + job + "/result").text


def test_restart_marks_inflight_interrupted_but_preserves_complete_cache(tmp_path):
    path = tmp_path / "persist.db"
    store = Store(path)
    store.create("a", "ru.ecode", "key-a")
    store.create("b", "nl.example", "key-b")
    store.finish("b", {"summary": {}}, 3600)
    store.close()
    reopened = Store(path)
    assert reopened.get("a")["status"] == "interrupted"
    assert reopened.cache("key-b")["id"] == "b"
    reopened.close()


@pytest.mark.parametrize(
    "headers,allowed,status",
    [
        ({"Origin": "https://audit.example", "Sec-Fetch-Site": "same-origin"}, (), 202),
        ({"Origin": "https://audit.example", "X-Forwarded-Host": "audit.example"}, (), 403),
        ({"Origin": "https://audit.example"}, ("https://audit.example",), 202),
        ({"Origin": "https://audit.example:443"}, ("https://audit.example",), 202),
        ({"Origin": "https://audit.example:8443"}, ("https://audit.example",), 403),
        ({"Origin": "https://audit.example.attacker.test"}, ("https://audit.example",), 403),
        ({"Origin": "https://other.test", "Sec-Fetch-Site": "cross-site"}, (), 403),
        ({"Origin": "https://sibling.test", "Sec-Fetch-Site": "same-site"}, (), 403),
        ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, (), 403),
        ({"Origin": "http://testserver:80"}, (), 202),
        ({"Sec-Fetch-Site": "cross-site"}, (), 403),
        ({}, (), 202),
    ],
)
def test_request_origin_through_reverse_proxy(tmp_path, headers, allowed, status):
    settings = Settings(db_path=tmp_path / "origins.db", allowed_origins=allowed)
    with TestClient(create_app(settings, FakeAnalyzer())) as client:
        response = client.post("/api/analyses", json={"regid": "ru.ecode"}, headers=headers)
        assert response.status_code == status
        if status == 403:
            assert "origin" in response.json()["detail"]


@pytest.mark.parametrize(
    "origin", ["*", "null", "https://audit.example/path", "https://user@audit.example"]
)
def test_invalid_public_origins_are_rejected(origin):
    with pytest.raises(ValueError, match="AUDIT_ALLOWED_ORIGINS"):
        Settings(allowed_origins=(origin,))
