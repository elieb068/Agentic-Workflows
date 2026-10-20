"""newsletter issues (public archive)

Revision ID: c4f2d1e3b5a6
Revises: b3e1c0d2a4f5
Create Date: 2026-10-20 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = 'c4f2d1e3b5a6'
down_revision: str | None = 'b3e1c0d2a4f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'newsletter_issues',
        sa.Column('edition', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('preheader', sa.String(length=300), server_default='', nullable=False),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('publish_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('edition', name=op.f('pk_newsletter_issues')),
    )
    op.create_index(op.f('ix_newsletter_issues_publish_at'), 'newsletter_issues', ['publish_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_newsletter_issues_publish_at'), table_name='newsletter_issues')
    op.drop_table('newsletter_issues')
