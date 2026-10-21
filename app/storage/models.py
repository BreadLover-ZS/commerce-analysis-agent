from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    # UTC stored without a timezone for consistent SQLite/PostgreSQL comparisons.
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新建经营分析")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_task_idempotency"),
        Index("ix_task_queue", "status", "lease_until", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(default=0)
    runtime_ms: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    model_calls: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    usage_complete: Mapped[bool] = mapped_column(default=True)
    usage_reports: Mapped[int] = mapped_column(default=0, server_default="0")
    review_decision: Mapped[bool | None] = mapped_column(nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_input_bundles.id"), nullable=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    graph_version: Mapped[str] = mapped_column(
        String(40), default="research-v1", server_default="research-v1"
    )
    schema_version: Mapped[str] = mapped_column(String(40), default="1", server_default="1")
    prompt_version: Mapped[str] = mapped_column(
        String(40), default="legacy-v1", server_default="legacy-v1"
    )
    tool_registry_version: Mapped[str] = mapped_column(
        String(40), default="legacy-v1", server_default="legacy-v1"
    )
    state_version: Mapped[int] = mapped_column(default=0, server_default="0")
    report_version: Mapped[int] = mapped_column(default=0, server_default="0")
    report_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_interrupt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    waiting_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(default=0, server_default="0")


class Event(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("task_id", "source_key", name="uq_evidence_source"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(300))
    locator: Mapped[str] = mapped_column(String(2000))
    excerpt: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("task_id", "name", name="uq_artifact_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    report_version: Mapped[int | None] = mapped_column(nullable=True)
    report_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_product_sku"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80))
    price_cents: Mapped[int] = mapped_column()
    stock: Mapped[int] = mapped_column()
    lead_days: Mapped[int] = mapped_column()
    stock_as_of: Mapped[date] = mapped_column(Date)
    sample: Mapped[bool] = mapped_column(default=True)


class DailySale(Base):
    __tablename__ = "daily_sales"
    __table_args__ = (
        UniqueConstraint("product_id", "day", name="uq_daily_sale"),
        Index("ix_sales_tenant_day", "tenant_id", "day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    day: Mapped[date] = mapped_column(Date)
    paid_units: Mapped[int] = mapped_column()
    refunded_units: Mapped[int] = mapped_column()
    revenue_cents: Mapped[int] = mapped_column()
    refund_cents: Mapped[int] = mapped_column()
    sample: Mapped[bool] = mapped_column(default=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    sample: Mapped[bool] = mapped_column(default=True)


class CommerceDataset(Base):
    """Owner-scoped immutable payload; only draft -> committed is mutable."""

    __tablename__ = "commerce_datasets"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", "sample", name="uq_dataset_content"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64))
    sample: Mapped[bool] = mapped_column()
    committed: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Chunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    locator: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)


class AnalysisInputBundle(Base):
    """Immutable selection of source versions, periods and maximum permissions."""

    __tablename__ = "analysis_input_bundles"
    __table_args__ = (
        UniqueConstraint("user_id", "bundle_hash", name="uq_analysis_bundle_content"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    sales_dataset_id: Mapped[str] = mapped_column(ForeignKey("commerce_datasets.id"))
    inventory_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_datasets.id"), nullable=True
    )
    document_manifest_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_manifests.id"), nullable=True
    )
    bundle_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolQueryResult(Base):
    __tablename__ = "tool_query_results"
    __table_args__ = (UniqueConstraint("task_id", "spec_hash", name="uq_tool_query_spec"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    spec_hash: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="PLANNED")
    query_spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint("task_id", "spec_hash", "attempt", name="uq_tool_invocation_attempt"),
    )
    call_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    query_result_id: Mapped[str] = mapped_column(ForeignKey("tool_query_results.id"))
    step_id: Mapped[str] = mapped_column(String(80))
    spec_hash: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    lease_token: Mapped[str] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ModelInvocation(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "step_id", "phase", "attempt", name="uq_model_invocation_identity"
        ),
    )
    call_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(80))
    phase: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column()
    prompt_hash: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(120))
    lease_token: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16))
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validated_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage_complete: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DiagnosisStep(Base):
    __tablename__ = "diagnosis_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "step_id", name="uq_diagnosis_step"),
        UniqueConstraint("task_id", "sequence", name="uq_diagnosis_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(80))
    sequence: Mapped[int] = mapped_column()
    plan_version: Mapped[int] = mapped_column(default=1)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON)
    validated_args: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    model_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiagnosticFinding(Base):
    __tablename__ = "diagnostic_findings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    hypothesis_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    report_version: Mapped[int] = mapped_column(default=0)


class InputRequest(Base):
    __tablename__ = "input_requests"
    __table_args__ = (UniqueConstraint("task_id", "interrupt_key", name="uq_input_request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    interrupt_key: Mapped[str] = mapped_column(String(80))
    interrupt_type: Mapped[str] = mapped_column(String(24))
    expected_version: Mapped[int] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    waiting_until: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskFeedback(Base):
    __tablename__ = "task_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    finding_id: Mapped[str | None] = mapped_column(
        ForeignKey("diagnostic_findings.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InventoryDataset(Base):
    __tablename__ = "inventory_datasets"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", "sample", name="uq_inventory_dataset_content"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str] = mapped_column(String(64))
    file_hash: Mapped[str] = mapped_column(String(64))
    sample: Mapped[bool] = mapped_column()
    committed: Mapped[bool] = mapped_column(default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentManifest(Base):
    __tablename__ = "document_manifests"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", name="uq_document_manifest_content"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    sample: Mapped[bool] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
