"""Coverage-gap tests added in Fase 4 (quality gate).

Covers, without any network or database:
- /ready degraded paths (DB error) and the health-check helpers directly;
- OpenTelemetry setup (console exporter) and the start_span helper;
- PostgresConversationRepository logic via a fake async session (the real
  SQL path is exercised by the testcontainers integration tests);
- engine/session factory helpers;
- the dedicated rate-limit rejection counter;
- POST without Content-Type -> 422 (documented decision; explicit non-JSON
  Content-Type -> 415 is covered in test_security.py).
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.api.health import _check_database, _check_llm_egress
from app.core.config import get_settings
from app.models.interaction import Interaction
from app.repositories.conversations import RecordNotFoundError
from app.repositories.database import create_engine, create_session_factory
from app.repositories.postgres import PostgresConversationRepository

# --------------------------------------------------------------------------
# Health-check helpers
# --------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, fail: bool):
        self._fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        if self._fail:
            raise RuntimeError("db down")


class _FakeEngine:
    def __init__(self, fail: bool = False):
        self._fail = fail

    def connect(self):
        return _FakeConn(self._fail)


def _request_with_state(**state) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


async def test_check_database_ok_and_error():
    assert await _check_database(_request_with_state(db_engine=_FakeEngine())) == "ok"
    assert await _check_database(_request_with_state(db_engine=_FakeEngine(fail=True))) == "error"
    assert await _check_database(_request_with_state(db_engine=None)) == "skipped"


class _FakeHTTPClient:
    def __init__(self, status_code: int | None = 200):
        self.status_code = status_code

    async def get(self, url, timeout=None):
        if self.status_code is None:
            raise httpx.ConnectError("no egress")
        return SimpleNamespace(status_code=self.status_code)


async def test_check_llm_egress_paths(monkeypatch):
    # No real key configured (conftest) -> skipped regardless of client.
    req = _request_with_state(http_client=_FakeHTTPClient())
    assert await _check_llm_egress(req) == "skipped"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-real-key")
    get_settings.cache_clear()
    assert await _check_llm_egress(_request_with_state(http_client=_FakeHTTPClient(200))) == "ok"
    assert await _check_llm_egress(_request_with_state(http_client=_FakeHTTPClient(503))) == "error"
    assert (
        await _check_llm_egress(_request_with_state(http_client=_FakeHTTPClient(None))) == "error"
    )
    assert await _check_llm_egress(_request_with_state(http_client=None)) == "skipped"
    get_settings.cache_clear()


def test_ready_degraded_when_db_fails(client):
    client.app.state.db_engine = _FakeEngine(fail=True)
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "error"


# --------------------------------------------------------------------------
# Tracing
# --------------------------------------------------------------------------


def test_setup_tracing_console_exporter(monkeypatch):
    import app.core.tracing as tracing

    monkeypatch.setenv("OTEL_ENABLED", "true")
    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(tracing, "_tracer", None)  # restored automatically

    tracing.setup_tracing(settings, app=None, engine=None, http_client=None)
    assert tracing._tracer is not None
    with tracing.start_span("unit-test-span", user_id="u1", skipped=None):
        pass
    # Flush/stop the BatchSpanProcessor so its atexit hook does not try to
    # write to pytest's already-closed captured stdout.
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    get_settings.cache_clear()


def test_start_span_noop_when_disabled(monkeypatch):
    import app.core.tracing as tracing

    monkeypatch.setattr(tracing, "_tracer", None)
    with tracing.start_span("noop"):
        pass


def test_setup_tracing_disabled_is_noop(monkeypatch):
    import app.core.tracing as tracing

    monkeypatch.setattr(tracing, "_tracer", None)
    settings = get_settings()  # OTEL_ENABLED defaults to false in tests
    tracing.setup_tracing(settings)
    assert tracing._tracer is None


# --------------------------------------------------------------------------
# Postgres repository (fake session; real SQL covered by testcontainers)
# --------------------------------------------------------------------------


class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal async-session stand-in backed by a shared dict."""

    def __init__(self, store: dict):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self  # reuses __aenter__/__aexit__

    def add(self, row: Interaction) -> None:
        now = datetime.now(UTC) + timedelta(microseconds=len(self._store))
        row.created_at = now
        row.updated_at = now
        self._store[row.id] = row

    async def refresh(self, row: Interaction) -> None:
        row.updated_at = datetime.now(UTC)

    async def get(self, model, record_id):
        return self._store.get(record_id)

    async def scalar(self, stmt):
        return len(self._store)

    async def scalars(self, stmt):
        rows = sorted(self._store.values(), key=lambda r: r.created_at, reverse=True)
        return _FakeScalarsResult(rows)


@pytest.fixture
def pg_repo():
    store: dict = {}
    return PostgresConversationRepository(lambda: _FakeSession(store))


async def test_postgres_repo_full_lifecycle(pg_repo):
    rec = await pg_repo.create_pending("user-1", "hello", model="m1", metadata={"k": "v"})
    assert rec.status == "pending"
    assert rec.metadata == {"k": "v"}

    done = await pg_repo.mark_completed(
        rec.id,
        "answer",
        model="m1",
        provider="openrouter",
        latency_ms=12.6,
        prompt_tokens=3,
        completion_tokens=5,
    )
    assert done.status == "completed"
    assert done.latency_ms == 13  # rounded to whole ms
    assert done.response == "answer"

    rec2 = await pg_repo.create_pending("user-1", "boom")
    failed = await pg_repo.mark_failed(rec2.id, "provider outage", latency_ms=7.4)
    assert failed.status == "failed"
    assert failed.error_detail == "provider outage"
    assert failed.latency_ms == 7

    items, total = await pg_repo.list_by_user("user-1", limit=10, offset=0)
    assert total == 2
    assert [i.id for i in items] == [rec2.id, rec.id]  # newest first


async def test_postgres_repo_not_found(pg_repo):
    with pytest.raises(RecordNotFoundError):
        await pg_repo.mark_completed(uuid.uuid4(), "x", model="m", provider="p", latency_ms=1.0)
    with pytest.raises(RecordNotFoundError):
        await pg_repo.mark_failed(uuid.uuid4(), "err", latency_ms=1.0)


def test_engine_and_session_factory_helpers():
    settings = get_settings()
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)
        assert factory is not None
    finally:
        # No connection was opened; sync dispose of the pool object only.
        engine.sync_engine.dispose()


# --------------------------------------------------------------------------
# Rate-limit rejection counter + Content-Type decision
# --------------------------------------------------------------------------


def test_rate_limit_rejection_increments_counter(monkeypatch, mock_llm, repo):
    from app.core.metrics import rate_limit_rejections_total
    from tests.conftest import make_client

    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    get_settings.cache_clear()
    client = make_client(mock_llm, repo)

    payload = {"user_id": "u1", "prompt": "hi"}
    assert client.post("/v1/chat", json=payload).status_code == 200
    resp = client.post("/v1/chat", json=payload)
    assert resp.status_code == 429
    value = rate_limit_rejections_total.labels(path="/v1/chat")._value.get()
    assert value >= 1


def test_post_without_content_type_returns_422(client):
    """Documented decision: absent Content-Type is treated as a JSON attempt.

    FastAPI parses the body as JSON; malformed input yields 422. Only an
    explicit non-JSON Content-Type yields 415 (BodyLimitMiddleware).
    """
    resp = client.post("/v1/chat", content=b"not-json", headers={"Content-Type": ""})
    assert resp.status_code == 422
