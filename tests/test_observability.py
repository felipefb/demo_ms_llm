"""Observability: /metrics, JSON access log with request_id, ASGI middleware."""

import json

from app.core import metrics as m
from app.core.logging import setup_logging


class TestMetricsEndpoint:
    def test_metrics_endpoint_exposes_custom_metrics(self, client):
        # Generate traffic first so HTTP series exist.
        r = client.post("/v1/chat", json={"user_id": "u1", "prompt": "hello metrics"})
        assert r.status_code == 200

        body = client.get("/metrics").text
        for name in (
            "http_requests_total",
            "http_request_duration_seconds",
            "llm_requests_total",
            "llm_latency_seconds",
            "llm_fallback_total",
            "llm_tokens_total",
            "circuit_breaker_state",
        ):
            assert f"# TYPE {name}" in body, f"missing metric {name}"
        # The chat request above must be counted with the route template.
        assert 'http_requests_total{method="POST",path="/v1/chat",status="200"}' in body

    def test_unmatched_paths_do_not_create_raw_path_series(self, client):
        """404s and 401s (pre-routing) must use the fixed 'unmatched' label.

        Otherwise an unauthenticated scanner could mint unbounded series and
        reflect arbitrary paths on the public /metrics output.
        """
        scanner_path = "/definitely-not-a-route-abc123"
        assert client.get(scanner_path).status_code == 404
        # 401: auth middleware rejects before routing (no matched route).
        unauth_path = "/v1/chat-scan-xyz"
        r = client.post(unauth_path, headers={"X-API-Key": "wrong-key"}, json={})
        assert r.status_code in (401, 404)

        body = client.get("/metrics").text
        assert scanner_path not in body
        assert unauth_path not in body
        assert 'path="unmatched"' in body

    async def test_llm_business_metrics_and_breaker_gauge(self, mock_llm):
        from app.services.resilience import ResilientLLMClient

        mock_llm.provider = "mock"
        chain = ResilientLLMClient([mock_llm])

        counter = m.llm_requests_total.labels(
            provider="mock", model="mock-model", outcome="success"
        )
        latency_count = m.llm_latency_seconds.labels(provider="mock")
        before = counter._value.get()
        result = await chain.generate("hi")
        assert result.provider == "mock"
        assert counter._value.get() == before + 1
        assert latency_count._sum.get() >= 0
        # Token usage exposed as a counter (total_tokens not persisted in DB).
        assert (
            m.llm_tokens_total.labels(
                provider="mock", model="mock-model", kind="prompt"
            )._value.get()
            > 0
        )
        # Breaker gauge: closed => 0.
        assert m.circuit_breaker_state.labels(provider="mock")._value.get() == 0


class TestAccessLog:
    def test_access_log_is_json_with_request_id_and_no_body(self, client, capsys):
        setup_logging("INFO", "json")
        rid = "test-rid-123"
        prompt = "super secret prompt"
        r = client.post(
            "/v1/chat",
            json={"user_id": "u1", "prompt": prompt},
            headers={"X-Request-ID": rid},
        )
        assert r.status_code == 200
        assert r.headers["X-Request-ID"] == rid
        assert "X-Response-Time-Ms" in r.headers

        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        access = [
            json.loads(ln)
            for ln in lines
            if ln.startswith("{") and json.loads(ln).get("event") == "http_request"
        ]
        assert access, "no JSON access log line emitted"
        entry = access[-1]
        assert entry["request_id"] == rid
        assert entry["status_code"] == 200
        assert entry["route"] == "/v1/chat"
        assert entry["latency_ms"] >= 0
        # Privacy: body never logged — only size and sha256.
        assert prompt not in json.dumps(entry)
        assert entry["body_bytes"] > 0
        assert len(entry["body_sha256"]) == 64


class TestMiddlewareOnUnhandledException:
    def test_request_id_header_present_on_unhandled_exception(self, mock_llm, repo, capsys):
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app()
        app.state.llm_client = mock_llm
        app.state.repository = repo

        @app.get("/__boom")
        async def boom():
            raise RuntimeError("unhandled boom")

        from tests.conftest import TEST_API_KEY

        client = TestClient(app, raise_server_exceptions=False, headers={"X-API-Key": TEST_API_KEY})
        r = client.get("/__boom", headers={"X-Request-ID": "rid-err"})
        assert r.status_code == 500
        # Pure-ASGI middleware guarantees the headers even on unhandled errors.
        assert r.headers["X-Request-ID"] == "rid-err"
        assert "X-Response-Time-Ms" in r.headers
        assert r.json()["error"]["code"] == "internal_error"
