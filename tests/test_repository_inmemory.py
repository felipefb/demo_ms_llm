"""Repository-level tests for the pending -> completed | failed flow (in-memory)."""

import pytest

from app.repositories.conversations import (
    InMemoryConversationRepository,
    RecordNotFoundError,
)


async def test_pending_to_completed_flow(repo: InMemoryConversationRepository):
    rec = await repo.create_pending("u1", "hello", model="m")
    assert rec.status == "pending"
    assert rec.response is None
    assert rec.created_at is not None

    done = await repo.mark_completed(
        rec.id,
        response="hi",
        model="m",
        provider="mock",
        latency_ms=123.6,
        prompt_tokens=1,
        completion_tokens=2,
    )
    assert done.status == "completed"
    assert done.response == "hi"
    # latency is stored as whole milliseconds (int), rounded.
    assert done.latency_ms == 124
    assert isinstance(done.latency_ms, int)
    assert done.updated_at >= done.created_at


async def test_pending_to_failed_preserves_prompt(repo: InMemoryConversationRepository):
    rec = await repo.create_pending("u1", "keep me")
    failed = await repo.mark_failed(rec.id, error_detail="boom", latency_ms=10.2)
    assert failed.status == "failed"
    assert failed.prompt == "keep me"
    assert failed.error_detail == "boom"
    assert failed.latency_ms == 10


async def test_mark_unknown_id_raises(repo: InMemoryConversationRepository):
    import uuid

    with pytest.raises(RecordNotFoundError):
        await repo.mark_failed(uuid.uuid4(), error_detail="x", latency_ms=1)


async def test_list_by_user_pagination(repo: InMemoryConversationRepository):
    for i in range(5):
        await repo.create_pending("u1", f"p{i}")
    await repo.create_pending("u2", "other")

    items, total = await repo.list_by_user("u1", limit=2, offset=0)
    assert total == 5
    assert len(items) == 2
    items2, _ = await repo.list_by_user("u1", limit=2, offset=4)
    assert len(items2) == 1
