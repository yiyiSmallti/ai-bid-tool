from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base


class SandboxArtifact(Base):
    __tablename__ = "sandbox_artifacts"
    sandbox_run_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_record_id: Mapped[UUID] = mapped_column(nullable=False)
    input_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    plaintext_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((kind)::text = ANY ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying, 'rendered_html'::character varying, 'source_pdf'::character varying, 'request_manifest'::character varying, 'capture_archive'::character varying, 'provenance_manifest'::character varying])::text[])))",
            name="sandbox_artifacts_gate_0",
        ),
        CheckConstraint(
            "((((ordinal >= 0) AND (ordinal <= 31)) AND ((size_bytes >= 1) AND (size_bytes <= 67108864))))",
            name="sandbox_artifacts_gate_1",
        ),
        CheckConstraint(
            "((((plaintext_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((provenance_manifest_hash)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="sandbox_artifacts_gate_2",
        ),
        CheckConstraint(
            "(((origin)::text = ANY ((ARRAY['prototype'::character varying, 'public_web_capture'::character varying, 'public_pdf_capture'::character varying])::text[])))",
            name="sandbox_artifacts_gate_3",
        ),
        CheckConstraint(
            "((parent_artifact_id IS DISTINCT FROM id))", name="sandbox_artifacts_gate_4"
        ),
        CheckConstraint(
            "(((((kind)::text = ANY ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying])::text[])) AND (width IS NOT NULL) AND (height IS NOT NULL) AND ((width >= 1) AND (width <= 8192)) AND ((height >= 1) AND (height <= 8192)) AND (((width)::bigint * height) <= 20000000) AND (size_bytes <= 41943040) AND ((media_type)::text = 'image/png'::text)) OR (((kind)::text <> ALL ((ARRAY['prototype_png'::character varying, 'capture_png'::character varying, 'pdf_page_png'::character varying])::text[])) AND (width IS NULL) AND (height IS NULL) AND ((media_type)::text = 'application/octet-stream'::text))))",
            name="sandbox_artifacts_gate_5",
        ),
        CheckConstraint(
            "(((((kind)::text = 'pdf_page_png'::text) AND (page IS NOT NULL) AND (page >= 1)) OR (((kind)::text <> 'pdf_page_png'::text) AND (page IS NULL))))",
            name="sandbox_artifacts_gate_6",
        ),
        CheckConstraint(
            "((((kind)::text <> ALL ((ARRAY['rendered_html'::character varying, 'request_manifest'::character varying, 'provenance_manifest'::character varying])::text[])) OR (size_bytes <= 4194304)))",
            name="sandbox_artifacts_gate_7",
        ),
        CheckConstraint(
            "((((kind)::text <> 'source_pdf'::text) OR (size_bytes <= 33554432)))",
            name="sandbox_artifacts_gate_8",
        ),
        UniqueConstraint(
            "attempt_record_id", "ordinal", name="sandbox_artifacts_attempt_record_id_ordinal_key"
        ),
        UniqueConstraint(
            "id",
            "sandbox_run_id",
            "input_id",
            "task_id",
            "attempt_record_id",
            name="sandbox_artifacts_id_sandbox_run_id_input_id_task_id_key",
        ),
        PrimaryKeyConstraint("id", name="sandbox_artifacts_pkey"),
        ForeignKeyConstraint(
            ["attempt_record_id", "sandbox_run_id", "input_id", "task_id"],
            [
                "sandbox_attempts.id",
                "sandbox_attempts.sandbox_run_id",
                "sandbox_attempts.input_id",
                "sandbox_attempts.task_id",
            ],
            name="sandbox_artifacts_attempt_record_id_sandbox_run_id__fkey",
        ),
        ForeignKeyConstraint(
            ["parent_artifact_id", "sandbox_run_id", "input_id", "task_id", "attempt_record_id"],
            [
                "sandbox_artifacts.id",
                "sandbox_artifacts.sandbox_run_id",
                "sandbox_artifacts.input_id",
                "sandbox_artifacts.task_id",
                "sandbox_artifacts.attempt_record_id",
            ],
            name="sandbox_artifacts_parent_artifact_id_sandbox_run_id_fkey",
        ),
        Index("sandbox_artifacts_attempt", "attempt_record_id", "ordinal"),
    )


