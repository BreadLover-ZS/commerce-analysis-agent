"""Version routing, durable diagnostic steps and typed human interrupts.

Revision ID: d31019000003
Revises: d31019000002
"""

import sqlalchemy as sa
from alembic import op

revision = "d31019000003"
down_revision = "d31019000002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("bundle_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("parent_task_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_task_bundle", "analysis_input_bundles", ["bundle_id"], ["id"])
        batch.create_foreign_key("fk_task_parent", "tasks", ["parent_task_id"], ["id"])
        for name, default in (
            ("graph_version", "research-v1"),
            ("schema_version", "1"),
            ("prompt_version", "legacy-v1"),
            ("tool_registry_version", "legacy-v1"),
        ):
            batch.add_column(sa.Column(name, sa.String(40), nullable=False, server_default=default))
        for name in ("state_version", "report_version", "recovery_attempts"):
            batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("report_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("pending_interrupt_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("waiting_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("execution_started_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("report_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("report_hash", sa.String(64), nullable=True))
    op.create_table(
        "diagnosis_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("step_id", sa.String(80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("validated_args", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model_call_id", sa.String(36), nullable=True),
        sa.Column("call_id", sa.String(36), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "step_id", name="uq_diagnosis_step"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_diagnosis_sequence"),
    )
    op.create_index("ix_diagnosis_steps_task_id", "diagnosis_steps", ["task_id"])
    op.create_table(
        "diagnostic_findings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("hypothesis_code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_diagnostic_findings_task_id", "diagnostic_findings", ["task_id"])
    op.create_table(
        "input_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("interrupt_key", sa.String(80), nullable=False),
        sa.Column("interrupt_type", sa.String(24), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=True),
        sa.Column("waiting_until", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "interrupt_key", name="uq_input_request_key"),
    )
    op.create_index("ix_input_requests_task_id", "input_requests", ["task_id"])
    op.create_table(
        "task_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "finding_id", sa.String(40), sa.ForeignKey("diagnostic_findings.id"), nullable=True
        ),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_task_feedback_task_id", "task_feedback", ["task_id"])


def downgrade():
    db = op.get_bind()
    for name in ("task_feedback", "input_requests", "diagnostic_findings", "diagnosis_steps"):
        if db.scalar(sa.select(sa.func.count()).select_from(sa.table(name))):
            raise RuntimeError("诊断运行表含数据，禁止自动删除；请保留等待、调用和报告记录")
    tasks = sa.table("tasks", sa.column("bundle_id"))
    if db.scalar(
        sa.select(sa.func.count()).select_from(tasks).where(tasks.c.bundle_id.is_not(None))
    ):
        raise RuntimeError("存在诊断任务，禁止删除版本与输入绑定")
    for name in ("task_feedback", "input_requests", "diagnostic_findings", "diagnosis_steps"):
        op.drop_table(name)
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("report_version")
        batch.drop_column("report_hash")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_task_bundle", type_="foreignkey")
        batch.drop_constraint("fk_task_parent", type_="foreignkey")
        for name in (
            "bundle_id",
            "parent_task_id",
            "graph_version",
            "schema_version",
            "prompt_version",
            "tool_registry_version",
            "state_version",
            "report_version",
            "report_hash",
            "pending_interrupt_id",
            "waiting_until",
            "execution_started_at",
            "recovery_attempts",
        ):
            batch.drop_column(name)
