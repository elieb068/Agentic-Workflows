"""brevo newsletter sync

Revision ID: b3e1c0d2a4f5
Revises: 87ebfa9cb32e
Create Date: 2026-10-19 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b3e1c0d2a4f5'
down_revision: str | None = '87ebfa9cb32e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('brevo_synced', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_table(
        'brevo_removals',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_brevo_removals')),
    )


def downgrade() -> None:
    op.drop_table('brevo_removals')
    op.drop_column('leads', 'brevo_synced')