class SandboxAttempt(Base):
    __tablename__ = "sandbox_attempts"
    sandbox_run_id: Mapped[UUID] = mapped_column(nullable=False)
    input_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(nullable=False)
    instance_group_ref_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cleanup_state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    runtime_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    descriptor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    completion_txid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(((instance_group_ref_hash)::text ~ '^[0-9a-f]{64}$'::text))",
            name="sandbox_attempts_gate_0",
        ),
        CheckConstraint(
            "(((cleanup_state)::text = ANY ((ARRAY['pending'::character varying, 'complete'::character varying, 'failed'::character varying])::text[])))",
            name="sandbox_attempts_gate_1",
        ),
        CheckConstraint(
            "((((ended_at IS NULL) AND (termination_code IS NULL) AND ((cleanup_state)::text = 'pending'::text)) OR ((ended_at IS NOT NULL) AND (ended_at >= started_at) AND ((termination_code)::text ~ '^[a-z0-9_]{1,100}$'::text) AND ((cleanup_state)::text = ANY ((ARRAY['complete'::character varying, 'failed'::character varying])::text[])))))",
            name="sandbox_attempts_gate_2",
        ),
        CheckConstraint(
            "(((jsonb_typeof(metrics) = 'object'::text) AND (jsonb_typeof(runtime_versions) = 'object'::text) AND (jsonb_typeof(issues) = 'array'::text)))",
            name="sandbox_attempts_gate_3",
        ),
        UniqueConstraint(
            "id",
            "sandbox_run_id",
            "input_id",
            "task_id",
            name="sandbox_attempts_id_sandbox_run_id_input_id_task_id_key",
        ),
        UniqueConstraint("job_id", "attempt_id", name="sandbox_attempts_job_id_attempt_id_key"),
        PrimaryKeyConstraint("id", name="sandbox_attempts_pkey"),
        ForeignKeyConstraint(
            ["sandbox_run_id", "input_id", "task_id", "job_id"],
            [
                "sandbox_runs.id",
                "sandbox_runs.input_id",
                "sandbox_runs.task_id",
                "sandbox_runs.job_id",
            ],
            name="sandbox_attempts_sandbox_run_id_input_id_task_id_jo_fkey",
        ),
        Index("sandbox_attempts_run", "sandbox_run_id", "started_at"),
    )


