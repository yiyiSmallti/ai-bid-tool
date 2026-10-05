import hashlib
import json
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from bid_cli.client import Client, State, new_output_path, save_download
from bid_cli.main import main

IDENTIFIER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(params=("3.0", "4.0"))
def contract_version(request):
    """Version metadata endpoints while preserving signed raw download paths."""
    return request.param


class SyntheticState(State):
    def load(self):
        return {"session": "synthetic-only-session", "org_id": IDENTIFIER}


def download_client(
    tmp_path,
    content,
    *,
    link=None,
    served=None,
    redirect=False,
    wrong_scope=False,
    transport_error=None,
    contract_version="3.0",
):
    client = Client(
        "remote",
        "https://synthetic.example.test",
        SyntheticState(tmp_path / "unused.enc"),
        contract_version=contract_version,
    )
    prefix = "/v4" if contract_version == "4.0" else ""
    path = f"/resources/certificates/revisions/{IDENTIFIER}/file/download"
    descriptor = {
        "name": "synthetic.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "page_count": 2,
        "media_type": "application/pdf",
    }
    calls = []

    def handler(request):
        calls.append(request.url)
        assert request.headers["Authorization"] == "Bearer synthetic-only-session"
        assert request.headers["X-Org-Id"] == IDENTIFIER
        if request.url.path == prefix + "/resources/certificates/files":
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="fixture",
                    items=[
                        {
                            "certificate_revision_id": IDENTIFIER,
                            "org_id": "foreign" if wrong_scope else IDENTIFIER,
                            "file": descriptor,
                        }
                    ],
                ).model_dump(mode="json"),
            )
        if request.url.path == prefix + path + "-link":
            return httpx.Response(
                200,
                json=Result(
                    ok=True,
                    command="fixture",
                    data={"url": link or path + "?signature=synthetic-only", "expires_in": 300},
                ).model_dump(mode="json"),
            )
        assert request.url.path == path and request.url.params["signature"] == "synthetic-only"
        if transport_error is not None:
            raise transport_error("Synthetic transport failure", request=request)
        if redirect:
            return httpx.Response(302, headers={"Location": "https://foreign.example.test/secret"})
        return httpx.Response(200, content=content if served is None else served)

    @asynccontextmanager
    async def transport():
        async with httpx.AsyncClient(
            base_url="https://synthetic.example.test", transport=httpx.MockTransport(handler)
        ) as http:
            yield http

    client.transport = transport
    return client, calls


