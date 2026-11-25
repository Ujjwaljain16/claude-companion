"""bridge request claims

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("session_tasks") as b:
        b.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("claimed_by", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("session_tasks") as b:
        b.drop_column("claimed_by")
        b.drop_column("claimed_at")
