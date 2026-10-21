"""Immutable inventory versions and local document manifests.

Revision ID: d31019000004
Revises: d31019000003
"""

import sqlalchemy as sa
from alembic import op

revision = "d31019000004"
down_revision = "d31019000003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_datasets",
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
        sa.UniqueConstraint(
            "user_id", "content_hash", "sample", name="uq_inventory_dataset_content"
        ),
    )
    op.create_index("ix_inventory_datasets_user_id", "inventory_datasets", ["user_id"])
    op.create_table(
        "document_manifests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=True
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("sample", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_document_manifest_content"),
    )
    op.create_index("ix_document_manifests_user_id", "document_manifests", ["user_id"])
    with op.batch_alter_table("analysis_input_bundles") as batch:
        batch.add_column(sa.Column("inventory_dataset_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("document_manifest_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_inventory", "inventory_datasets", ["inventory_dataset_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_analysis_manifest", "document_manifests", ["document_manifest_id"], ["id"]
        )


def downgrade():
    db = op.get_bind()
    for name in ("inventory_datasets", "document_manifests"):
        if db.scalar(sa.select(sa.func.count()).select_from(sa.table(name))):
            raise RuntimeError("版本化来源含数据，禁止自动删除；须保留输入与历史证据")
    with op.batch_alter_table("analysis_input_bundles") as batch:
        batch.drop_constraint("fk_analysis_inventory", type_="foreignkey")
        batch.drop_constraint("fk_analysis_manifest", type_="foreignkey")
        batch.drop_column("inventory_dataset_id")
        batch.drop_column("document_manifest_id")
    op.drop_table("document_manifests")
    op.drop_table("inventory_datasets")
