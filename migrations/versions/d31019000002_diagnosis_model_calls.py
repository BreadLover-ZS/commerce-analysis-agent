"""Persist model attempt identity, reserved budget, output and usage.

Revision ID: d31019000002
Revises: d31019000001
"""

import sqlalchemy as sa
from alembic import op

revision = "d31019000002"
down_revision = "d31019000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_invocations",
        sa.Column("call_id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("validated_output", sa.JSON(), nullable=True),
        sa.Column("response_digest", sa.String(64), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("usage_complete", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "task_id", "step_id", "phase", "attempt", name="uq_model_invocation_identity"
        ),
    )
    op.create_index("ix_model_invocations_task_id", "model_invocations", ["task_id"])


def downgrade():
    table = sa.table("model_invocations", sa.column("call_id"))
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(table)):
        raise RuntimeError("模型调用账本含数据，禁止自动删除；回滚须保留费用和失败证据")
    op.drop_table("model_invocations")
