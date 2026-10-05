"""Certificate originals composed from images and PDFs, through the API.

Failure modes identified before implementation:
* Privacy: a phone photo's EXIF (GPS, device, time) survives into the stored original
  or into the page preview that becomes an export attachment.
* Wrong page: a photo shows sideways because its EXIF orientation is ignored, a
  requested rotation is lost, or files land out of upload order.
* Memory: a small PNG declares huge dimensions and is decoded anyway.
* Provenance: the uploaded files are not retained unchanged, or a committed original
  later gains, loses or swaps a part.
* Regression: a single unrotated PDF is no longer stored byte for byte as before.
* Isolation: another org or a missing org context sees parts.
* Refusals: unsupported or disguised types, too many files, mismatched options.
"""

import asyncio
import hashlib
import json
import struct
from pathlib import Path

import pymupdf
import pytest
from app.models.entities import CertificateFilePart
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from test_certificate_files import DATA, select_version, setup
from test_evidence_sources import add

MARKER = b"SYNTHETIC-GPS-48.8584N"


def photo(width=60, height=30, orientation=6) -> bytes:
    """A JPEG whose left half is red, carrying an EXIF orientation and a GPS-like marker."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(255)
    for x in range(width // 2):
        for y in range(height):
            pixmap.set_pixel(x, y, (255, 0, 0))
    jpeg = pixmap.tobytes("jpg", jpg_quality=95)
    tiff = (
        b"MM\x00\x2a\x00\x00\x00\x08"
        + struct.pack(">H", 1)
        + struct.pack(">HHII", 0x0112, 3, 1, orientation << 16)
        + b"\x00\x00\x00\x00"
    )
    exif = b"Exif\x00\x00" + tiff + MARKER
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]


def png(width=40, height=20) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(200)
    return pixmap.tobytes("png")


def pdf(pages=2) -> bytes:
    with pymupdf.open() as document:
        for number in range(pages):
            document.new_page().insert_text((40, 60), f"Synthetic license page {number + 1}")
        return document.tobytes()


async def upload_many(api, headers, row, files, parts=None, expected=1):
    metadata = {"expected_revision": expected, "data": DATA}
    if parts is not None:
        metadata["parts"] = parts
    return await api.post(
        f"/resources/certificates/{row['certificate_id']}/file-revisions",
        headers=headers,
        data={"metadata": json.dumps(metadata)},
        files=[("file", (name, content)) for name, content in files],
    )


async def download(api, headers, revision_id):
    link = await api.get(
        f"/resources/certificates/revisions/{revision_id}/file/download-link", headers=headers
    )
    assert link.status_code == 200, link.text
    return (await api.get(link.json()["data"]["url"], headers=headers)).content


async def test_images_and_pdfs_compose_one_upright_metadata_free_original(
    api, headers, application, tenants
):
    org_a = tenants["orgs"][0]
    row, task = await setup(api, headers[0])
    uploads = [("营业执照正本.jpg", photo()), ("副本.pdf", pdf(2)), ("扫描件.png", png())]
    response = await upload_many(
        api, headers[0], row, uploads, parts=[{"rotation": 0}, {"rotation": 0}, {"rotation": 90}]
    )
    assert response.status_code == 200, response.text
    scan = response.json()["data"]
    assert scan["file"]["page_count"] == 4 and scan["file"]["name"] == DATA["name"] + ".pdf"
    assert [(p["ordinal"], p["page_start"], p["page_count"], p["media_type"]) for p in scan["parts"]] == [
        (1, 1, 1, "image/jpeg"),
        (2, 2, 2, "application/pdf"),
        (3, 4, 1, "image/png"),
    ]  # fmt: skip
    original = await download(api, headers[0], scan["certificate_revision_id"])
    assert hashlib.sha256(original).hexdigest() == scan["file"]["sha256"]
    assert MARKER not in original and b"Exif" not in original
    with pymupdf.open(stream=original, filetype="pdf") as composed:
        first = composed[0]
        # EXIF orientation 6 turns the 60x30 photo upright: portrait, red on top.
        assert first.rect.height > first.rect.width and first.rotation == 0
        top = first.get_pixmap(clip=pymupdf.Rect(0, 0, first.rect.width, 20)).pixel(5, 5)
        assert top[0] > 200 and top[1] < 80
        assert "Synthetic license page 2" in composed[2].get_text()
        assert composed[3].rotation == 90
    listed = await api.get(
        "/resources/certificates/files",
        headers=headers[0],
        params={"certificate_id": row["certificate_id"]},
    )
    assert [p["name"] for p in listed.json()["items"][0]["parts"]] == [n for n, _ in uploads]

    # Each uploaded file is kept unchanged, encrypted, beside the composed original.
    async with application.state.db.transaction(org_a) as session:
        stored = (
            await session.scalars(select(CertificateFilePart).order_by(CertificateFilePart.ordinal))
        ).all()
        for part, (_, content) in zip(stored, uploads, strict=True):
            assert await application.state.storage.read(org_a, part.storage_key) == content
    # The image page archives as evidence like any PDF page; its preview has no EXIF.
    chosen = (await select_version(api, headers[0], row, task, revision=2)).json()["data"]
    archived = await add(api, headers[0], task, chosen["id"], page=1)
    assert archived.status_code == 200, archived.text
    preview_link = await api.get(
        f"/evidence-sources/{archived.json()['data']['source']['id']}/preview/download-link",
        headers=headers[0],
    )
    preview = (await api.get(preview_link.json()["data"]["url"], headers=headers[0])).content
    assert preview.startswith(b"\x89PNG") and MARKER not in preview

    # A committed original never gains a part; parts stay in their org.
    with pytest.raises(DBAPIError) as error:
        async with application.state.db.transaction(org_a) as session:
            source = stored[0]
            session.add(
                CertificateFilePart(
                    **{
                        column.name: getattr(source, column.name)
                        for column in CertificateFilePart.__table__.columns
                        if column.name not in {"id", "created_at", "ordinal", "storage_key"}
                    },
                    ordinal=4,
                    storage_key=source.storage_key.rsplit("/", 1)[0] + f"/4-{source.sha256}",
                )
            )
    assert error.value.orig.sqlstate == "23514"  # pyright: ignore[reportAttributeAccessIssue]
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert (await session.scalars(select(CertificateFilePart))).all() == []
    async with application.state.db.transaction() as session:
        assert (await session.scalars(select(CertificateFilePart))).all() == []
    other = await api.get(
        "/resources/certificates/files",
        headers=headers[1],
        params={"revision_id": scan["certificate_revision_id"]},
    )
    assert other.status_code == 404

    artifact = Path("data/work/certificate-images")
    await asyncio.to_thread(artifact.mkdir, parents=True, exist_ok=True)
    (artifact / "synthetic-composed.pdf").write_bytes(original)
    (artifact / "receipt.json").write_text(
        json.dumps({"sha256": scan["file"]["sha256"], "parts": scan["parts"]}, indent=2)
    )


async def test_single_pdf_is_still_stored_unchanged(api, headers):
    row, _ = await setup(api, headers[0])
    content = pdf(1)
    scan = (await upload_many(api, headers[0], row, [("license.pdf", content)])).json()["data"]
    assert scan["parts"] == []
    assert await download(api, headers[0], scan["certificate_revision_id"]) == content


@pytest.mark.parametrize("case", ["bomb", "gif", "disguised", "too_many", "options"])
async def test_refused_uploads_store_nothing(case, api, headers, application, monkeypatch):
    row, _ = await setup(api, headers[0])
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("Refused upload stored")

    monkeypatch.setattr(application.state.storage, "put", forbidden)
    parts = None
    if case == "bomb":
        small = bytearray(png())
        small[16:24] = struct.pack(">II", 100_000, 100_000)  # 10 gigapixels, header only
        files = [("bomb.png", bytes(small))]
    elif case == "gif":
        files = [("logo.gif", b"GIF89a" + b"\x00" * 20)]
    elif case == "disguised":
        files = [("photo.jpg", png())]
    elif case == "too_many":
        files = [(f"page-{n}.png", png()) for n in range(21)]
    else:
        files, parts = [("a.png", png()), ("b.png", png())], [{"rotation": 90}]
    response = await upload_many(api, headers[0], row, files, parts=parts)
    expected = 422 if case == "options" else 400
    assert response.status_code == expected, response.text
    assert not calls
    if case == "bomb":
        assert "40 megapixels" in response.json()["data"]["error"]["message"]
