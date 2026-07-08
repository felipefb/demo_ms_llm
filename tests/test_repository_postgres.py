"""Integration tests for PostgresConversationRepository against a real
PostgreSQL 16 (testcontainers).

Skipped automatically when Docker (or the testcontainers package) is not
available, so the default suite stays green offline. To run them:

    pip install "testcontainers[postgres]"
    pytest tests/test_repository_postgres.py  # requires a running Docker daemon

The schema is applied via the Alembic migration (alembic upgrade head), so
these tests also validate the initial migration.
"""

import os
import shutil
import subprocess
import sys
import uuid

import pytest

try:
    from testcontainers.postgres import PostgresContainer

    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (HAS_TESTCONTAINERS and _docker_available()),
    reason="requires Docker daemon and testcontainers[postgres] (offline-safe skip)",
)


@pytest.fixture(scope="module")
def pg_url():
    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        # Apply the real Alembic migration against the container.
        env = {"DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env={**os.environ, **env},
        )
        yield url


@pytest.fixture
async def pg_repo(pg_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.repositories.postgres import PostgresConversationRepository

    engine = create_async_engine(pg_url)
    repo = PostgresConversationRepository(async_sessionmaker(engine, expire_on_commit=False))
    yield repo
    await engine.dispose()


async def test_pg_pending_to_completed(pg_repo):
    rec = await pg_repo.create_pending("u1", "hello", model="m", metadata={"k": "v"})
    assert rec.status == "pending"
    assert rec.response is None

    done = await pg_repo.mark_completed(
        rec.id,
        response="hi",
        model="m",
        provider="openrouter",
        latency_ms=99.7,
        prompt_tokens=3,
        completion_tokens=5,
    )
    assert done.status == "completed"
    assert done.latency_ms == 100
    assert done.metadata == {"k": "v"}


async def test_pg_pending_to_failed(pg_repo):
    rec = await pg_repo.create_pending("u2", "keep me")
    failed = await pg_repo.mark_failed(rec.id, error_detail="outage", latency_ms=5.2)
    assert failed.status == "failed"
    assert failed.prompt == "keep me"
    assert failed.error_detail == "outage"
    assert failed.latency_ms == 5


async def test_pg_unknown_id_raises(pg_repo):
    from app.repositories.conversations import RecordNotFoundError

    with pytest.raises(RecordNotFoundError):
        await pg_repo.mark_failed(uuid.uuid4(), error_detail="x", latency_ms=1)


async def test_pg_list_by_user_pagination(pg_repo):
    user = f"u-{uuid.uuid4()}"
    for i in range(5):
        await pg_repo.create_pending(user, f"p{i}")

    items, total = await pg_repo.list_by_user(user, limit=2, offset=0)
    assert total == 5
    assert len(items) == 2
    # newest first (created_at DESC)
    assert items[0].created_at >= items[1].created_at
