"""create interactions table

Revision ID: 0001
Revises:
Create Date: 2026-07-06

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

interaction_status = postgresql.ENUM(
    "pending", "completed", "failed", name="interaction_status", create_type=False
)


def upgrade() -> None:
    interaction_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("status", interaction_status, nullable=False, server_default="pending"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_interactions_user_id", "interactions", ["user_id"])
    op.create_index(
        "ix_interactions_user_id_created_at",
        "interactions",
        ["user_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_interactions_user_id_created_at", table_name="interactions")
    op.drop_index("ix_interactions_user_id", table_name="interactions")
    op.drop_table("interactions")
    interaction_status.drop(op.get_bind(), checkfirst=True)