class SandboxFetchReceipt(Base):
    __tablename__ = "sandbox_fetch_receipts"
    sandbox_run_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_record_id: Mapped[UUID] = mapped_column(nullable=False)
    input_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    request_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_redirect_id: Mapped[UUID | None] = mapped_column(nullable=True)
    url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_request_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_code: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bundle_artifact_id: Mapped[UUID | None] = mapped_column(nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((request_ordinal >= 0) AND (request_ordinal <= 203)) AND ((response_bytes >= 0) AND (response_bytes <= 67108864))))",
            name="sandbox_fetch_receipts_gate_0",
        ),
        CheckConstraint(
            "((((url_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((response_sha256 IS NULL) OR ((response_sha256)::text ~ '^[0-9a-f]{64}$'::text))))",
            name="sandbox_fetch_receipts_gate_1",
        ),
        CheckConstraint(
            "(((status_code IS NULL) OR ((status_code >= 100) AND (status_code <= 599))))",
            name="sandbox_fetch_receipts_gate_2",
        ),
        CheckConstraint(
            "((((decision_code)::text ~ '^[a-z0-9_]{1,100}$'::text) AND (ended_at >= started_at)))",
            name="sandbox_fetch_receipts_gate_3",
        ),
        CheckConstraint(
            "((parent_redirect_id IS DISTINCT FROM id))", name="sandbox_fetch_receipts_gate_4"
        ),
        UniqueConstraint(
            "attempt_record_id",
            "request_ordinal",
            name="sandbox_fetch_receipts_attempt_record_id_request_ord_key",
        ),
        UniqueConstraint(
            "id", "attempt_record_id", name="sandbox_fetch_receipts_id_attempt_record_id_key"
        ),
        PrimaryKeyConstraint("id", name="sandbox_fetch_receipts_pkey"),
        ForeignKeyConstraint(
            ["attempt_record_id", "sandbox_run_id", "input_id", "task_id"],
            [
                "sandbox_attempts.id",
                "sandbox_attempts.sandbox_run_id",
                "sandbox_attempts.input_id",
                "sandbox_attempts.task_id",
            ],
            name="sandbox_fetch_receipts_attempt_record_id_sandbox_ru_fkey",
        ),
        ForeignKeyConstraint(
            ["bundle_artifact_id", "sandbox_run_id", "input_id", "task_id", "attempt_record_id"],
            [
                "sandbox_artifacts.id",
                "sandbox_artifacts.sandbox_run_id",
                "sandbox_artifacts.input_id",
                "sandbox_artifacts.task_id",
                "sandbox_artifacts.attempt_record_id",
            ],
            name="sandbox_fetch_receipts_bundle_artifact_id_sandbox_r_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_redirect_id", "attempt_record_id"],
            ["sandbox_fetch_receipts.id", "sandbox_fetch_receipts.attempt_record_id"],
            name="sandbox_fetch_receipts_parent_redirect_id_attempt_r_fkey",
        ),
    )


