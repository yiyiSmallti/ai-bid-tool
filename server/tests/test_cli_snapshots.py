import hashlib
import json
from pathlib import Path

import bid_cli.export as export_cli
from app.schemas.contracts import Result
from bid_cli.client import Client
from bid_cli.main import main
from cryptography.fernet import Fernet

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
IDENTIFIER_2 = "00000000-0000-0000-0000-000000000002"
IDENTIFIER_3 = "00000000-0000-0000-0000-000000000003"
IDENTIFIER_4 = "00000000-0000-0000-0000-000000000004"
PRODUCT = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "product_id": IDENTIFIER,
    "revision": 1,
    "data": {
        "name": "Synthetic product",
        "vendor": "Synthetic vendor",
        "model": "Exact synthetic model",
        "model_version": None,
        "official_url": None,
        "whitepaper_url": None,
    },
}
SELECTION = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "product_revision_id": IDENTIFIER,
    "revision": 1,
    "lot": None,
    "data": PRODUCT["data"],
}
FEATURE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "feature_id": IDENTIFIER,
    "revision": 1,
    "data": {
        "product_id": IDENTIFIER,
        "name": "Synthetic feature",
        "description": "Synthetic declaration",
        "status": "planned",
    },
}
FEATURE_SELECTION = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "feature_revision_id": IDENTIFIER,
    "revision": 1,
    "lot": None,
    "data": FEATURE["data"],
}
FEATURE_WARNINGS = ["Implementation status is a declaration; evidence has not been verified"]

CERTIFICATE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "certificate_id": IDENTIFIER,
    "revision": 1,
    "data": {
        "kind": "qualification",
        "name": "Synthetic declaration",
        "number": "SYNTHETIC-ONLY",
        "valid_from": None,
        "valid_until": None,
    },
}
CERTIFICATE_SELECTION = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "certificate_revision_id": IDENTIFIER,
    "revision": 1,
    "lot": None,
    "data": CERTIFICATE["data"],
}
CERTIFICATE_WARNINGS = [
    "Certificate metadata and dates are declarations; authenticity, legality and compliance have not been verified"
]

PROFILE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "profile_id": IDENTIFIER,
    "revision": 1,
    "data": {
        "name": "Synthetic declaration",
        "registration_details": None,
        "performance_summary": None,
        "standard_wording": None,
    },
}
PROFILE_SELECTION = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "profile_revision_id": IDENTIFIER,
    "revision": 1,
    "lot": None,
    "data": PROFILE["data"],
}
PROFILE_WARNINGS = [
    "Company metadata is a declaration; authenticity, performance and qualification have not been verified"
]


TEMPLATE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "template_id": IDENTIFIER,
    "revision": 1,
    "data": {"name": "Synthetic template", "project_types": None, "chapters": None},
    "file": {
        "name": "synthetic.docx",
        "sha256": "b" * 64,
        "size_bytes": 1,
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
}
TEMPLATE_SELECTION = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "template_revision_id": IDENTIFIER,
    "revision": 1,
    "lot": None,
    "data": TEMPLATE["data"],
    "file": TEMPLATE["file"],
}
TEMPLATE_WARNINGS = [
    "Template metadata is a declaration; chapter matching and export adaptation have not been verified"
]

EXPORT_COLUMNS = [
    {"key": key, "width_percent": width}
    for key, width in zip(
        (
            "ordinal",
            "tender_clause",
            "source_location",
            "response",
            "deviation",
            "deviation_note",
            "evidence",
        ),
        (5, 25, 15, 25, 10, 10, 10),
        strict=True,
    )
]
EXPORT_SECTIONS = [
    {
        "section": section,
        "heading_style_id": "Heading 1",
        "table_style_id": "Table Grid",
        "columns": EXPORT_COLUMNS if section in {"substantive", "commercial", "technical"} else [],
    }
    for section in (
        "substantive",
        "commercial",
        "technical",
        "comply_only",
        "gaps",
        "evidence_appendix",
    )
]
EXPORT_BINDING = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "template_revision_id": IDENTIFIER,
    "template_sha256": "b" * 64,
    "binding_hash": "c" * 64,
    "static_content_hash": "d" * 64,
    "adapter_version": "docx-export-v1",
    "sections": EXPORT_SECTIONS,
    "reviewed_by": IDENTIFIER,
    "reviewed_at": "2026-10-01T00:00:00Z",
}
EXPORT_RUN = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "draft_id": IDENTIFIER,
    "render_job_id": IDENTIFIER_2,
    "mode": "review_copy",
    "input_hash": "e" * 64,
    "state": "awaiting_release",
    "candidate_sha256": "f" * 64,
    "export_id": None,
    "issues": [],
}
EXPORT_FILE = {
    "name": "synthetic-review.docx",
    "sha256": "a" * 64,
    "size_bytes": 1,
    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
EXPORT_VIEW = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "run_id": IDENTIFIER,
    "draft_id": IDENTIFIER,
    "task_template_id": IDENTIFIER,
    "template_revision_id": IDENTIFIER,
    "binding_id": IDENTIFIER,
    "mode": "review_copy",
    "completion": "partial",
    "validity": "current",
    "input_hash": "e" * 64,
    "manifest_hash": "f" * 64,
    "file": EXPORT_FILE,
    "released_by": IDENTIFIER,
    "released_at": "2026-10-01T00:00:00Z",
    "issues": [],
    "invalidated_requirement_ids": [],
}


async def fake_download(self, revision_id, output):
    # JSON shape fixture only; the actual download transport has independent tests.
    return Result(
        ok=True,
        command="resource template download",
        data={
            "template_revision_id": str(revision_id),
            "output_path": str(output),
            "file": TEMPLATE["file"],
        },
        warnings=TEMPLATE_WARNINGS,
    ).model_dump(mode="json")


async def fake_export_download(self, export_id, output):
    return Result(
        ok=False,
        command="export download",
        data={
            "export_id": str(export_id),
            "output_path": str(output),
            "file": EXPORT_FILE,
            "mode": "review_copy",
            "completion": "partial",
            "validity": "current",
            "issues": [],
        },
    ).model_dump(mode="json")


