import json
from pathlib import Path

from app.schemas.contracts import Result
from bid_cli.client import Client
from bid_cli.main import main
from cryptography.fernet import Fernet

IDENTIFIER = "00000000-0000-0000-0000-000000000001"
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


async def fake_request(self, method, path, **kwargs):
    # Contract fixtures only: no real service, provider or user data is involved.
    data, items, warnings = {}, [], []
    if path == "/auth/login":
        data = {"session": "synthetic-fixture-session", "org_id": IDENTIFIER, "expires_in": 3600}
    elif path == "/org/current":
        data = {"org_id": IDENTIFIER, "role": "admin"}
    elif path == "/tasks" and method == "POST":
        data = {"id": IDENTIFIER, "name": "Synthetic task", "org_id": IDENTIFIER}
    elif path == "/tasks":
        items = [{"id": IDENTIFIER, "name": "Synthetic task", "org_id": IDENTIFIER}]
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
    elif path.endswith(("/parse", "/extract")):
        data = {"job_id": IDENTIFIER, "status": "queued", "cached": False}
    elif path.endswith("/cancel"):
        data = {"id": IDENTIFIER, "status": "cancelled"}
    elif path.startswith("/jobs/"):
        data = {
            "id": IDENTIFIER,
            "kind": "parse",
            "status": "succeeded",
            "result": {"pages": 1},
            "error": None,
            "attempts": 1,
        }
    elif path.endswith("/requirements"):
        items = [
            {
                "id": IDENTIFIER,
                "text": "Synthetic requirement",
                "source": {
                    "document_id": IDENTIFIER,
                    "chunk_id": IDENTIFIER,
                    "page": 1,
                    "quote": "Synthetic source",
                },
            }
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
    common = ["--state", str(tmp_path / "session.enc")]
    commands = {
        "login": ["login", "--email", "synthetic@example.test", "--org", IDENTIFIER],
        "org use": ["org", "use", IDENTIFIER],
        "task create": ["task", "create", "--name", "Synthetic task"],
        "task list": ["task", "list"],
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
        "req extract": ["req", "extract", "--document", IDENTIFIER],
        "req list": ["req", "list", "--task", IDENTIFIER],
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
        "schema": ["schema"],
    }
    actual = {}
    for name, args in commands.items():
        main([*common, *args, "--json"])
        body = json.loads(capsys.readouterr().out)
        body["duration_ms"] = 0
        if "encrypted_token_file" in body["data"]:
            body["data"]["encrypted_token_file"] = "<encrypted-token-file>"
        if "output_path" in body["data"]:
            body["data"]["output_path"] = "<download-output>"
        assert "synthetic-fixture-token" not in json.dumps(body)
        assert "synthetic-fixture-session" not in json.dumps(body)
        actual[name] = body
    snapshot = Path(__file__).with_name("snapshots") / "cli-v1.json"
    import os

    if os.environ.get("BID_UPDATE_SNAPSHOTS") == "1":
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    assert actual == json.loads(snapshot.read_text())
