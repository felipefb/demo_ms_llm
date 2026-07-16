"""add 'blocked' to interaction_status (guardrail de escopo temático)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-15

"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE não pode rodar dentro da transação que criou o
    # tipo; o autocommit_block garante execução fora da transação da migration.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE interaction_status ADD VALUE IF NOT EXISTS 'blocked'")


def downgrade() -> None:
    # PostgreSQL não suporta remover um valor de enum. O valor extra é
    # inofensivo para versões anteriores do código (nenhuma linha nova é
    # criada com ele após o downgrade da aplicação).
    pass