SCAN = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "certificate_id": IDENTIFIER,
    "certificate_revision_id": IDENTIFIER,
    "revision": 2,
    "data": CERTIFICATE["data"],
    "file": {
        "name": "synthetic.pdf",
        "sha256": "c" * 64,
        "size_bytes": 1,
        "page_count": 1,
        "media_type": "application/pdf",
    },
}
SCAN_SELECTION = {
    **CERTIFICATE_SELECTION,
    "revision": 2,
    "certificate_file_id": IDENTIFIER,
    "file": SCAN["file"],
}
SCAN_WARNINGS = [
    "Certificate file is user-supplied; authenticity, eligibility and metadata matching have not been verified"
]


async def fake_scan_download(self, revision_id, output):
    # Contract fixture only; actual PDF transport and files have independent tests.
    return Result(
        ok=True,
        command="resource certificate file download",
        data={
            "certificate_revision_id": str(revision_id),
            "output_path": str(output),
            "file": SCAN["file"],
        },
        warnings=SCAN_WARNINGS,
    ).model_dump(mode="json")


SOURCE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "task_certificate_id": IDENTIFIER,
    "certificate_id": IDENTIFIER,
    "certificate_revision_id": IDENTIFIER,
    "certificate_file_id": IDENTIFIER,
    "source_kind": "user_supplied_certificate_pdf",
    "original": SCAN["file"],
    "page": 1,
    "render_profile": "pdf-page-preview-v1",
    "dpi": 150,
    "preview": {
        "name": "synthetic.png",
        "sha256": "d" * 64,
        "size_bytes": 1,
        "media_type": "image/png",
        "width_px": 1,
        "height_px": 1,
    },
    "rendered_at": "2026-10-01T00:00:00Z",
    "created_by": IDENTIFIER,
    "active_selection": True,
    "status": "unconfirmed_source",
    "confirmed_by": None,
    "eligible_for_draft_export": False,
}
SOURCE_WARNINGS = [
    "Source is an unconfirmed user-supplied PDF page; authenticity and eligibility are not verified; never eligible for draft/export"
]

SANDBOX_ARTIFACT = {
    "id": IDENTIFIER,
    "run_id": IDENTIFIER,
    "attempt_id": IDENTIFIER,
    "kind": "rendered_html",
    "sha256": "e" * 64,
    "size_bytes": 1,
    "media_type": "text/html",
    "width": None,
    "height": None,
    "page": None,
    "parent_artifact_id": None,
    "provenance_manifest_hash": "f" * 64,
}
SANDBOX_RUN = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "job_id": IDENTIFIER,
    "purpose": "prototype_offline",
    "request_hash": "a" * 64,
    "profile": "prototype-v1",
    "policy_revision": "policy-v1",
    "selection_active": True,
    "state": "queued",
    "attempt_id": None,
    "cleanup_state": "not_started",
    "artifacts": [],
    "metrics": None,
    "issues": [],
    "usage_ids": [],
    "charge": None,
    "charge_currency": None,
}

CLAUSE = {
    "document_id": IDENTIFIER,
    "chunk_id": IDENTIFIER,
    "page": 1,
    "location": None,
    "quote": "Synthetic source",
}
EVIDENCE = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "card_id": IDENTIFIER_2,
    "input": {
        "kind": "product",
        "selection_id": IDENTIFIER,
        "field_path": "name",
        "quote": "Synthetic product",
    },
    "selection_id": IDENTIFIER,
    "resource_revision_id": IDENTIFIER,
    "material_kind": "declaration",
    "quote_check": "exact_field_match",
    "source_archive": None,
    "confirmed_by": IDENTIFIER,
    "confirmed_at": "2026-10-01T00:00:00+00:00",
    "active_selection": True,
}
CARD = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "extraction_job_id": IDENTIFIER,
    "requirement_id": IDENTIFIER,
    "revision": 1,
    "revision_id": IDENTIFIER,
    "state": "draft",
    "review_domain": "technical",
    "disposition": "respond",
    "disposition_by": IDENTIFIER,
    "disposition_at": "2026-10-01T00:00:00+00:00",
    "suggested_disposition": None,
    "origin": "human",
    "actor_kind": "session",
    "model_job_id": None,
    "review_hint": None,
    "source": CLAUSE,
    "content": {
        "response_kind": "commitment",
        "response_text": "We commit to the stated delivery date.",
        "deviation": "none",
        "deviation_note": "The offered delivery date is identical.",
        "evidence": [],
    },
    "evidence": [],
    "confirmed_by": None,
    "confirmed_at": None,
    "reason": None,
    "reviewed_warning_codes": [],
    "warning_codes": [],
    "eligibility": "unconfirmed",
}
DRAFT = {
    "id": IDENTIFIER,
    "org_id": IDENTIFIER,
    "task_id": IDENTIFIER,
    "extraction_job_id": IDENTIFIER,
    "generation_job_id": IDENTIFIER,
    "status": "draft",
    "completion": "partial",
    "validity": "current",
    "input_hash": "e" * 64,
    "tables": {
        "substantive": [],
        "commercial": [
            {
                "requirement_id": IDENTIFIER,
                "card_id": IDENTIFIER,
                "card_revision_id": IDENTIFIER,
                "table": "commercial",
                "category": "qualification",
                "starred": False,
                "tender_clause": CLAUSE,
                "location_label": "Page 1",
                "response_kind": "commitment",
                "response_text": "We commit to the stated delivery date.",
                "deviation": "none",
                "deviation_note": "The offered delivery date is identical.",
                "evidence": [],
            }
        ],
        "technical": [
            {
                "requirement_id": IDENTIFIER_2,
                "card_id": IDENTIFIER_2,
                "card_revision_id": IDENTIFIER_2,
                "table": "technical",
                "category": "technical",
                "starred": False,
                "tender_clause": CLAUSE,
                "location_label": "Page 1",
                "response_kind": "evidence",
                "response_text": "The selected product has the named capability.",
                "deviation": "negative",
                "deviation_note": "One optional mode is not offered.",
                "evidence": [EVIDENCE],
            }
        ],
    },
    "comply_only": [
        {
            "requirement_id": IDENTIFIER_3,
            "card_id": IDENTIFIER_3,
            "card_revision_id": IDENTIFIER_3,
            "tender_clause": CLAUSE,
            "location_label": "Page 1",
            "disposition_by": IDENTIFIER,
            "disposition_at": "2026-10-01T00:00:00+00:00",
        }
    ],
    "gaps": [
        {
            "requirement_id": IDENTIFIER_4,
            "card_id": None,
            "card_revision_id": None,
            "tender_clause": CLAUSE,
            "location_label": "Page 1",
            "reasons": ["missing_card"],
        }
    ],
    "invalidated_requirements": [],
}


