"""Security tests: auth (401), rate limit (429 + Retry-After), body limits
(413/415), security headers, and config fail-fast for API_KEY_HASH."""

import pytest

from app.core.auth import hash_api_key, mask_key
from app.core.config import Settings, get_settings
from tests.conftest import TEST_API_KEY, make_client

CHAT_PAYLOAD = {"user_id": "u1", "prompt": "hello"}


# ---------------------------------------------------------------- auth


def test_request_without_api_key_returns_401(client):
    resp = client.post("/v1/chat", json=CHAT_PAYLOAD, headers={"X-API-Key": ""})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert "request_id" in body["error"]


def test_request_with_wrong_api_key_returns_401(client):
    resp = client.post("/v1/chat", json=CHAT_PAYLOAD, headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_conversations_requires_api_key(client):
    resp = client.get("/v1/conversations/u1", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_valid_api_key_passes(client):
    resp = client.post("/v1/chat", json=CHAT_PAYLOAD)  # default header from make_client
    assert resp.status_code == 200


def test_health_and_ready_are_public(client):
    assert client.get("/health", headers={"X-API-Key": ""}).status_code == 200
    # /ready may be 200 or 503 depending on state, but never 401.
    assert client.get("/ready", headers={"X-API-Key": ""}).status_code != 401


def test_hash_and_mask_helpers():
    assert hash_api_key(TEST_API_KEY) == get_settings().api_key_hash
    assert TEST_API_KEY not in mask_key(TEST_API_KEY)
    assert mask_key("") == "<empty>"


# ---------------------------------------------------------- rate limiting


def test_rate_limit_returns_429_with_retry_after(monkeypatch, mock_llm, repo):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "3")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()
    client = make_client(mock_llm, repo)
    for _ in range(3):
        assert client.post("/v1/chat", json=CHAT_PAYLOAD).status_code == 200
    resp = client.post("/v1/chat", json=CHAT_PAYLOAD)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1


def test_rate_limit_skips_public_paths(monkeypatch, mock_llm, repo):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    get_settings.cache_clear()
    client = make_client(mock_llm, repo)
    for _ in range(5):
        assert client.get("/health").status_code == 200


# ------------------------------------------------------- body/input limits


def test_oversized_body_returns_413(client):
    huge = "x" * 100_000  # > MAX_BODY_BYTES (64 KiB)
    resp = client.post("/v1/chat", json={"user_id": "u1", "prompt": huge})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_prompt_over_schema_limit_returns_422(client):
    resp = client.post("/v1/chat", json={"user_id": "u1", "prompt": "x" * 5000})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_unexpected_content_type_returns_415(client):
    resp = client.post("/v1/chat", content=b"user_id=u1", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


def test_extra_fields_rejected(client):
    resp = client.post("/v1/chat", json={**CHAT_PAYLOAD, "role": "admin"})
    assert resp.status_code == 422


# --------------------------------------------------------- security headers


def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert resp.headers["Cache-Control"] == "no-store"
    assert "content-security-policy" in resp.headers


# ------------------------------------------------------------- config


def test_prod_requires_api_key_hash(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("API_KEY_HASH", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "real-key")
    with pytest.raises(Exception, match="API_KEY_HASH"):
        Settings(_env_file=None)


def test_invalid_api_key_hash_rejected(monkeypatch):
    monkeypatch.setenv("API_KEY_HASH", "not-a-sha256")
    with pytest.raises(Exception, match="SHA-256"):
        Settings(_env_file=None)
