"""PostgreSQL implementation of the ConversationRepository protocol.

Each method uses a short-lived session/transaction from the injected
sessionmaker, so the repository is safe to share across requests.
latency_ms is rounded to whole milliseconds (column is INTEGER).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.interaction import Interaction, InteractionStatus
from app.repositories.conversations import ConversationRecord, RecordNotFoundError


def _to_record(row: Interaction) -> ConversationRecord:
    return ConversationRecord(
        id=row.id,
        user_id=row.user_id,
        prompt=row.prompt,
        status=row.status.value,
        response=row.response,
        model=row.model,
        provider=row.provider,
        error_detail=row.error_detail,
        latency_ms=row.latency_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        metadata=row.extra_metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresConversationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _get(self, session: AsyncSession, record_id: uuid.UUID) -> Interaction:
        row = await session.get(Interaction, record_id)
        if row is None:
            raise RecordNotFoundError(str(record_id))
        return row

    async def create_pending(
        self,
        user_id: str,
        prompt: str,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> ConversationRecord:
        async with self._session_factory() as session:
            async with session.begin():
                row = Interaction(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    prompt=prompt,
                    model=model,
                    extra_metadata=metadata,
                    status=InteractionStatus.pending,
                )
                session.add(row)
            await session.refresh(row)
            return _to_record(row)

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
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._get(session, record_id)
                row.status = InteractionStatus.completed
                row.response = response
                row.model = model
                row.provider = provider
                row.latency_ms = round(latency_ms)
                row.prompt_tokens = prompt_tokens
                row.completion_tokens = completion_tokens
            await session.refresh(row)
            return _to_record(row)

    async def mark_failed(
        self, record_id: uuid.UUID, error_detail: str, latency_ms: float
    ) -> ConversationRecord:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._get(session, record_id)
                row.status = InteractionStatus.failed
                row.error_detail = error_detail
                row.latency_ms = round(latency_ms)
            await session.refresh(row)
            return _to_record(row)

    async def list_by_user(
        self, user_id: str, limit: int, offset: int
    ) -> tuple[list[ConversationRecord], int]:
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(Interaction).where(Interaction.user_id == user_id)
            )
            result = await session.scalars(
                select(Interaction)
                .where(Interaction.user_id == user_id)
                .order_by(Interaction.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [_to_record(r) for r in result.all()], int(total or 0)