async def fake_source_download(self, source_id, output):
    # Contract fixture only; real rendering/transport is tested independently.
    return Result(
        ok=True,
        command="evidence source download",
        data={
            "evidence_source_id": str(source_id),
            "output_path": str(output),
            "file": SOURCE["preview"],
        },
        warnings=SOURCE_WARNINGS,
    ).model_dump(mode="json")


async def fake_sandbox_download(self, artifact_id, output):
    return Result(
        ok=True,
        command="sandbox download",
        data={
            "artifact": SANDBOX_ARTIFACT,
            "output_path": str(output),
        },
    ).model_dump(mode="json")


PLATFORM_ORG = {
    "id": IDENTIFIER,
    "name": "Synthetic tenant",
    "active": True,
    "created_at": "2026-10-01T00:00:00+00:00",
    "member_count": 1,
    "admin_emails": ["boss@example.test"],
}
PLATFORM_MODEL = {
    "id": "opus-standard",
    "capability": "llm_extract",
    "provider": "anthropic",
    "model": "claude-opus-5-5",
    "base_url": None,
    "credential": "main",
    "vendor_input_usd_per_mtok": 4.0,
    "vendor_output_usd_per_mtok": 20.0,
    "sale_input_per_mtok": 6.0,
    "sale_output_per_mtok": 30.0,
    "enabled": True,
    "revision": 1,
    "updated_by": "ops@example.test",
    "updated_at": "2026-10-01T00:00:00+00:00",
    "default": True,
    "credential_configured": True,
}
PLATFORM_USAGE = {
    "org_id": IDENTIFIER,
    "org_name": "Synthetic tenant",
    "month": "2026-10-01",
    "billing": "platform",
    "provider": "anthropic",
    "model": "claude-opus-5-5",
    "calls": 1,
    "tokens": 10,
    "input_tokens": 8,
    "output_tokens": 2,
    "ocr_pages": 0,
    "vendor_usd": 0.1,
    "unpriced_calls": 0,
    "charge": 0.15,
}
PLATFORM_CARD = {
    "id": IDENTIFIER,
    "last4": "TURE",
    "face_value": 10.0,
    "currency": "USD",
    "batch_id": IDENTIFIER,
    "note": None,
    "expires_at": None,
    "status": "active",
    "expired": False,
    "created_by": "ops@example.test",
    "created_at": "2026-10-01T00:00:00+00:00",
    "redeemed_org_id": None,
    "redeemed_at": None,
}
PLATFORM_AUDIT = {
    "id": IDENTIFIER,
    "created_at": "2026-10-01T00:00:00+00:00",
    "actor_email": "ops@example.test",
    "action": "platform.login",
    "object_id": None,
    "outcome": "success",
    "details": {"totp_counter": 1},
}