class SandboxInput(Base):
    __tablename__ = "sandbox_inputs"
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    extraction_job_id: Mapped[UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    task_feature_id: Mapped[UUID | None] = mapped_column(nullable=True)
    feature_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    task_resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    product_revision_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_url_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "(((purpose)::text = ANY ((ARRAY['prototype_offline'::character varying, 'vendor_capture'::character varying])::text[])))",
            name="sandbox_inputs_gate_0",
        ),
        CheckConstraint(
            "(((jsonb_typeof(spec) = 'object'::text) AND ((spec ->> 'purpose'::text) IS NOT NULL) AND ((spec ->> 'purpose'::text) = (purpose)::text)))",
            name="sandbox_inputs_gate_1",
        ),
        CheckConstraint("((generation_job_id IS NULL))", name="sandbox_inputs_gate_2"),
        CheckConstraint(
            "(((((purpose)::text = 'prototype_offline'::text) AND (task_feature_id IS NOT NULL) AND (feature_revision_id IS NOT NULL) AND (html_sha256 IS NOT NULL) AND (size_bytes IS NOT NULL) AND (object_key IS NOT NULL) AND (task_resource_id IS NULL) AND (product_revision_id IS NULL) AND (source_field IS NULL) AND (source_url_sha256 IS NULL) AND (encrypted_source_url IS NULL)) OR (((purpose)::text = 'vendor_capture'::text) AND (task_feature_id IS NULL) AND (feature_revision_id IS NULL) AND (html_sha256 IS NULL) AND (size_bytes IS NULL) AND (object_key IS NULL) AND (task_resource_id IS NOT NULL) AND (product_revision_id IS NOT NULL) AND (source_field IS NOT NULL) AND (source_url_sha256 IS NOT NULL) AND (encrypted_source_url IS NOT NULL))))",
            name="sandbox_inputs_gate_3",
        ),
        CheckConstraint(
            "((((html_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((size_bytes >= 1) AND (size_bytes <= 4194304))))",
            name="sandbox_inputs_gate_4",
        ),
        CheckConstraint(
            "((((source_url_sha256)::text ~ '^[0-9a-f]{64}$'::text) AND ((source_field)::text = ANY ((ARRAY['official_url'::character varying, 'whitepaper_url'::character varying])::text[]))))",
            name="sandbox_inputs_gate_5",
        ),
        CheckConstraint(
            "((object_key = (((('sandbox-inputs/'::text) || (id)::text) || '/'::text) || (html_sha256)::text)))",
            name="sandbox_inputs_gate_6",
        ),
        CheckConstraint(
            "(((((purpose)::text = 'prototype_offline'::text) AND (spec ?& ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_feature_id'::text, 'expected_feature_revision_id'::text, 'html_sha256'::text, 'html_size_bytes'::text, 'viewport'::text]) AND ((spec - ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_feature_id'::text, 'expected_feature_revision_id'::text, 'html_sha256'::text, 'html_size_bytes'::text, 'viewport'::text]) = '{}'::jsonb)) OR (((purpose)::text = 'vendor_capture'::text) AND (spec ?& ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_resource_id'::text, 'expected_product_revision_id'::text, 'source_field'::text, 'expected_source_url_sha256'::text, 'format'::text, 'pdf_pages'::text, 'viewport'::text, 'archive'::text, 'capture_key'::text]) AND ((spec - ARRAY['purpose'::text, 'extraction_job_id'::text, 'task_resource_id'::text, 'expected_product_revision_id'::text, 'source_field'::text, 'expected_source_url_sha256'::text, 'format'::text, 'pdf_pages'::text, 'viewport'::text, 'archive'::text, 'capture_key'::text]) = '{}'::jsonb))))",
            name="sandbox_spec_fields",
        ),
        CheckConstraint(
            "(((jsonb_typeof((spec -> 'viewport'::text)) = 'object'::text) AND ((spec -> 'viewport'::text) ?& ARRAY['width'::text, 'height'::text, 'device_scale_factor'::text]) AND (((spec -> 'viewport'::text) - ARRAY['width'::text, 'height'::text, 'device_scale_factor'::text]) = '{}'::jsonb) AND (jsonb_typeof(((spec -> 'viewport'::text) -> 'width'::text)) = 'number'::text) AND (jsonb_typeof(((spec -> 'viewport'::text) -> 'height'::text)) = 'number'::text) AND (((spec -> 'viewport'::text) -> 'device_scale_factor'::text) = '1'::jsonb) AND (((((spec -> 'viewport'::text) ->> 'width'::text))::numeric >= (320)::numeric) AND ((((spec -> 'viewport'::text) ->> 'width'::text))::numeric <= (4096)::numeric)) AND (((((spec -> 'viewport'::text) ->> 'height'::text))::numeric >= (240)::numeric) AND ((((spec -> 'viewport'::text) ->> 'height'::text))::numeric <= (4096)::numeric)) AND (trunc((((spec -> 'viewport'::text) ->> 'width'::text))::numeric) = (((spec -> 'viewport'::text) ->> 'width'::text))::numeric) AND (trunc((((spec -> 'viewport'::text) ->> 'height'::text))::numeric) = (((spec -> 'viewport'::text) ->> 'height'::text))::numeric)))",
            name="sandbox_viewport_fields",
        ),
        UniqueConstraint(
            "id", "task_id", "document_id", name="sandbox_inputs_id_task_id_document_id_key"
        ),
        UniqueConstraint("id", "task_id", name="sandbox_inputs_id_task_id_key"),
        PrimaryKeyConstraint("id", name="sandbox_inputs_pkey"),
        ForeignKeyConstraint(
            ["generation_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="sandbox_generation_binding",
        ),
        ForeignKeyConstraint(
            ["document_id", "task_id"],
            ["documents.id", "documents.task_id"],
            name="sandbox_input_document",
        ),
        ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="sandbox_inputs_document_id_fkey"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id"], ["jobs.id"], name="sandbox_inputs_extraction_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["extraction_job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="sandbox_inputs_extraction_job_id_task_id_document_i_fkey",
        ),
        ForeignKeyConstraint(
            ["feature_revision_id"],
            ["feature_revisions.id"],
            name="sandbox_inputs_feature_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["generation_job_id"], ["jobs.id"], name="sandbox_inputs_generation_job_id_fkey"
        ),
        ForeignKeyConstraint(
            ["product_revision_id"],
            ["product_revisions.id"],
            name="sandbox_inputs_product_revision_id_fkey",
        ),
        ForeignKeyConstraint(
            ["task_feature_id", "task_id", "feature_revision_id"],
            ["task_features.id", "task_features.task_id", "task_features.feature_revision_id"],
            name="sandbox_inputs_task_feature_id_task_id_feature_revi_fkey",
        ),
        ForeignKeyConstraint(["task_id"], ["tasks.id"], name="sandbox_inputs_task_id_fkey"),
        ForeignKeyConstraint(
            ["task_resource_id", "task_id", "product_revision_id"],
            ["task_resources.id", "task_resources.task_id", "task_resources.product_revision_id"],
            name="sandbox_inputs_task_resource_id_task_id_product_rev_fkey",
        ),
    )