async def test_certificate_file_download_verified_atomic_private_file_and_no_overwrite(
    contract_version, tmp_path, pdf_bytes
):
    output = tmp_path.resolve() / "new.pdf"
    client, calls = download_client(tmp_path, pdf_bytes, contract_version=contract_version)
    result = await client.download_certificate_file(UUID(IDENTIFIER), output)
    assert (
        result["ok"] and result["data"]["file"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    )
    assert output.read_bytes() == pdf_bytes and output.stat().st_mode & 0o777 == 0o600
    assert len(calls) == 3 and not list(tmp_path.glob(".bid-download-*"))
    with pytest.raises(ServiceError) as error:
        await client.download_certificate_file(UUID(IDENTIFIER), output)
    assert error.value.exit_code == 2 and len(calls) == 3 and output.read_bytes() == pdf_bytes


@pytest.mark.parametrize(
    "link",
    [
        "https://foreign.example.test/secret",
        "//foreign.example.test/secret",
        "/arbitrary?signature=synthetic-only",
        f"/resources/certificates/revisions/{IDENTIFIER}/file/download?signature=one&signature=two",
        f"/resources/certificates/revisions/{IDENTIFIER}/file/download?signature=synthetic-only&extra=true",
        f"/resources/certificates/revisions/{IDENTIFIER}/file/download?signature=synthetic-only#extra",
    ],
)
async def test_certificate_file_download_rejects_external_or_malformed_links(
    contract_version, tmp_path, pdf_bytes, link
):
    client, calls = download_client(
        tmp_path, pdf_bytes, link=link, contract_version=contract_version
    )
    output = tmp_path.resolve() / "new.pdf"
    with pytest.raises(ServiceError) as error:
        await client.download_certificate_file(UUID(IDENTIFIER), output)
    assert error.value.code == "invalid_download_link" and len(calls) == 2 and not output.exists()


@pytest.mark.parametrize("failure", ["hash", "short", "long", "redirect", "foreign"])
async def test_certificate_file_download_never_writes_bad_response(
    contract_version, tmp_path, pdf_bytes, failure
):
    served = {
        "hash": b"x" * len(pdf_bytes),
        "short": pdf_bytes[:-1],
        "long": pdf_bytes + b"x",
    }.get(failure)
    client, calls = download_client(
        tmp_path,
        pdf_bytes,
        served=served,
        redirect=failure == "redirect",
        wrong_scope=failure == "foreign",
        contract_version=contract_version,
    )
    output = tmp_path.resolve() / "new.pdf"
    with pytest.raises(ServiceError) as error:
        await client.download_certificate_file(UUID(IDENTIFIER), output)
    assert error.value.exit_code == 4 and not output.exists()
    assert all(url.host == "synthetic.example.test" for url in calls)


@pytest.mark.parametrize(
    "transport_error", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_certificate_file_download_network_and_partial_connection_errors_are_retryable(
    contract_version, tmp_path, pdf_bytes, transport_error
):
    client, _ = download_client(
        tmp_path, pdf_bytes, transport_error=transport_error, contract_version=contract_version
    )
    output = tmp_path.resolve() / "new.pdf"
    with pytest.raises(ServiceError) as error:
        await client.download_certificate_file(UUID(IDENTIFIER), output)
    assert error.value.code == "network_unavailable" and error.value.exit_code == 3
    assert not output.exists()


@pytest.mark.parametrize(
    "transport_error", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_certificate_file_metadata_transport_errors_share_cli_contract(
    contract_version, tmp_path, transport_error
):
    client = Client(
        "remote",
        "https://synthetic.example.test",
        SyntheticState(tmp_path / "unused.enc"),
        contract_version=contract_version,
    )

    def failure(request):
        raise transport_error("Synthetic transport failure", request=request)

    @asynccontextmanager
    async def transport():
        async with httpx.AsyncClient(
            base_url="https://synthetic.example.test", transport=httpx.MockTransport(failure)
        ) as http:
            yield http

    client.transport = transport
    with pytest.raises(ServiceError) as error:
        await client.request("GET", "/resources/certificates/files")
    assert error.value.code == "network_unavailable" and error.value.exit_code == 3


def test_certificate_file_download_existing_symlink_and_racing_file_are_preserved(tmp_path):
    real = tmp_path.resolve() / "existing.pdf"
    real.write_bytes(b"Synthetic existing")
    link = tmp_path.resolve() / "linked.pdf"
    link.symlink_to(real)
    with pytest.raises(ServiceError):
        new_output_path(link)
    target = tmp_path.resolve() / "race.pdf"
    new_output_path(target)
    target.write_bytes(b"Synthetic racing writer")
    with pytest.raises(ServiceError):
        save_download(target, b"Synthetic downloaded")
    assert (
        target.read_bytes() == b"Synthetic racing writer"
        and real.read_bytes() == b"Synthetic existing"
    )
    assert not list(tmp_path.glob(".bid-download-*"))


@pytest.mark.parametrize(
    "failure",
    ["missing", "invalid_json", "oversized_metadata", "invalid_utf8", "extra", "invalid_file"],
)
def test_certificate_file_cli_invalid_input_before_network(
    failure, monkeypatch, tmp_path, capsys, pdf_bytes
):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("invalid input reached network")

    monkeypatch.setattr(Client, "request", forbidden)
    metadata = tmp_path / "metadata.json"
    file = tmp_path / "synthetic.pdf"
    file.write_bytes(pdf_bytes)
    data = {
        "expected_revision": 1,
        "data": {"kind": "qualification", "name": "Synthetic", "number": "SYNTHETIC-ONLY"},
    }
    if failure == "missing":
        pass
    elif failure == "invalid_json":
        metadata.write_text("invalid")
    elif failure == "oversized_metadata":
        metadata.write_text(" " * (128 * 1024 + 1))
    elif failure == "invalid_utf8":
        metadata.write_bytes(b"\xff")
    else:
        if failure == "extra":
            data["org_id"] = IDENTIFIER
        metadata.write_text(json.dumps(data))
        if failure == "invalid_file":
            file.write_bytes(b"Synthetic invalid file")
    with pytest.raises(SystemExit) as error:
        main(
            [
                "resource",
                "certificate",
                "file",
                "add",
                "--id",
                IDENTIFIER,
                "--input",
                str(metadata),
                "--file",
                str(file),
                "--json",
            ]
        )
    body = json.loads(capsys.readouterr().out)
    assert error.value.code == 2 and not body["ok"] and not calls