async def fake_request(self, method, path, **kwargs):
    # Contract fixtures only: no real service, provider or user data is involved.
    data, items, warnings = {}, [], []
    if path == "/auth/login":
        data = {"session": "synthetic-fixture-session", "org_id": IDENTIFIER, "expires_in": 3600}
    elif path == "/org/current":
        data = {"org_id": IDENTIFIER, "role": "admin"}
    elif path == "/platform/auth/login":
        data = {
            "session": "synthetic-fixture-session",
            "email": "ops@example.test",
            "expires_in": 1800,
        }
    elif path == "/platform/orgs" and method == "POST":
        data = {
            "org_id": IDENTIFIER,
            "admin_user_id": IDENTIFIER,
            "admin_email": "boss@example.test",
            "user_created": True,
            "setup_url": "/app/setup-password#token=synthetic-fixture-link",
            "setup_expires_in": 86400,
        }
    elif path == "/platform/orgs":
        items = [PLATFORM_ORG]
    elif path.endswith("/active"):
        data = {"org_id": IDENTIFIER, "active": False}
    elif path == "/platform/models":
        if method == "POST":
            data = PLATFORM_MODEL
        else:
            items = [PLATFORM_MODEL]
    elif path.startswith("/platform/models/"):
        data = {"model_id": "opus-standard", "passed": True, "items": 1, "usage": {"tokens": 10}}
    elif path == "/platform/usage":
        data = {
            "from": "2026-10",
            "to": "2026-10",
            "totals": {"calls": 1, "tokens": 10, "vendor_usd": 0.1, "charge": 0.15},
        }
        items = [PLATFORM_USAGE]
    elif path == "/platform/audit":
        items = [PLATFORM_AUDIT]
    elif path == "/auth/setup-password":
        data = {"password_set": True}
    elif path == "/auth/orgs":
        items = [
            {"org_id": IDENTIFIER, "name": "Synthetic tenant", "role": "admin", "active": True}
        ]
    elif path.endswith("/balance"):
        data = {
            "org_id": IDENTIFIER,
            "mode": "add",
            "delta": 10.0,
            "balance": 10.0,
            "currency": "USD",
        }
    elif path == "/platform/cards" and method == "POST":
        data = {
            "batch_id": IDENTIFIER,
            "count": 1,
            "face_value": 10.0,
            "currency": "USD",
            "expires_at": None,
            "note": None,
            "cards": [{"id": IDENTIFIER, "code": "SYNT-HETI-CFIX-TURE", "last4": "TURE"}],
        }
    elif path == "/platform/cards":
        data, items = {"currency": "USD"}, [PLATFORM_CARD]
    elif path.endswith("/void"):
        data = {**PLATFORM_CARD, "status": "void"}
    elif path == "/billing":
        data = {"currency": "USD", "balance": 10.0}
        items = [
            {
                "id": IDENTIFIER,
                "created_at": "2026-10-01T00:00:00+00:00",
                "kind": "redeem",
                "amount": 10.0,
                "balance_after": 10.0,
                "currency": "USD",
                "reason": None,
            }
        ]
    elif path == "/billing/redeem":
        data = {"card_id": IDENTIFIER, "amount": 10.0, "balance": 10.0, "currency": "USD"}
    elif path.endswith("/sandbox-runs"):
        if method == "POST":
            submit = kwargs.get("json") or json.loads(kwargs["data"]["submit"])
            purpose = submit["spec"]["purpose"]
            if submit["dry_run"]:
                data = {
                    "dry_run": True,
                    "request_hash": "a" * 64,
                    "purpose": purpose,
                    "profile": "prototype-v1" if purpose == "prototype_offline" else "capture-v1",
                    "policy_revision": "policy-v1",
                    "ready": True,
                    "issues": [],
                    "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
                    "estimate_basis": "no_vendor_call",
                    "reserved_charge": None,
                    "charge_currency": None,
                    "estimated_duration_ms": None,
                }
            else:
                assert submit["expected_request_hash"] == "a" * 64
                data = {
                    **SANDBOX_RUN,
                    "purpose": purpose,
                    "profile": "prototype-v1" if purpose == "prototype_offline" else "capture-v1",
                }
        else:
            data, items = kwargs["params"], [SANDBOX_RUN]
    elif path.startswith("/sandbox-runs/"):
        data = SANDBOX_RUN
    elif path == "/tasks" and method == "POST":
        data = {"id": IDENTIFIER, "name": "Synthetic task", "org_id": IDENTIFIER}
    elif path == "/tasks":
        items = [{"id": IDENTIFIER, "name": "Synthetic task", "org_id": IDENTIFIER}]
    elif path == "/export-template-bindings":
        if method == "POST":
            data = EXPORT_BINDING
        else:
            data, items = {"template_revision_id": IDENTIFIER}, [EXPORT_BINDING]
    elif path.endswith("/export-runs"):
        data = EXPORT_RUN
    elif path.startswith("/export-runs/") and path.endswith("/release"):
        data = EXPORT_VIEW
    elif path.startswith("/export-runs/"):
        data = EXPORT_RUN
    elif path.endswith("/exports"):
        data, items = {"task_id": IDENTIFIER}, [EXPORT_VIEW]
    elif path.startswith("/exports/"):
        data = EXPORT_VIEW
    elif path.endswith("/model-redaction"):
        data = {
            "task_id": IDENTIFIER,
            "revision": 2,
            "model_redaction_enabled": False,
            "changed_by": IDENTIFIER,
        }
    elif path.endswith("/cards/dispositions"):
        data = {"correlation_id": IDENTIFIER, "updated": 1}
        items = [CARD]
    elif path.endswith("/cards/generations"):
        assert kwargs["json"] == {
            "extraction_job_id": IDENTIFIER,
            "requirement_ids": [IDENTIFIER, IDENTIFIER_2],
            "reasoning": "high",
            "dry_run": False,
            "retry": True,
        }
        data = {
            "job_id": IDENTIFIER_2,
            "generation_job_id": IDENTIFIER_2,
            "status": "queued",
            "cached": False,
        }
    elif path.endswith("/cards"):
        if method == "POST":
            data = CARD
        else:
            data = {"task_id": IDENTIFIER, "extraction_job_id": IDENTIFIER}
            items = [
                {
                    "requirement_id": IDENTIFIER,
                    "source": CLAUSE,
                    "status": "draft",
                    "card": CARD,
                }
            ]
    elif path.startswith("/cards/"):
        data = CARD
        if kwargs.get("params", {}).get("history") == "true":
            items = [CARD]
    elif path.endswith("/drafts"):
        if method == "POST":
            data = {"job_id": IDENTIFIER, "status": "queued", "cached": False}
        else:
            data = {"task_id": IDENTIFIER, "extraction_job_id": IDENTIFIER}
            items = [
                {
                    "id": IDENTIFIER,
                    "task_id": IDENTIFIER,
                    "extraction_job_id": IDENTIFIER,
                    "generation_job_id": IDENTIFIER,
                    "completion": "partial",
                    "validity": "current",
                    "status": "draft",
                    "input_hash": "e" * 64,
                    "invalidated_requirements": [],
                    "summary": {
                        "rows": 2,
                        "comply_only": 1,
                        "gaps": 1,
                        "negative_deviations": 1,
                    },
                }
            ]
    elif path.startswith("/drafts/"):
        data = DRAFT
    elif path.startswith("/resources/products"):
        if method == "POST":
            data = PRODUCT
        else:
            data, items = {"history": False, "current_revisions": {IDENTIFIER: 1}}, [PRODUCT]
    elif path.endswith("/products"):
        if method == "POST":
            data = {**SELECTION, "duplicate": False, "replaced_snapshot_id": None}
        else:
            data, items = {"history": False, "active_snapshot_ids": [IDENTIFIER]}, [SELECTION]
    elif path.startswith("/resources/features"):
        warnings = FEATURE_WARNINGS
        if method == "POST":
            data = FEATURE
        else:
            data, items = {"history": False, "current_revisions": {IDENTIFIER: 1}}, [FEATURE]
    elif path.endswith("/features"):
        warnings = FEATURE_WARNINGS
        if method == "POST":
            data = {**FEATURE_SELECTION, "duplicate": False, "replaced_snapshot_id": None}
        else:
            data, items = (
                {"history": False, "active_snapshot_ids": [IDENTIFIER]},
                [FEATURE_SELECTION],
            )
    elif path.endswith("/file-revisions") or path == "/resources/certificates/files":
        warnings = SCAN_WARNINGS
        if method == "POST":
            data = SCAN
        else:
            data, items = {"history": False, "current_revisions": {IDENTIFIER: 2}}, [SCAN]
    elif path.endswith("/certificate-files"):
        warnings = SCAN_WARNINGS
        data, items = {"history": False, "active_snapshot_ids": [IDENTIFIER]}, [SCAN_SELECTION]
    elif path.startswith("/resources/certificates"):
        warnings = CERTIFICATE_WARNINGS
        if method == "POST":
            data = CERTIFICATE
        else:
            data, items = (
                {
                    "history": False,
                    "current_revisions": {IDENTIFIER: 1},
                    "validity_by_revision": {IDENTIFIER: {"as_of": None, "state": "unknown"}},
                },
                [CERTIFICATE],
            )
    elif path.endswith("/certificates"):
        warnings = CERTIFICATE_WARNINGS
        if method == "POST":
            data = {**CERTIFICATE_SELECTION, "duplicate": False, "replaced_snapshot_id": None}
        else:
            data, items = (
                {
                    "history": False,
                    "active_snapshot_ids": [IDENTIFIER],
                    "validity_by_revision": {IDENTIFIER: {"as_of": None, "state": "unknown"}},
                },
                [CERTIFICATE_SELECTION],
            )
    elif path.startswith("/resources/profiles"):
        warnings = PROFILE_WARNINGS
        if method == "POST":
            data = PROFILE
        else:
            data, items = {"history": False, "current_revisions": {IDENTIFIER: 1}}, [PROFILE]
    elif path.endswith("/profiles"):
        warnings = PROFILE_WARNINGS
        if method == "POST":
            data = {**PROFILE_SELECTION, "duplicate": False, "replaced_snapshot_id": None}
        else:
            data, items = (
                {"history": False, "active_snapshot_ids": [IDENTIFIER]},
                [PROFILE_SELECTION],
            )
    elif path.startswith("/resources/templates"):
        warnings = TEMPLATE_WARNINGS
        if method == "POST":
            data = TEMPLATE
        else:
            data, items = {"history": False, "current_revisions": {IDENTIFIER: 1}}, [TEMPLATE]
    elif path.endswith("/templates"):
        warnings = TEMPLATE_WARNINGS
        if method == "POST":
            data = {**TEMPLATE_SELECTION, "duplicate": False, "replaced_snapshot_id": None}
        else:
            data, items = (
                {"history": False, "active_snapshot_ids": [IDENTIFIER]},
                [TEMPLATE_SELECTION],
            )
    elif path.endswith("/evidence-sources"):
        warnings = SOURCE_WARNINGS
        if method == "POST":
            data = {"source": SOURCE, "duplicate": False}
        else:
            data, items = {"history": False, "active_source_ids": [IDENTIFIER]}, [SOURCE]
    elif path.endswith("/documents"):
        data = {
            "id": IDENTIFIER,
            "name": "fixture.pdf",
            "sha256": "a" * 64,
            "task_id": IDENTIFIER,
            "duplicate": False,
        }
    elif path.endswith("/parse"):
        data = {"job_id": IDENTIFIER, "status": "queued", "cached": False}
    elif path.endswith("/extract"):
        data = {"job_id": IDENTIFIER, "status": "queued", "cached": False, "reasoning": "high"}
    elif path.endswith("/extractions"):
        items = [
            {
                "job_id": IDENTIFIER,
                "document_id": IDENTIFIER,
                "reasoning": "high",
                "model": "synthetic-model",
                "status": "succeeded",
                "created_at": "2026-10-01T00:00:00+00:00",
                "finished_at": "2026-10-01T00:05:00+00:00",
                "saved": 2,
                "rejected": 0,
                "tokens": 1500,
                "error": None,
                "latest": True,
            }
        ]
    elif path.endswith("/cancel"):
        data = {"id": IDENTIFIER, "status": "cancelled"}
    elif path == f"/jobs/{IDENTIFIER_2}":
        data = {
            "id": IDENTIFIER_2,
            "kind": "card_generate",
            "status": "succeeded",
            "error": None,
            "attempts": 1,
            "reasoning": "high",
            "result": {
                "generation_job_id": IDENTIFIER_2,
                "completion": "partial",
                "created_revision_ids": [IDENTIFIER_3],
                "skipped": {},
                "rejected_references": {IDENTIFIER: ["m1:quote_not_sent"]},
                "needs_material": [IDENTIFIER],
                "usage_record_ids": [IDENTIFIER_4],
                "charge": "0.001",
                "billing_currency": "USD",
                "stop_reason": None,
                "warnings": [f"needs_material:{IDENTIFIER}"],
                "cost": {"llm_tokens": 1000, "ocr_pages": 0, "usd": None},
                "exit_code": 5,
            },
        }
    elif path.startswith("/jobs/"):
        data = {
            "id": IDENTIFIER,
            "kind": "draft",
            "status": "succeeded",
            "result": {
                "draft_id": IDENTIFIER,
                "completion": "partial",
                "warnings": [f"missing_card:{IDENTIFIER_4}"],
                "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
                "exit_code": 5,
            },
            "error": None,
            "attempts": 1,
        }
    elif path.endswith("/requirements/repair"):
        execute = method == "POST"
        if execute:
            assert kwargs["json"] == {
                "extraction_job_id": IDENTIFIER,
                "expected_preview": "a" * 64,
                "reason": "Reviewed synthetic citation repair",
            }
        else:
            assert kwargs["params"] == {"job": IDENTIFIER}
        data = {
            "task_id": IDENTIFIER,
            "extraction_job_id": IDENTIFIER,
            "preview_hash": "a" * 64,
            "execute": execute,
            "changed": 1 if execute else 0,
            "repairable": 1,
            "unlocatable": 0,
            "unchanged": 0,
        }
        items = [
            {
                "requirement_id": IDENTIFIER,
                "source": {
                    "document_id": IDENTIFIER,
                    "chunk_id": IDENTIFIER,
                    "page": 1,
                    "location": None,
                    "quote": "Synthetic source",
                },
                "model_quote": None,
                "proposed_quote": "Synthetic  source",
                "status": "repairable",
                "reason": None,
                "quote_changed": True,
            }
        ]
    elif path.endswith("/requirements"):
        items = [
            {
                "id": IDENTIFIER,
                "text": "Synthetic requirement",
                "model_quote": "Synthetic source",
                "job_id": IDENTIFIER,
                "reasoning": None,
                "source": {
                    "document_id": IDENTIFIER,
                    "chunk_id": IDENTIFIER,
                    "page": 1,
                    "location": None,
                    "quote": "Synthetic source",
                },
            },
            {
                "id": IDENTIFIER,
                "text": "Synthetic Word requirement",
                "model_quote": "★ 核心数不少于 32 核",
                "category": "technical",
                "starred": True,
                "condition": {},
                "job_id": IDENTIFIER,
                "reasoning": "high",
                "source": {
                    "document_id": IDENTIFIER,
                    "chunk_id": IDENTIFIER,
                    "page": None,
                    "location": {
                        "block_id": "t1r2c2",
                        "kind": "cell",
                        "section_path": ["第一章 总则"],
                        "paragraph": None,
                        "table": 1,
                        "row": 2,
                        "column": 2,
                        "label": "第一章 总则 > 表 1 第 2 行第 2 列",
                    },
                    "quote": "★ 核心数不少于 32 核",
                },
            },
        ]
    elif path == "/tokens":
        data = {
            "id": IDENTIFIER,
            "token": "synthetic-fixture-token",
            "scopes": ["task:read"],
            "expires_at": "2030-01-01T00:00:00+00:00",
        }
    return Result(ok=True, command="fixture", data=data, items=items, warnings=warnings).model_dump(
        mode="json"
    )