class SandboxRun(Base):
    __tablename__ = "sandbox_runs"
    input_id: Mapped[UUID] = mapped_column(nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    token_id: Mapped[UUID | None] = mapped_column(nullable=True)
    scope_snapshot: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    capture_key: Mapped[UUID | None] = mapped_column(nullable=True)
    id: Mapped[UUID] = mapped_column(nullable=False, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    __table_args__ = (
        CheckConstraint(
            "((((request_hash)::text ~ '^[0-9a-f]{64}$'::text) AND ((runtime_profile_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((policy_sha256)::text ~ '^[0-9a-f]{64}$'::text)))",
            name="sandbox_runs_gate_0",
        ),
        CheckConstraint(
            "((((actor_kind)::text = ANY ((ARRAY['session'::character varying, 'token'::character varying, 'agent'::character varying])::text[])) AND (((actor_kind)::text = 'token'::text) = (token_id IS NOT NULL))))",
            name="sandbox_runs_gate_1",
        ),
        CheckConstraint(
            '(((jsonb_typeof(scope_snapshot) = \'array\'::text) AND (scope_snapshot <@ \'["sandbox:read", "sandbox:render", "sandbox:capture", "task:read", "resource:read", "job:read"]\'::jsonb) AND (scope_snapshot ?& ARRAY[\'task:read\'::text, \'resource:read\'::text, \'job:read\'::text])))',
            name="sandbox_runs_gate_2",
        ),
        CheckConstraint(
            "(((profile)::text = ANY ((ARRAY['prototype-offline-v1'::character varying, 'vendor-capture-v1'::character varying])::text[])))",
            name="sandbox_runs_gate_3",
        ),
        CheckConstraint(
            "(((policy_revision)::text ~ '^[A-Za-z0-9_.-]{1,100}$'::text))",
            name="sandbox_runs_gate_4",
        ),
        UniqueConstraint("capture_key", name="sandbox_runs_capture_key_key"),
        UniqueConstraint(
            "id",
            "input_id",
            "task_id",
            "job_id",
            name="sandbox_runs_id_input_id_task_id_job_id_key",
        ),
        UniqueConstraint("job_id", name="sandbox_runs_job_id_key"),
        UniqueConstraint("request_hash", name="sandbox_runs_request_hash_key"),
        PrimaryKeyConstraint("id", name="sandbox_runs_pkey"),
        ForeignKeyConstraint(
            ["input_id", "task_id", "document_id"],
            ["sandbox_inputs.id", "sandbox_inputs.task_id", "sandbox_inputs.document_id"],
            name="sandbox_runs_input_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(
            ["job_id", "task_id", "document_id"],
            ["jobs.id", "jobs.task_id", "jobs.document_id"],
            name="sandbox_runs_job_id_task_id_document_id_fkey",
        ),
        ForeignKeyConstraint(["token_id"], ["api_tokens.id"], name="sandbox_runs_token_id_fkey"),
        Index("sandbox_runs_task", "task_id", "created_at"),
    )
