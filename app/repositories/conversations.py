"""Conversation repository interface + in-memory implementation.

Flow contract (pending -> completed | failed): the prompt is persisted BEFORE
the LLM call (create_pending) and updated afterwards (mark_completed /
mark_failed), so a prompt is never lost even when all providers fail.

The PostgreSQL implementation (Fase 2) must implement the same
ConversationRepository protocol.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

Status = Literal["pending", "completed", "failed", "blocked"]


@dataclass
class ConversationRecord:
    id: uuid.UUID
    user_id: str
    prompt: str
    status: Status
    created_at: datetime
    updated_at: datetime
    response: str | None = None
    model: str | None = None
    provider: str | None = None
    error_detail: str | None = None
    # Stored as whole milliseconds (INTEGER column); float inputs are rounded
    # at the persistence boundary in every implementation.
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    metadata: dict | None = field(default=None)


class RecordNotFoundError(Exception):
    pass


class ConversationRepository(Protocol):
    async def create_pending(
        self,
        user_id: str,
        prompt: str,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> ConversationRecord: ...

    async def mark_completed(
        self,
        record_id: uuid.UUID,
        response: str,
        model: str,
        provider: str,
        latency_ms: float,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> ConversationRecord: ...

    async def mark_failed(
        self, record_id: uuid.UUID, error_detail: str, latency_ms: float
    ) -> ConversationRecord: ...

    async def mark_blocked(
        self, record_id: uuid.UUID, response: str, reason: str, latency_ms: float
    ) -> ConversationRecord: ...

    async def list_by_user(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[ConversationRecord], int]: ...


class InMemoryConversationRepository:
    """Stores conversations in process memory. For dev/tests only."""

    def __init__(self) -> None:
        self._items: dict[uuid.UUID, ConversationRecord] = {}

    async def create_pending(
        self,
        user_id: str,
        prompt: str,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> ConversationRecord:
        now = datetime.now(UTC)
        record = ConversationRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            prompt=prompt,
            status="pending",
            model=model,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        self._items[record.id] = record
        return record

    def _get(self, record_id: uuid.UUID) -> ConversationRecord:
        try:
            return self._items[record_id]
        except KeyError as exc:
            raise RecordNotFoundError(str(record_id)) from exc

    async def mark_completed(
        self,
        record_id: uuid.UUID,
        response: str,
        model: str,
        provider: str,
        latency_ms: float,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> ConversationRecord:
        record = self._get(record_id)
        record.status = "completed"
        record.response = response
        record.model = model
        record.provider = provider
        record.latency_ms = round(latency_ms)
        record.prompt_tokens = prompt_tokens
        record.completion_tokens = completion_tokens
        record.updated_at = datetime.now(UTC)
        return record

    async def mark_failed(
        self, record_id: uuid.UUID, error_detail: str, latency_ms: float
    ) -> ConversationRecord:
        record = self._get(record_id)
        record.status = "failed"
        record.error_detail = error_detail
        record.latency_ms = round(latency_ms)
        record.updated_at = datetime.now(UTC)
        return record

    async def mark_blocked(
        self, record_id: uuid.UUID, response: str, reason: str, latency_ms: float
    ) -> ConversationRecord:
        record = self._get(record_id)
        record.status = "blocked"
        record.response = response
        record.provider = "guardrail"
        record.error_detail = reason
        record.latency_ms = round(latency_ms)
        record.updated_at = datetime.now(UTC)
        return record

    async def list_by_user(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[ConversationRecord], int]:
        matches = [r for r in self._items.values() if r.user_id == user_id]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[offset : offset + limit], len(matches)
