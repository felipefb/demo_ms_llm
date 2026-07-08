import uuid


def test_valid_payload_returns_200_with_full_contract(client, mock_llm):
    resp = client.post(
        "/v1/chat",
        json={"user_id": "user-1", "prompt": "Hello there"},
    )
    assert resp.status_code == 200
    body = resp.json()
    uuid.UUID(body["id"])  # valid uuid
    assert body["user_id"] == "user-1"
    assert body["prompt"] == "Hello there"
    assert body["response"] == "mocked response"
    assert body["model"] == "mock-model"
    assert body["provider"] == "mock"
    assert body["status"] == "completed"
    assert body["usage"]["total_tokens"] == 8
    assert "timestamp" in body
    assert isinstance(body["latency_ms"], (int, float))
    assert mock_llm.calls == [{"prompt": "Hello there", "model": None}]


def test_pending_to_completed_flow_persists_record(client, repo):
    resp = client.post("/v1/chat", json={"user_id": "u-flow", "prompt": "hi"})
    assert resp.status_code == 200
    records = list(repo._items.values())
    assert len(records) == 1
    r = records[0]
    assert r.status == "completed"
    assert r.response == "mocked response"
    assert r.provider == "mock"
    assert r.latency_ms is not None
    assert r.prompt_tokens == 3 and r.completion_tokens == 5
    assert r.updated_at >= r.created_at


def test_llm_failure_persists_failed_record_and_returns_503(failing_client, repo):
    resp = failing_client.post("/v1/chat", json={"user_id": "u-fail", "prompt": "hi"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "llm_unavailable"
    assert body["error"]["request_id"]
    # Prompt must never be lost: record persisted with status=failed.
    records = list(repo._items.values())
    assert len(records) == 1
    r = records[0]
    assert r.status == "failed"
    assert r.prompt == "hi"
    assert r.response is None
    assert "simulated provider outage" in r.error_detail
    assert r.latency_ms is not None


def test_model_override_allowed_by_allowlist(client, mock_llm):
    resp = client.post(
        "/v1/chat",
        json={"user_id": "u", "prompt": "hi", "model": "custom-model"},
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "custom-model"
    assert mock_llm.calls[0]["model"] == "custom-model"


def test_model_not_in_allowlist_rejected(client, mock_llm):
    resp = client.post(
        "/v1/chat",
        json={"user_id": "u", "prompt": "hi", "model": "evil-model"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "model_not_allowed"
    assert mock_llm.calls == []


def test_extra_field_rejected(client):
    resp = client.post(
        "/v1/chat",
        json={"user_id": "u", "prompt": "hi", "hacker_field": "x"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_whitespace_only_prompt_rejected(client):
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": "   \n\t  "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_prompt_is_stripped(client):
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": "  hi  "})
    assert resp.status_code == 200
    assert resp.json()["prompt"] == "hi"


def test_metadata_too_many_keys_rejected(client):
    metadata = {f"k{i}": "v" for i in range(21)}
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": "hi", "metadata": metadata})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_metadata_too_large_rejected(client):
    metadata = {"k": "x" * 5000}
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": "hi", "metadata": metadata})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_metadata_non_string_values_rejected(client):
    resp = client.post(
        "/v1/chat",
        json={"user_id": "u", "prompt": "hi", "metadata": {"nested": {"a": 1}}},
    )
    assert resp.status_code == 422


def test_missing_fields_returns_422_envelope(client):
    resp = client.post("/v1/chat", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"]
    fields = {d["field"] for d in body["error"]["details"]}
    assert {"user_id", "prompt"} <= fields


def test_empty_prompt_returns_422(client):
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_prompt_over_limit_returns_422(client):
    resp = client.post("/v1/chat", json={"user_id": "u", "prompt": "x" * 4001})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_request_id_header_is_echoed(client):
    rid = "test-req-id-123"
    resp = client.post(
        "/v1/chat",
        json={"user_id": "u", "prompt": "hi"},
        headers={"X-Request-ID": rid},
    )
    assert resp.headers["X-Request-ID"] == rid


def test_request_id_generated_when_absent(client):
    resp = client.get("/health")
    assert resp.headers["X-Request-ID"]


def test_response_time_header_present(client):
    resp = client.get("/health")
    assert float(resp.headers["X-Response-Time-Ms"]) >= 0


def test_conversation_history_paginated_with_status(client):
    for i in range(3):
        client.post("/v1/chat", json={"user_id": "hist-user", "prompt": f"msg {i}"})
    resp = client.get("/v1/conversations/hist-user?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0
    item = body["items"][0]
    assert item["status"] == "completed"
    assert item["provider"] == "mock"


def test_conversation_history_includes_failed_records(failing_client):
    failing_client.post("/v1/chat", json={"user_id": "u-f", "prompt": "lost?"})
    resp = failing_client.get("/v1/conversations/u-f")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["status"] == "failed"
    assert item["response"] is None
    assert item["prompt"] == "lost?"


def test_conversation_history_empty_user(client):
    resp = client.get("/v1/conversations/nobody")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_not_found_uses_error_envelope(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert "request_id" in body["error"]


def test_chat_expoe_resposta_estruturada(client):
    """O campo structured existe e degrada com seguranca (echo devolve texto cru)."""
    r = client.post("/v1/chat", json={"user_id": "u-struct", "prompt": "oi"})
    assert r.status_code == 200
    body = r.json()
    assert "structured" in body
    assert body["structured"]["normalizada"] is False  # mock nao fala JSON
    assert body["structured"]["resposta"] == body["response"]
    assert body["structured"]["dados"] == []
