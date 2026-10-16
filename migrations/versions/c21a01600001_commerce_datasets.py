"""Owner-scoped versioned commerce datasets."""

import sqlalchemy as sa
from alembic import op

revision = "c21a01600001"
down_revision = "878d61a878e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "commerce_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("sample", sa.Boolean(), nullable=False),
        sa.Column("committed", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "content_hash", "sample", name="uq_dataset_content"),
    )
    op.create_index("ix_commerce_datasets_user_id", "commerce_datasets", ["user_id"])


def downgrade():
    op.drop_table("commerce_datasets")