def test_every_command_json_snapshot(monkeypatch, tmp_path, capsys, docx_bytes, pdf_bytes):
    monkeypatch.setenv("BID_CLI_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BID_PASSWORD", "synthetic-test-password")
    monkeypatch.setattr(Client, "request", fake_request)
    monkeypatch.setattr(Client, "download_template", fake_download)
    monkeypatch.setattr(Client, "download_certificate_file", fake_scan_download)
    monkeypatch.setattr(Client, "download_evidence_source", fake_source_download)
    monkeypatch.setattr(export_cli, "download_export", fake_export_download)
    monkeypatch.setattr("bid_cli.sandbox.download_artifact", fake_sandbox_download)
    source = tmp_path / "fixture.pdf"
    source.write_bytes(b"Synthetic transport fixture")
    product_input, update_input, selection_input = (
        tmp_path / name for name in ("product.json", "update.json", "selection.json")
    )
    product_input.write_text(json.dumps({"data": PRODUCT["data"]}))
    update_input.write_text(json.dumps({"expected_revision": 1, "data": PRODUCT["data"]}))
    selection_input.write_text(json.dumps({"product_id": IDENTIFIER}))
    feature_input, feature_update_input, feature_selection_input = (
        tmp_path / name
        for name in ("feature.json", "feature-update.json", "feature-selection.json")
    )
    feature_input.write_text(json.dumps({"data": FEATURE["data"]}))
    feature_update_input.write_text(json.dumps({"expected_revision": 1, "data": FEATURE["data"]}))
    feature_selection_input.write_text(json.dumps({"feature_id": IDENTIFIER}))
    certificate_input, certificate_update_input, certificate_selection_input = (
        tmp_path / name
        for name in ("certificate.json", "certificate-update.json", "certificate-selection.json")
    )
    certificate_input.write_text(json.dumps({"data": CERTIFICATE["data"]}))
    certificate_update_input.write_text(
        json.dumps({"expected_revision": 1, "data": CERTIFICATE["data"]})
    )
    certificate_selection_input.write_text(json.dumps({"certificate_id": IDENTIFIER}))
    profile_input, profile_update_input, profile_selection_input = (
        tmp_path / name
        for name in ("profile.json", "profile-update.json", "profile-selection.json")
    )
    profile_input.write_text(json.dumps({"data": PROFILE["data"]}))
    profile_update_input.write_text(json.dumps({"expected_revision": 1, "data": PROFILE["data"]}))
    profile_selection_input.write_text(json.dumps({"profile_id": IDENTIFIER}))
    template_file = tmp_path / "synthetic.docx"
    template_file.write_bytes(docx_bytes)
    template_input = tmp_path / "template.json"
    template_update_input = tmp_path / "template-update.json"
    template_selection_input = tmp_path / "template-selection.json"
    template_input.write_text(json.dumps({"data": TEMPLATE["data"]}))
    template_update_input.write_text(json.dumps({"expected_revision": 1, "data": TEMPLATE["data"]}))
    template_selection_input.write_text(json.dumps({"template_id": IDENTIFIER}))
    scan_file = tmp_path / "synthetic.pdf"
    scan_file.write_bytes(pdf_bytes)
    evidence_input = tmp_path / "source.json"
    evidence_input.write_text(json.dumps({"task_certificate_id": IDENTIFIER, "page": 1}))
    sandbox_html = tmp_path / "prototype.html"
    sandbox_html.write_text("<html>synthetic</html>")
    sandbox_render_input = tmp_path / "sandbox-render.json"
    sandbox_render_input.write_text(
        json.dumps(
            {
                "purpose": "prototype_offline",
                "extraction_job_id": IDENTIFIER,
                "task_feature_id": IDENTIFIER,
                "expected_feature_revision_id": IDENTIFIER,
                "html_sha256": hashlib.sha256(sandbox_html.read_bytes()).hexdigest(),
                "html_size_bytes": len(sandbox_html.read_bytes()),
            }
        )
    )
    sandbox_capture_input = tmp_path / "sandbox-capture.json"
    sandbox_capture_input.write_text(
        json.dumps(
            {
                "purpose": "vendor_capture",
                "extraction_job_id": IDENTIFIER,
                "task_resource_id": IDENTIFIER,
                "expected_product_revision_id": IDENTIFIER,
                "source_field": "official_url",
                "expected_source_url_sha256": "a" * 64,
                "format": "web",
                "capture_key": IDENTIFIER,
            }
        )
    )
    card_create_input = tmp_path / "card-create.json"
    card_update_input = tmp_path / "card-update.json"
    card_classify_input = tmp_path / "card-classify.json"
    disposition_input = tmp_path / "card-disposition.json"
    redaction_input = tmp_path / "redaction.json"
    card_content = {
        "response_kind": "commitment",
        "response_text": "We commit to the stated delivery date.",
        "deviation": "none",
        "deviation_note": "The offered delivery date is identical.",
        "evidence": [],
    }
    card_create_input.write_text(
        json.dumps(
            {
                "extraction_job_id": IDENTIFIER,
                "requirement_id": IDENTIFIER,
                "content": card_content,
            }
        )
    )
    card_update_input.write_text(json.dumps({"expected_revision": 1, "content": card_content}))
    card_classify_input.write_text(
        json.dumps(
            {"expected_revision": 1, "review_domain": "technical", "reason": "Assigned owner"}
        )
    )
    disposition_input.write_text(
        json.dumps(
            {
                "extraction_job_id": IDENTIFIER,
                "items": [
                    {
                        "requirement_id": IDENTIFIER,
                        "expected_revision": 1,
                        "disposition": "respond",
                        "reason": "Requires a response",
                    }
                ],
            }
        )
    )
    redaction_input.write_text(
        json.dumps({"expected_revision": 1, "model_redaction_enabled": False})
    )
    export_binding_input = tmp_path / "export-binding.json"
    export_prepare_input = tmp_path / "export-prepare.json"
    export_release_input = tmp_path / "export-release.json"
    export_binding_input.write_text(
        json.dumps(
            {
                "template_revision_id": IDENTIFIER,
                "expected_template_sha256": "b" * 64,
                "expected_static_content_hash": "d" * 64,
                "sections": EXPORT_SECTIONS,
            }
        )
    )
    export_prepare_input.write_text(
        json.dumps(
            {
                "draft_id": IDENTIFIER,
                "task_template_id": IDENTIFIER,
                "binding_id": IDENTIFIER,
                "mode": "review_copy",
                "expected_input_hash": "e" * 64,
            }
        )
    )
    export_release_input.write_text(
        json.dumps(
            {
                "expected_input_hash": "e" * 64,
                "expected_candidate_sha256": "f" * 64,
            }
        )
    )
    platform_model_input = tmp_path / "platform-model.json"
    platform_model_input.write_text(
        json.dumps(
            {
                k: v
                for k, v in PLATFORM_MODEL.items()
                if k not in {"revision", "updated_by", "updated_at", "credential_configured"}
            }
        )
    )
    monkeypatch.setenv("BID_SETUP_TOKEN", "synthetic-fixture-link")
    monkeypatch.setenv("BID_CARD_CODE", "SYNT-HETI-CFIX-TURE")
    common = ["--state", str(tmp_path / "session.enc")]
    commands = {
        "login": ["login", "--email", "synthetic@example.test", "--org", IDENTIFIER],
        "org use": ["org", "use", IDENTIFIER],
        "task create": ["task", "create", "--name", "Synthetic task"],
        "task list": ["task", "list"],
        "sandbox render": [
            "sandbox", "render", "--task", IDENTIFIER, "--html", str(sandbox_html),
            "--input", str(sandbox_render_input),
        ],
        "sandbox capture": [
            "sandbox", "capture", "--task", IDENTIFIER, "--input", str(sandbox_capture_input),
        ],
        "sandbox list": ["sandbox", "list", "--task", IDENTIFIER],
        "sandbox show": ["sandbox", "show", "--id", IDENTIFIER],
        "sandbox download": [
            "sandbox", "download", "--artifact", IDENTIFIER,
            "--output", str(tmp_path / "sandbox-artifact.bin"),
        ],
        "task redaction set": [
            "task", "redaction", "set", "--task", IDENTIFIER, "--input", str(redaction_input),
        ],
        "card list": ["card", "list", "--task", IDENTIFIER, "--job", IDENTIFIER],
        "card show": ["card", "show", "--id", IDENTIFIER, "--history"],
        "card create": [
            "card", "create", "--task", IDENTIFIER, "--input", str(card_create_input),
        ],
        "card generate": [
            "card", "generate", "--task", IDENTIFIER, "--job", IDENTIFIER,
            "--requirement", IDENTIFIER, "--requirement", IDENTIFIER_2,
            "--reasoning", "high", "--retry", "--wait",
        ],
        "card update": [
            "card", "update", "--id", IDENTIFIER, "--input", str(card_update_input),
        ],
        "card classify": [
            "card", "classify", "--id", IDENTIFIER, "--input", str(card_classify_input),
        ],
        "card disposition": [
            "card", "disposition", "--task", IDENTIFIER, "--input", str(disposition_input),
        ],
        "card submit": [
            "card", "submit", "--id", IDENTIFIER, "--expected-revision", "1",
        ],
        "card withdraw": [
            "card", "withdraw", "--id", IDENTIFIER, "--expected-revision", "1", "--reason", "Edit",
        ],
        "card confirm": [
            "card", "confirm", "--id", IDENTIFIER, "--expected-revision", "1",
            "--evidence", IDENTIFIER, "--reviewed-warning", "proof_material_required",
            "--reason", "Reviewed source page",
        ],
        "card reject": [
            "card", "reject", "--id", IDENTIFIER, "--expected-revision", "1", "--reason", "Incorrect",
        ],
        "card needs-material": [
            "card", "needs-material", "--id", IDENTIFIER, "--expected-revision", "1",
            "--reason", "Certificate page is missing",
        ],
        "card reopen": [
            "card", "reopen", "--id", IDENTIFIER, "--expected-revision", "1",
            "--reason", "Material changed",
        ],
        "draft": ["draft", "--task", IDENTIFIER, "--job", IDENTIFIER, "--wait"],
        "draft show": ["draft", "show", "--id", IDENTIFIER],
        "draft list": ["draft", "list", "--task", IDENTIFIER, "--job", IDENTIFIER],
        "export binding create": ["export", "binding", "create", "--input", str(export_binding_input)],
        "export binding list": ["export", "binding", "list", "--template-revision", IDENTIFIER],
        "export prepare": ["export", "prepare", "--task", IDENTIFIER, "--input", str(export_prepare_input), "--wait"],
        "export run show": ["export", "run", "show", "--id", IDENTIFIER],
        "export release": ["export", "release", "--run", IDENTIFIER, "--input", str(export_release_input)],
        "export list": ["export", "list", "--task", IDENTIFIER],
        "export show": ["export", "show", "--id", IDENTIFIER],
        "export download": ["export", "download", "--id", IDENTIFIER, "--output", str(tmp_path / "export.docx")],
        "resource product add": ["resource", "product", "add", "--input", str(product_input)],
        "resource product list": ["resource", "product", "list"],
        "resource product update": [
            "resource",
            "product",
            "update",
            "--id",
            IDENTIFIER,
            "--input",
            str(update_input),
        ],
        "task resource add": [
            "task",
            "resource",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(selection_input),
        ],
        "task resource list": ["task", "resource", "list", "--task", IDENTIFIER],
        "resource feature add": ["resource", "feature", "add", "--input", str(feature_input)],
        "resource feature list": ["resource", "feature", "list"],
        "resource feature update": [
            "resource",
            "feature",
            "update",
            "--id",
            IDENTIFIER,
            "--input",
            str(feature_update_input),
        ],
        "task feature add": [
            "task",
            "feature",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(feature_selection_input),
        ],
        "task feature list": ["task", "feature", "list", "--task", IDENTIFIER],
        "resource certificate add": [
            "resource",
            "certificate",
            "add",
            "--input",
            str(certificate_input),
        ],
        "resource certificate list": ["resource", "certificate", "list"],
        "resource certificate update": [
            "resource",
            "certificate",
            "update",
            "--id",
            IDENTIFIER,
            "--input",
            str(certificate_update_input),
        ],
        "task certificate add": [
            "task",
            "certificate",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(certificate_selection_input),
        ],
        "task certificate list": ["task", "certificate", "list", "--task", IDENTIFIER],
        "resource profile add": [
            "resource",
            "profile",
            "add",
            "--input",
            str(profile_input),
        ],
        "resource profile list": ["resource", "profile", "list"],
        "resource profile update": [
            "resource",
            "profile",
            "update",
            "--id",
            IDENTIFIER,
            "--input",
            str(profile_update_input),
        ],
        "task profile add": [
            "task",
            "profile",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(profile_selection_input),
        ],
        "task profile list": ["task", "profile", "list", "--task", IDENTIFIER],
        "resource certificate file add": [
            "resource",
            "certificate",
            "file",
            "add",
            "--id",
            IDENTIFIER,
            "--input",
            str(certificate_update_input),
            "--file",
            str(scan_file),
        ],
        "resource certificate file list": ["resource", "certificate", "file", "list"],
        "task certificate file list": ["task", "certificate", "file", "list", "--task", IDENTIFIER],
        "resource certificate file download": [
            "resource",
            "certificate",
            "file",
            "download",
            "--revision",
            IDENTIFIER,
            "--output",
            str(tmp_path / "download.pdf"),
        ],
        "resource template add": [
            "resource",
            "template",
            "add",
            "--input",
            str(template_input),
            "--file",
            str(template_file),
        ],
        "resource template list": ["resource", "template", "list"],
        "resource template update": [
            "resource",
            "template",
            "update",
            "--id",
            IDENTIFIER,
            "--input",
            str(template_update_input),
            "--file",
            str(template_file),
        ],
        "task template add": [
            "task",
            "template",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(template_selection_input),
        ],
        "task template list": ["task", "template", "list", "--task", IDENTIFIER],
        "resource template download": [
            "resource",
            "template",
            "download",
            "--revision",
            IDENTIFIER,
            "--output",
            str(tmp_path / "download.docx"),
        ],
        "tender upload": ["tender", "upload", "--task", IDENTIFIER, "--file", str(source)],
        "tender parse": ["tender", "parse", "--document", IDENTIFIER],
        "req extract": ["req", "extract", "--document", IDENTIFIER, "--reasoning", "high"],
        "req list": ["req", "list", "--task", IDENTIFIER, "--job", IDENTIFIER],
        "req history": ["req", "history", "--task", IDENTIFIER],
        "req repair-citations": ["req", "repair-citations", "--task", IDENTIFIER, "--job", IDENTIFIER],
        "req repair-citations execute": ["req", "repair-citations", "--task", IDENTIFIER, "--job", IDENTIFIER, "--execute", "--expected-preview", "a" * 64, "--reason", "Reviewed synthetic citation repair"],
        "job status": ["job", "status", IDENTIFIER],
        "job wait": ["job", "wait", IDENTIFIER],
        "job cancel": ["job", "cancel", IDENTIFIER],
        "token create": [
            "token",
            "create",
            "--name",
            "Synthetic token",
            "--scope",
            "task:read",
            "--expires-at",
            "2030-01-01T00:00:00+00:00",
            "--output",
            str(tmp_path / "token.enc"),
        ],
        "evidence source add": [
            "evidence",
            "source",
            "add",
            "--task",
            IDENTIFIER,
            "--input",
            str(evidence_input),
        ],
        "evidence source list": ["evidence", "source", "list", "--task", IDENTIFIER],
        "evidence source download": [
            "evidence",
            "source",
            "download",
            "--id",
            IDENTIFIER,
            "--output",
            str(tmp_path / "source.png"),
        ],
        "platform login": ["platform", "login", "--email", "ops@example.test", "--totp", "123456"],
        "platform org list": ["platform", "org", "list"],
        "platform org create": [
            "platform", "org", "create", "--name", "Synthetic tenant", "--admin-email", "boss@example.test",
        ],
        "platform org set-active": ["platform", "org", "set-active", "--id", IDENTIFIER, "--inactive"],
        "platform model list": ["platform", "model", "list"],
        "platform model set": ["platform", "model", "set", "--input", str(platform_model_input)],
        "platform model test": ["platform", "model", "test", "--id", "opus-standard"],
        "platform usage": ["platform", "usage", "--from", "2026-10", "--to", "2026-10"],
        "platform audit": ["platform", "audit", "--limit", "5"],
        "auth setup-password": ["auth", "setup-password"],
        "auth orgs": ["auth", "orgs", "--email", "boss@example.test"],
        "platform org balance": ["platform", "org", "balance", "--id", IDENTIFIER, "--add", "10", "--reason", "Synthetic credit"],
        "platform card create": ["platform", "card", "create", "--count", "1", "--face-value", "10", "--output", str(tmp_path / "cards.csv")],
        "platform card list": ["platform", "card", "list", "--status", "active"],
        "platform card void": ["platform", "card", "void", "--id", IDENTIFIER],
        "billing balance": ["billing", "balance"],
        "billing redeem": ["billing", "redeem"],
        "schema": ["schema"],
    }  # fmt: skip
    actual = {}
    partial_commands = {
        "card generate",
        "draft",
        "draft show",
        "export download",
        "export release",
        "job status",
        "job wait",
    }
    for name, args in commands.items():
        try:
            main([*common, *args, "--json"])
        except SystemExit as error:
            assert name in partial_commands and error.code == 5
        else:
            assert name not in partial_commands
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        if "encrypted_token_file" in body["data"]:
            body["data"]["encrypted_token_file"] = "<encrypted-token-file>"
        if "output_path" in body["data"]:
            body["data"]["output_path"] = "<download-output>"
        assert "synthetic-fixture-token" not in json.dumps(body)
        assert "SYNT-HETI-CFIX-TURE" not in json.dumps(body)
        assert "synthetic-fixture-session" not in json.dumps(body)
        actual[name] = body
    snapshot = Path(__file__).with_name("snapshots") / "cli-v1.json"
    import os

    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    assert actual == json.loads(snapshot.read_text())
