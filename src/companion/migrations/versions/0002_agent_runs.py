"""agent runs: trajectories on results, tags on usage

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("eval_results") as b:
        b.add_column(sa.Column("trajectory", sa.JSON(), nullable=True))
        b.add_column(sa.Column("agent_meta", sa.JSON(), nullable=True))
    with op.batch_alter_table("usage_records") as b:
        b.add_column(sa.Column("tag", sa.String(length=120), nullable=True))
        b.create_index("ix_usage_records_tag", ["tag"])


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as b:
        b.drop_index("ix_usage_records_tag")
        b.drop_column("tag")
    with op.batch_alter_table("eval_results") as b:
        b.drop_column("agent_meta")
        b.drop_column("trajectory")
