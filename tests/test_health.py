def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"database", "llm_egress"}


def test_openapi_docs_available(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/v1/chat" in schema["paths"]
    assert "/v1/conversations/{user_id}" in schema["paths"]
