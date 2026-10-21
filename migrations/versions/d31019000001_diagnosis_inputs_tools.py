"""Immutable diagnosis inputs and metered, versioned tool results.

Revision ID: d31019000001
Revises: c21a01600001
"""

import sqlalchemy as sa
from alembic import op

revision = "d31019000001"
down_revision = "c21a01600001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_input_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column(
            "sales_dataset_id", sa.String(36), sa.ForeignKey("commerce_datasets.id"), nullable=False
        ),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "bundle_hash", name="uq_analysis_bundle_content"),
    )
    op.create_index("ix_analysis_input_bundles_user_id", "analysis_input_bundles", ["user_id"])
    op.create_table(
        "tool_query_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("query_spec", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "spec_hash", name="uq_tool_query_spec"),
    )
    op.create_index("ix_tool_query_results_task_id", "tool_query_results", ["task_id"])
    op.create_table(
        "tool_invocations",
        sa.Column("call_id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "query_result_id", sa.String(36), sa.ForeignKey("tool_query_results.id"), nullable=False
        ),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("task_id", "spec_hash", "attempt", name="uq_tool_invocation_attempt"),
    )
    op.create_index("ix_tool_invocations_task_id", "tool_invocations", ["task_id"])


def downgrade():
    # Operational rollback disables the new entry but preserves referenced evidence.
    # Only an empty, disposable database may remove these tables automatically.
    db = op.get_bind()
    for name in ("tool_invocations", "tool_query_results", "analysis_input_bundles"):
        table = sa.table(name, sa.column("id" if name != "tool_invocations" else "call_id"))
        if db.scalar(sa.select(sa.func.count()).select_from(table)):
            raise RuntimeError(
                "诊断表含数据，禁止自动删除；请停用入口并保留证据，按备份恢复流程回滚"
            )
    op.drop_table("tool_invocations")
    op.drop_table("tool_query_results")
    op.drop_table("analysis_input_bundles")
