import asyncio
import json
import os
import socket
import subprocess
import sys

import httpx
import pytest
from conftest import PASSWORD


@pytest.fixture(scope="session")
def real_queue_schema(admin_engine):
    subprocess.run(
        [sys.executable, "-m", "app.admin", "init-db"], check=True, stdout=subprocess.DEVNULL
    )


async def execute_cli(arguments: list[str], environment: dict) -> tuple[int, dict]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bid_cli.main",
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode is not None
    try:
        body = json.loads(stdout)
    except ValueError as exc:
        raise AssertionError("CLI did not emit one JSON object: " + stderr.decode()[:500]) from exc
    return process.returncode, body


async def test_real_background_worker_and_both_cli_modes(
    tenants, real_queue_schema, tmp_path, pdf_bytes, docx_bytes
):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "BID_DATA_DIR": str(tmp_path / "files"), "BID_PASSWORD": PASSWORD}
    pdf = tmp_path / "fixture.pdf"
    pdf.write_bytes(pdf_bytes)
    worker_log, api_log = (tmp_path / "worker.log").open("w"), (tmp_path / "api.log").open("w")
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.jobs.worker",
        env=environment,
        stdout=worker_log,
        stderr=worker_log,
    )
    server = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--no-access-log",
        env=environment,
        stdout=api_log,
        stderr=api_log,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            for _ in range(100):
                try:
                    if (await http.get("/health")).status_code == 200:
                        break
                except httpx.NetworkError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Local test API did not start")
        for mode in ("local", "remote"):
            base = [
                "--mode",
                mode,
                "--server",
                f"http://127.0.0.1:{port}",
                "--state",
                str(tmp_path / f"{mode}.enc"),
            ]
            status, logged = await execute_cli(
                [
                    *base,
                    "login",
                    "--email",
                    "a@example.test",
                    "--org",
                    str(tenants["orgs"][0]),
                    "--json",
                ],
                environment,
            )
            assert (
                status == 0 and logged["data"]["authenticated"] and "session" not in logged["data"]
            )
            status, task = await execute_cli(
                [
                    *base,
                    "task",
                    "create",
                    "--name",
                    "Synthetic CLI task",
                    "--tender",
                    str(pdf),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and task["data"]["document_id"]
            metadata = {
                "name": "Synthetic CLI product",
                "vendor": "Synthetic vendor",
                "model": "Exact A",
            }
            product_input = tmp_path / f"{mode}-product.json"
            product_input.write_text(json.dumps({"data": metadata}))
            status, product = await execute_cli(
                [*base, "resource", "product", "add", "--input", str(product_input), "--json"],
                environment,
            )
            assert status == 0 and product["data"]["revision"] == 1
            product_id = product["data"]["product_id"]
            selection_input = tmp_path / f"{mode}-selection.json"
            selection_input.write_text(json.dumps({"product_id": product_id}))
            selection_args = [
                *base,
                "task",
                "resource",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(selection_input),
                "--json",
            ]
            status, selected = await execute_cli(selection_args, environment)
            assert status == 0 and selected["data"]["revision"] == 1
            product_input.write_text(
                json.dumps({"expected_revision": 1, "data": {**metadata, "model": "Exact B"}})
            )
            update_args = [
                *base,
                "resource",
                "product",
                "update",
                "--id",
                product_id,
                "--input",
                str(product_input),
                "--json",
            ]
            status, revised = await execute_cli(update_args, environment)
            assert status == 0 and revised["data"]["revision"] == 2
            status, conflict = await execute_cli(update_args, environment)
            assert status == 2 and conflict["data"]["error"]["code"] == "revision_conflict"
            listing_args = [*base, "task", "resource", "list", "--task", task["data"]["id"]]
            status, fixed = await execute_cli([*listing_args, "--json"], environment)
            assert status == 0 and fixed["items"][0]["data"]["model"] == "Exact A"
            status, replaced = await execute_cli(selection_args, environment)
            assert status == 0 and replaced["data"]["revision"] == 2
            assert replaced["data"]["replaced_snapshot_id"] == selected["data"]["id"]
            status, repeated = await execute_cli(selection_args, environment)
            assert status == 0 and repeated["data"]["duplicate"]
            status, history = await execute_cli([*listing_args, "--history", "--json"], environment)
            assert status == 0 and [row["revision"] for row in history["items"]] == [1, 2]
            status, revisions = await execute_cli(
                [*base, "resource", "product", "list", "--id", product_id, "--history", "--json"],
                environment,
            )
            assert status == 0 and [row["revision"] for row in revisions["items"]] == [1, 2]
            feature_metadata = {
                "product_id": product_id,
                "name": "Synthetic CLI feature",
                "description": "Synthetic declaration, no verified screenshot",
                "status": "planned",
            }
            feature_input = tmp_path / f"{mode}-feature.json"
            feature_input.write_text(json.dumps({"data": feature_metadata}))
            status, feature = await execute_cli(
                [*base, "resource", "feature", "add", "--input", str(feature_input), "--json"],
                environment,
            )
            assert status == 0 and feature["data"]["revision"] == 1 and feature["warnings"]
            feature_id = feature["data"]["feature_id"]
            selection_input.write_text(json.dumps({"feature_id": feature_id}))
            feature_selection_args = [
                *base,
                "task",
                "feature",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(selection_input),
                "--json",
            ]
            status, feature_selected = await execute_cli(feature_selection_args, environment)
            assert status == 0 and feature_selected["data"]["revision"] == 1
            feature_input.write_text(
                json.dumps(
                    {"expected_revision": 1, "data": {**feature_metadata, "status": "developing"}}
                )
            )
            feature_update_args = [
                *base,
                "resource",
                "feature",
                "update",
                "--id",
                feature_id,
                "--input",
                str(feature_input),
                "--json",
            ]
            status, feature_updated = await execute_cli(feature_update_args, environment)
            assert status == 0 and feature_updated["data"]["revision"] == 2
            status, feature_conflict = await execute_cli(feature_update_args, environment)
            assert status == 2 and feature_conflict["data"]["error"]["code"] == "revision_conflict"
            feature_listing_args = [*base, "task", "feature", "list", "--task", task["data"]["id"]]
            status, feature_fixed = await execute_cli(
                [*feature_listing_args, "--json"], environment
            )
            assert status == 0 and feature_fixed["items"][0]["data"]["status"] == "planned"
            status, feature_replaced = await execute_cli(feature_selection_args, environment)
            assert (
                status == 0
                and feature_replaced["data"]["replaced_snapshot_id"]
                == feature_selected["data"]["id"]
            )
            status, feature_duplicate = await execute_cli(feature_selection_args, environment)
            assert status == 0 and feature_duplicate["data"]["duplicate"]
            status, feature_history = await execute_cli(
                [*feature_listing_args, "--history", "--json"], environment
            )
            assert status == 0 and [row["revision"] for row in feature_history["items"]] == [1, 2]
            status, feature_versions = await execute_cli(
                [*base, "resource", "feature", "list", "--id", feature_id, "--history", "--json"],
                environment,
            )
            assert status == 0 and [row["revision"] for row in feature_versions["items"]] == [1, 2]
            certificate_metadata = {
                "kind": "qualification",
                "name": "Synthetic declaration",
                "number": "SYNTHETIC-ONLY",
                "valid_from": "2026-01-01",
                "valid_until": "2026-12-31",
            }
            certificate_input = tmp_path / f"{mode}-certificate.json"
            certificate_input.write_text(json.dumps({"data": certificate_metadata}))
            status, certificate = await execute_cli(
                [
                    *base,
                    "resource",
                    "certificate",
                    "add",
                    "--input",
                    str(certificate_input),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and certificate["data"]["revision"] == 1 and certificate["warnings"]
            certificate_id = certificate["data"]["certificate_id"]
            selection_input.write_text(json.dumps({"certificate_id": certificate_id}))
            certificate_selection_args = [
                *base,
                "task",
                "certificate",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(selection_input),
                "--json",
            ]
            status, certificate_selected = await execute_cli(
                certificate_selection_args, environment
            )
            assert status == 0 and certificate_selected["data"]["revision"] == 1
            certificate_input.write_text(
                json.dumps(
                    {
                        "expected_revision": 1,
                        "data": {**certificate_metadata, "valid_until": "2027-12-31"},
                    }
                )
            )
            certificate_update_args = [
                *base,
                "resource",
                "certificate",
                "update",
                "--id",
                certificate_id,
                "--input",
                str(certificate_input),
                "--json",
            ]
            status, certificate_updated = await execute_cli(certificate_update_args, environment)
            assert status == 0 and certificate_updated["data"]["revision"] == 2
            status, certificate_conflict = await execute_cli(certificate_update_args, environment)
            assert (
                status == 2 and certificate_conflict["data"]["error"]["code"] == "revision_conflict"
            )
            certificate_listing_args = [
                *base,
                "task",
                "certificate",
                "list",
                "--task",
                task["data"]["id"],
            ]
            status, certificate_fixed = await execute_cli(
                [*certificate_listing_args, "--json"], environment
            )
            assert (
                status == 0 and certificate_fixed["items"][0]["data"]["valid_until"] == "2026-12-31"
            )
            status, certificate_replaced = await execute_cli(
                certificate_selection_args, environment
            )
            assert (
                status == 0
                and certificate_replaced["data"]["replaced_snapshot_id"]
                == certificate_selected["data"]["id"]
            )
            status, certificate_duplicate = await execute_cli(
                certificate_selection_args, environment
            )
            assert status == 0 and certificate_duplicate["data"]["duplicate"]
            status, certificate_history = await execute_cli(
                [*certificate_listing_args, "--history", "--json"], environment
            )
            assert status == 0 and [row["revision"] for row in certificate_history["items"]] == [
                1,
                2,
            ]
            status, certificate_versions = await execute_cli(
                [
                    *base,
                    "resource",
                    "certificate",
                    "list",
                    "--id",
                    certificate_id,
                    "--history",
                    "--json",
                ],
                environment,
            )
            assert status == 0 and [row["revision"] for row in certificate_versions["items"]] == [
                1,
                2,
            ]
            status, inspected = await execute_cli(
                [*certificate_listing_args, "--history", "--as-of", "2027-01-01", "--json"],
                environment,
            )
            assert status == 0 and sorted(
                value["state"] for value in inspected["data"]["validity_by_revision"].values()
            ) == ["expired", "valid"]
            profile_metadata = {
                "name": "Synthetic organization declaration",
                "registration_details": None,
                "performance_summary": "Synthetic A",
                "standard_wording": None,
            }
            profile_input = tmp_path / f"{mode}-profile.json"
            profile_input.write_text(json.dumps({"data": profile_metadata}))
            status, profile = await execute_cli(
                [
                    *base,
                    "resource",
                    "profile",
                    "add",
                    "--input",
                    str(profile_input),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and profile["data"]["revision"] == 1 and profile["warnings"]
            profile_id = profile["data"]["profile_id"]
            selection_input.write_text(json.dumps({"profile_id": profile_id}))
            profile_selection_args = [
                *base,
                "task",
                "profile",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(selection_input),
                "--json",
            ]
            status, profile_selected = await execute_cli(profile_selection_args, environment)
            assert status == 0 and profile_selected["data"]["revision"] == 1
            profile_input.write_text(
                json.dumps(
                    {
                        "expected_revision": 1,
                        "data": {**profile_metadata, "performance_summary": "Synthetic B"},
                    }
                )
            )
            profile_update_args = [
                *base,
                "resource",
                "profile",
                "update",
                "--id",
                profile_id,
                "--input",
                str(profile_input),
                "--json",
            ]
            status, profile_updated = await execute_cli(profile_update_args, environment)
            assert status == 0 and profile_updated["data"]["revision"] == 2
            status, profile_conflict = await execute_cli(profile_update_args, environment)
            assert status == 2 and profile_conflict["data"]["error"]["code"] == "revision_conflict"
            profile_listing_args = [
                *base,
                "task",
                "profile",
                "list",
                "--task",
                task["data"]["id"],
            ]
            status, profile_fixed = await execute_cli(
                [*profile_listing_args, "--json"], environment
            )
            assert (
                status == 0
                and profile_fixed["items"][0]["data"]["performance_summary"] == "Synthetic A"
            )
            status, profile_replaced = await execute_cli(profile_selection_args, environment)
            assert (
                status == 0
                and profile_replaced["data"]["replaced_snapshot_id"]
                == profile_selected["data"]["id"]
            )
            status, profile_duplicate = await execute_cli(profile_selection_args, environment)
            assert status == 0 and profile_duplicate["data"]["duplicate"]
            status, profile_history = await execute_cli(
                [*profile_listing_args, "--history", "--json"], environment
            )
            assert status == 0 and [row["revision"] for row in profile_history["items"]] == [
                1,
                2,
            ]
            status, profile_versions = await execute_cli(
                [
                    *base,
                    "resource",
                    "profile",
                    "list",
                    "--id",
                    profile_id,
                    "--history",
                    "--json",
                ],
                environment,
            )
            assert status == 0 and [row["revision"] for row in profile_versions["items"]] == [
                1,
                2,
            ]
            template_source = tmp_path / f"{mode}-synthetic.docx"
            template_source.write_bytes(docx_bytes)
            template_input = tmp_path / f"{mode}-template.json"
            template_metadata = {
                "name": "Synthetic template",
                "project_types": None,
                "chapters": None,
            }
            template_input.write_text(json.dumps({"data": template_metadata}))
            status, template = await execute_cli(
                [
                    *base,
                    "resource",
                    "template",
                    "add",
                    "--input",
                    str(template_input),
                    "--file",
                    str(template_source),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and template["data"]["revision"] == 1 and template["warnings"]
            template_id, template_revision_id = (
                template["data"]["template_id"],
                template["data"]["id"],
            )
            selection_input.write_text(json.dumps({"template_id": template_id}))
            template_selection_args = [
                *base,
                "task",
                "template",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(selection_input),
                "--json",
            ]
            status, template_selected = await execute_cli(template_selection_args, environment)
            assert status == 0 and template_selected["data"]["revision"] == 1
            from io import BytesIO

            from docx import Document

            newer = Document(BytesIO(docx_bytes))
            newer.add_paragraph("Synthetic revision two")
            buffer = BytesIO()
            newer.save(buffer)
            template_source.write_bytes(buffer.getvalue())
            template_input.write_text(
                json.dumps(
                    {
                        "expected_revision": 1,
                        "data": {**template_metadata, "name": "Synthetic updated template"},
                    }
                )
            )
            template_update_args = [
                *base,
                "resource",
                "template",
                "update",
                "--id",
                template_id,
                "--input",
                str(template_input),
                "--file",
                str(template_source),
                "--json",
            ]
            status, template_updated = await execute_cli(template_update_args, environment)
            assert status == 0 and template_updated["data"]["revision"] == 2
            status, template_conflict = await execute_cli(template_update_args, environment)
            assert status == 4 and template_conflict["data"]["error"]["code"] == "revision_conflict"
            template_list_args = [*base, "task", "template", "list", "--task", task["data"]["id"]]
            status, template_fixed = await execute_cli([*template_list_args, "--json"], environment)
            assert (
                status == 0
                and template_fixed["items"][0]["template_revision_id"] == template_revision_id
            )
            status, template_replaced = await execute_cli(template_selection_args, environment)
            assert (
                status == 0
                and template_replaced["data"]["replaced_snapshot_id"]
                == template_selected["data"]["id"]
            )
            status, template_repeat = await execute_cli(template_selection_args, environment)
            assert status == 0 and template_repeat["data"]["duplicate"]
            status, template_history = await execute_cli(
                [*template_list_args, "--history", "--json"], environment
            )
            assert status == 0 and [entry["revision"] for entry in template_history["items"]] == [
                1,
                2,
            ]
            status, template_versions = await execute_cli(
                [*base, "resource", "template", "list", "--id", template_id, "--history", "--json"],
                environment,
            )
            assert status == 0 and [entry["revision"] for entry in template_versions["items"]] == [
                1,
                2,
            ]
            for identifier, expected in [
                (template_revision_id, docx_bytes),
                (template_updated["data"]["id"], buffer.getvalue()),
            ]:
                output = tmp_path.resolve() / f"{mode}-{identifier}.docx"
                arguments = [
                    *base,
                    "resource",
                    "template",
                    "download",
                    "--revision",
                    identifier,
                    "--output",
                    str(output),
                    "--json",
                ]
                status, downloaded = await execute_cli(arguments, environment)
                assert (
                    status == 0
                    and downloaded["data"]["template_revision_id"] == identifier
                    and output.read_bytes() == expected
                )
                status, exists = await execute_cli(arguments, environment)
                assert (
                    status == 2
                    and exists["data"]["error"]["code"] == "invalid_output_path"
                    and output.read_bytes() == expected
                )
            scan_source = tmp_path / f"{mode}-certificate.pdf"
            scan_source.write_bytes(pdf_bytes)
            scan_input = tmp_path / f"{mode}-certificate-file.json"
            scan_metadata = {**certificate_metadata, "valid_until": "2027-12-31"}
            scan_input.write_text(json.dumps({"expected_revision": 2, "data": scan_metadata}))
            scan_add_args = [
                *base,
                "resource",
                "certificate",
                "file",
                "add",
                "--id",
                certificate_id,
                "--input",
                str(scan_input),
                "--file",
                str(scan_source),
                "--json",
            ]
            status, scan = await execute_cli(scan_add_args, environment)
            assert status == 0 and scan["data"]["revision"] == 3
            scan_revision = scan["data"]["certificate_revision_id"]
            status, conflict = await execute_cli(scan_add_args, environment)
            assert status == 4 and conflict["data"]["error"]["code"] == "revision_conflict"
            scan_task_args = [
                *base,
                "task",
                "certificate",
                "file",
                "list",
                "--task",
                task["data"]["id"],
            ]
            status, old_scan = await execute_cli([*scan_task_args, "--json"], environment)
            assert (
                status == 0
                and old_scan["items"][0]["revision"] == 2
                and old_scan["items"][0]["file"] is None
            )
            selection_input.write_text(json.dumps({"certificate_id": certificate_id}))
            status, scan_selected = await execute_cli(certificate_selection_args, environment)
            assert status == 0 and scan_selected["data"]["revision"] == 3
            status, scan_repeated = await execute_cli(certificate_selection_args, environment)
            assert status == 0 and scan_repeated["data"]["duplicate"]
            scan_input.write_text(
                json.dumps(
                    {
                        "expected_revision": 3,
                        "data": {**scan_metadata, "name": "Synthetic metadata only"},
                    }
                )
            )
            status, plain = await execute_cli(
                [
                    *base,
                    "resource",
                    "certificate",
                    "update",
                    "--id",
                    certificate_id,
                    "--input",
                    str(scan_input),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and plain["data"]["revision"] == 4
            status, current = await execute_cli(
                [
                    *base,
                    "resource",
                    "certificate",
                    "file",
                    "list",
                    "--id",
                    certificate_id,
                    "--json",
                ],
                environment,
            )
            assert status == 0 and not current["items"] and current["warnings"]
            status, fixed = await execute_cli([*scan_task_args, "--json"], environment)
            assert (
                status == 0
                and fixed["items"][0]["certificate_revision_id"] == scan_revision
                and fixed["items"][0]["file"] == scan["data"]["file"]
            )
            status, file_history = await execute_cli(
                [
                    *base,
                    "resource",
                    "certificate",
                    "file",
                    "list",
                    "--id",
                    certificate_id,
                    "--history",
                    "--json",
                ],
                environment,
            )
            assert status == 0 and [v["revision"] for v in file_history["items"]] == [3]
            output = tmp_path.resolve() / f"{mode}-certificate-download.pdf"
            scan_download_args = [
                *base,
                "resource",
                "certificate",
                "file",
                "download",
                "--revision",
                scan_revision,
                "--output",
                str(output),
                "--json",
            ]
            status, downloaded = await execute_cli(scan_download_args, environment)
            assert (
                status == 0
                and output.read_bytes() == pdf_bytes
                and output.stat().st_mode & 0o777 == 0o600
            )
            status, unchanged = await execute_cli(scan_download_args, environment)
            assert status == 2 and output.read_bytes() == pdf_bytes
            status, plain_selected = await execute_cli(certificate_selection_args, environment)
            assert status == 0 and plain_selected["data"]["revision"] == 4
            status, scan_history = await execute_cli(
                [*scan_task_args, "--history", "--json"], environment
            )
            assert status == 0 and [v["revision"] for v in scan_history["items"]] == [1, 2, 3, 4]
            assert [v["file"] is None for v in scan_history["items"]] == [True, True, False, True]
            # Real PDF page source through each real CLI mode, with fixed old revision.
            selection_input.write_text(
                json.dumps({"certificate_id": certificate_id, "revision": 3})
            )
            status, source_choice = await execute_cli(certificate_selection_args, environment)
            assert status == 0
            source_input = tmp_path / f"{mode}-source.json"
            source_input.write_text(
                json.dumps({"task_certificate_id": source_choice["data"]["id"], "page": 1})
            )
            source_add = [
                *base,
                "evidence",
                "source",
                "add",
                "--task",
                task["data"]["id"],
                "--input",
                str(source_input),
                "--json",
            ]
            status, archived = await execute_cli(source_add, environment)
            assert status == 0 and not archived["data"]["duplicate"]
            source = archived["data"]["source"]
            assert (
                source["certificate_revision_id"] == scan_revision
                and source["confirmed_by"] is None
                and source["eligible_for_draft_export"] is False
            )
            status, repeated = await execute_cli(source_add, environment)
            assert (
                status == 0
                and repeated["data"]["duplicate"]
                and repeated["data"]["source"]["id"] == source["id"]
            )
            source_input.write_text(
                json.dumps({"task_certificate_id": source_choice["data"]["id"], "page": 2})
            )
            status, other_page = await execute_cli(source_add, environment)
            assert status == 0 and other_page["data"]["source"]["id"] != source["id"]
            png_output = tmp_path.resolve() / f"{mode}-source-download.png"
            source_download = [
                *base,
                "evidence",
                "source",
                "download",
                "--id",
                source["id"],
                "--output",
                str(png_output),
                "--json",
            ]
            status, preview = await execute_cli(source_download, environment)
            assert status == 0 and png_output.stat().st_mode & 0o777 == 0o600
            import pymupdf

            actual = pymupdf.Pixmap(png_output.read_bytes())
            with pymupdf.open(stream=pdf_bytes, filetype="pdf") as fixture_pdf:
                expected = fixture_pdf[0].get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
                assert (actual.width, actual.height, actual.samples) == (
                    expected.width,
                    expected.height,
                    expected.samples,
                )
            status, no_overwrite = await execute_cli(source_download, environment)
            assert status == 2
            selection_input.write_text(json.dumps({"certificate_id": certificate_id}))
            status, _ = await execute_cli(certificate_selection_args, environment)
            assert status == 0
            source_list = [*base, "evidence", "source", "list", "--task", task["data"]["id"]]
            status, active_sources = await execute_cli([*source_list, "--json"], environment)
            assert status == 0 and active_sources["items"] == []
            status, old_sources = await execute_cli(
                [*source_list, "--history", "--json"], environment
            )
            assert (
                status == 0
                and len(old_sources["items"]) == 2
                and all(not row["active_selection"] for row in old_sources["items"])
            )
            document = task["data"]["document_id"]
            status, parsed = await execute_cli(
                [
                    *base,
                    "tender",
                    "parse",
                    "--document",
                    document,
                    "--wait",
                    "--timeout",
                    "20",
                    "--json",
                ],
                environment,
            )
            assert status == 0 and len(parsed["items"]) == 2 and parsed["data"]["pages"] == 2
            assert all(item["citation_verified"] for item in parsed["items"])
            status, duplicate = await execute_cli(
                [
                    *base,
                    "tender",
                    "upload",
                    "--task",
                    task["data"]["id"],
                    "--file",
                    str(pdf),
                    "--json",
                ],
                environment,
            )
            assert status == 0 and duplicate["data"]["duplicate"]
            status, failed = await execute_cli(
                [
                    *base,
                    "req",
                    "extract",
                    "--document",
                    document,
                    "--wait",
                    "--timeout",
                    "20",
                    "--json",
                ],
                environment,
            )
            assert status == 4 and failed["ok"] is False
            assert failed["data"]["error"]["code"] == "provider_unavailable"
    finally:
        for process in (server, worker):
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        worker_log.close()
        api_log.close()
