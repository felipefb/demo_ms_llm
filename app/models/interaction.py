"""SQLAlchemy ORM model for the `interactions` table.

Decision (inherited from Fase 1 review): `latency_ms` is stored as INTEGER
(whole milliseconds). Float values measured at the endpoint are rounded at the
persistence boundary (round-half-even via round()). The in-memory repository
applies the same rounding so both implementations behave identically.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    pass


class InteractionStatus(enum.StrEnum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    # Bloqueada pelo guardrail de escopo temático (app/services/guardrail.py):
    # o prompt nunca chegou ao LLM, mas a tentativa fica auditável.
    blocked = "blocked"


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[InteractionStatus] = mapped_column(
        Enum(InteractionStatus, name="interaction_status", native_enum=True),
        nullable=False,
        default=InteractionStatus.pending,
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (Index("ix_interactions_user_id_created_at", "user_id", created_at.desc()),)
