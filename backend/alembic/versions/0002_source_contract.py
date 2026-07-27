"""Create source and source segment tables.

Revision ID: 0002_source_contract
Revises: 0001_initial_runtime
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_source_contract"
down_revision: str | None = "0001_initial_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_sha256", name="uq_sources_content_sha256"),
    )
    op.create_index("ix_sources_created_at", "sources", ["created_at"], unique=False)
    op.create_table(
        "source_segments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "position",
            name="uq_source_segments_position",
        ),
    )
    op.create_index(
        "ix_source_segments_source_id",
        "source_segments",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_source_segments_source_id", table_name="source_segments")
    op.drop_table("source_segments")
    op.drop_index("ix_sources_created_at", table_name="sources")
    op.drop_table("sources")
