"""Synthetic local-only GM certificates/CMS and PDF signatures for acceptance tests.

No production certificate, business document or private key is read. Keys here are
deliberately fixed synthetic integers and must never be used outside these fixtures.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import pymupdf
from app.core.bid_der import encode
from app.services.bid_pdf_signatures import (
    CMS_DATA,
    CMS_SIGNED_DATA,
    CONTENT_TYPE,
    EC,
    GM_CMS_DATA,
    GM_CMS_SIGNED_DATA,
    MESSAGE_DIGEST,
    SIGNING_TIME,
    SM2,
    SM2_SIGNATURE,
    SM3,
    TIMESTAMP,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from gmssl import sm2, sm3


def sequence(*items: bytes) -> bytes:
    return encode(0x30, b"".join(items))


def integer(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return encode(2, (b"\0" if raw[0] & 128 else b"") + raw)


def oid(value: str) -> bytes:
    parts = list(map(int, value.split(".")))
    output = bytearray()
    for value in [parts[0] * 40 + parts[1]] + parts[2:]:
        octets = [value & 127]
        while value > 127:
            value >>= 7
            octets.insert(0, 128 | (value & 127))
        output.extend(octets)
    return encode(6, bytes(output))


def algorithm(value: str) -> bytes:
    return sequence(oid(value))


def name(value: str) -> bytes:
    return sequence(encode(0x31, sequence(oid("2.5.4.3"), encode(12, value.encode()))))


def sm3_digest(value: bytes) -> bytes:
    return bytes.fromhex(sm3.sm3_hash(list(value)))


def sm2_key(private: int) -> sm2.CryptSM2:
    tool = sm2.CryptSM2(private_key=f"{private:064x}", public_key="")
    tool.public_key = tool._kg(private, tool.ecc_table["g"])
    return tool


def sign(
    key: sm2.CryptSM2, message: bytes, *, direct: bool = False, wrong_user_id: bool = False
) -> bytes:
    if direct:
        digest = message
    elif wrong_user_id:
        table = key.ecc_table
        user_id = b"not-default-id!!"
        z = (
            (len(user_id) * 8).to_bytes(2, "big")
            + user_id
            + bytes.fromhex(table["a"] + table["b"] + table["g"] + key.public_key)
        )
        digest = sm3_digest(sm3_digest(z) + message)
    else:
        digest = bytes.fromhex(key._sm3_z(message))
    nonce = hashlib.sha256(digest + key.private_key.encode()).hexdigest()
    raw = key.sign(digest, nonce)
    if raw is None:
        raise RuntimeError("synthetic nonce failed")
    return sequence(integer(int(raw[:64], 16)), integer(int(raw[64:], 16)))


@dataclass(frozen=True)
class GMFixture:
    root_der: bytes
    signer_der: bytes
    signer_key: sm2.CryptSM2
    signer_serial: int
    issuer_name: bytes
    intermediate_der: bytes | None = None


def gm_fixture(
    *,
    expired: bool = False,
    not_yet_valid: bool = False,
    intermediate: bool = False,
    chain: bool = False,
    signer_purposes: tuple[str, ...] | None = None,
) -> GMFixture:
    root_key, signer_key = sm2_key(1), sm2_key(2)
    root_name = name("SYNTHETIC GM Root")

    def certificate(
        key: sm2.CryptSM2,
        subject: bytes,
        issuer: bytes,
        issuer_key: sm2.CryptSM2,
        serial: int,
        *,
        ca: bool,
        expiry: bool = False,
        future: bool = False,
        purposes: tuple[str, ...] | None = None,
    ) -> bytes:
        constraints = sequence(encode(1, b"\xff")) if ca else sequence()
        # Provincial CA signer certificates mark extended key usage critical.
        usage = (
            [
                sequence(
                    oid("2.5.29.37"),
                    encode(1, b"\xff"),
                    encode(4, sequence(*(oid(value) for value in purposes))),
                )
            ]
            if purposes
            else []
        )
        extensions = encode(
            0xA3,
            sequence(
                sequence(oid("2.5.29.19"), encode(1, b"\xff"), encode(4, constraints)),
                sequence(
                    oid("2.5.29.15"),
                    encode(1, b"\xff"),
                    encode(4, encode(3, b"\x01\x06" if ca else b"\x07\x80")),
                ),
                *usage,
            ),
        )
        tbs = sequence(
            encode(0xA0, integer(2)),
            integer(serial),
            algorithm(SM2_SIGNATURE),
            issuer,
            sequence(
                encode(23, b"350101000000Z" if future else b"200101000000Z"),
                encode(23, b"210101000000Z" if expiry else b"400101000000Z"),
            ),
            subject,
            sequence(
                sequence(oid(EC), oid(SM2)), encode(3, b"\x00\x04" + bytes.fromhex(key.public_key))
            ),
            extensions,
        )
        return sequence(tbs, algorithm(SM2_SIGNATURE), encode(3, b"\x00" + sign(issuer_key, tbs)))

    root = certificate(root_key, root_name, root_name, root_key, 1, ca=True)
    if chain:
        intermediate_key = sm2_key(3)
        intermediate_name = name("SYNTHETIC GM Intermediate")
        intermediate_der = certificate(
            intermediate_key, intermediate_name, root_name, root_key, 3, ca=True
        )
        leaf = certificate(
            signer_key,
            name("SYNTHETIC GM Signer"),
            intermediate_name,
            intermediate_key,
            2,
            ca=False,
            expiry=expired,
            future=not_yet_valid,
        )
        return GMFixture(root, leaf, signer_key, 2, intermediate_name, intermediate_der)
    leaf = certificate(
        signer_key,
        name("SYNTHETIC GM Signer"),
        root_name,
        root_key,
        2,
        ca=intermediate,
        expiry=expired,
        future=not_yet_valid,
        purposes=signer_purposes,
    )
    return GMFixture(root, leaf, signer_key, 2, root_name)


def cms(
    fixture: GMFixture,
    content: bytes,
    *,
    attrs: bool = True,
    direct_digest: bool = False,
    wrong_digest: bool = False,
    wrong_signature: bool = False,
    wrong_user_id: bool = False,
    gm_oids: bool = True,
    signing_time: bool = True,
    timestamp: bool = False,
    embedded_digest: bool = False,
) -> bytes:
    """GM/T 0010 SignedData; embedded_digest builds the 点聚 layout seen in real bids:
    SM3(ByteRange bytes) as eContent, no signed attributes, the ZA-bound SM2
    signature over that digest and the GM/T 0006 sm2sign OID."""
    data_oid = GM_CMS_DATA if gm_oids else CMS_DATA
    signed_oid = GM_CMS_SIGNED_DATA if gm_oids else CMS_SIGNED_DATA
    message_digest = sm3_digest(content)
    if wrong_digest:
        message_digest = bytes([message_digest[0] ^ 1]) + message_digest[1:]
    attr_values = [
        sequence(oid(CONTENT_TYPE), encode(0x31, oid(data_oid))),
        sequence(oid(MESSAGE_DIGEST), encode(0x31, encode(4, message_digest))),
    ]
    if signing_time:
        attr_values.append(sequence(oid(SIGNING_TIME), encode(0x31, encode(23, b"261008120000Z"))))
    attribute_bytes = b"".join(sorted(attr_values))
    if embedded_digest:
        attrs = False
        # eContent carries the (possibly wrong) digest; the signature always matches it.
        signed = message_digest
    else:
        signed = (
            encode(0x31, attribute_bytes) if attrs else message_digest if direct_digest else content
        )
    signature = sign(
        fixture.signer_key, signed, direct=direct_digest and not attrs, wrong_user_id=wrong_user_id
    )
    if wrong_signature:
        signature = signature[:-1] + bytes([signature[-1] ^ 1])
    signer = sequence(
        integer(1),
        sequence(fixture.issuer_name, integer(fixture.signer_serial)),
        algorithm(SM3),
        *([encode(0xA0, attribute_bytes)] if attrs else []),
        algorithm("1.2.156.10197.1.301.1" if embedded_digest else SM2_SIGNATURE),
        encode(4, signature),
        *([encode(0xA1, sequence(oid(TIMESTAMP), encode(0x31, sequence())))] if timestamp else []),
    )
    return sequence(
        oid(signed_oid),
        encode(
            0xA0,
            sequence(
                integer(1),
                encode(0x31, algorithm(SM3)),
                sequence(oid(data_oid), encode(0xA0, encode(4, message_digest)))
                if embedded_digest
                else sequence(oid(data_oid)),
                encode(0xA0, fixture.signer_der + (fixture.intermediate_der or b"")),
                encode(0x31, signer),
            ),
        ),
    )


def unsigned_pdf(*, empty_widget: bool = False) -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "SYNTHETIC signature fixture only")
        if empty_widget:
            widget = pymupdf.Widget()
            widget.field_name = "synthetic-empty-signature"
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
            widget.rect = pymupdf.Rect(72, 100, 200, 140)
            page.add_widget(widget)
        return doc.tobytes()


def standard_fixture(key_algorithm: str = "rsa") -> tuple[bytes, Callable[[bytes], bytes]]:
    """Return a synthetic RSA/ECDSA root and standard detached CMS producer."""
    make_key = (
        (lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048))
        if key_algorithm == "rsa"
        else (lambda: ec.generate_private_key(ec.SECP256R1()))
    )
    root_key, leaf_key = make_key(), make_key()
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC Standard Root")])
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC Standard Signer")])

    def make_certificate(
        subject: x509.Name, issuer: x509.Name, public_key, issuer_key, serial: int, ca: bool
    ) -> x509.Certificate:
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(public_key)
            .serial_number(serial)
            .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
            .not_valid_after(datetime(2040, 1, 1, tzinfo=UTC))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(issuer_key, hashes.SHA256())
        )

    root = make_certificate(root_name, root_name, root_key.public_key(), root_key, 3, True)
    leaf = make_certificate(leaf_name, root_name, leaf_key.public_key(), root_key, 4, False)

    def build(content: bytes) -> bytes:
        return (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(content)
            .add_signer(leaf, leaf_key, hashes.SHA256())
            .sign(
                serialization.Encoding.DER,
                [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary],
            )
        )

    return root.public_bytes(serialization.Encoding.DER), build


def signed_pdf(
    fixture: GMFixture | None = None,
    *,
    base: bytes | None = None,
    attrs: bool = True,
    direct_digest: bool = False,
    wrong_digest: bool = False,
    wrong_signature: bool = False,
    wrong_user_id: bool = False,
    subfilter: str = "GM.sm2cms.detached",
    signing_time: bool = True,
    gm_oids: bool = True,
    cms_builder: Callable[[bytes], bytes] | None = None,
    timestamp: bool = False,
    embedded_digest: bool = False,
) -> bytes:
    """Append a real PDF revision; earlier signatures retain their exact signed bytes."""
    fixture = fixture or gm_fixture()
    base = base or unsigned_pdf()
    with pymupdf.open(stream=base, filetype="pdf") as doc:
        count = doc.xref_length()
        catalog_xref = doc.pdf_catalog()
        catalog = doc.xref_object(catalog_xref)
        catalog = re.sub(r"/AcroForm\s*\d+\s+\d+\s+R", "", catalog)
        catalog = catalog.rstrip()[:-2] + f" /AcroForm {count + 2} 0 R >>"
        previous_xref = int(re.findall(rb"startxref\s+(\d+)", base)[-1])
    slot = b"0" * 32768
    filter_name = b"DJ.GMPkiLite" if subfilter == "GM.sm2cms.detached" else b"Adobe.PPKLite"
    objects = [
        (
            count,
            b"<< /Type /Sig /Filter /"
            + filter_name
            + b" /SubFilter /"
            + subfilter.encode()
            + b" /M (D:20261008120000Z) /ByteRange [1234567890 1234567890 1234567890 1234567890] /Contents <"
            + slot
            + b"> >>",
        ),
        (
            count + 1,
            f"<< /Type /Annot /Subtype /Widget /FT /Sig /T (synthetic-signature-{count}) /V {count} 0 R /Rect [0 0 0 0] >>".encode(),
        ),
        (count + 2, f"<< /Fields [{count + 1} 0 R] /SigFlags 3 >>".encode()),
        (catalog_xref, catalog.encode()),
    ]
    # Keep prior widgets reachable in multi-signature fixtures through raw /Type
    # /Sig objects; the validator records every original signature dictionary.
    output = bytearray(base + b"\n")
    offsets = {}
    for number, body in objects:
        offsets[number] = len(output)
        output.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(b"xref\n")
    for number in sorted(offsets):
        output.extend(f"{number} 1\n{offsets[number]:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {count + 3} /Root {catalog_xref} 0 R /Prev {previous_xref} >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    token_start = output.index(b"/Contents <", offsets[count]) + len(b"/Contents ")
    token_end = token_start + len(slot) + 2
    byte_range = [0, token_start, token_end, len(output) - token_end]
    placeholder = b"1234567890 1234567890 1234567890 1234567890"
    replacement = b" ".join(f"{value:<10d}".encode() for value in byte_range)
    where = output.index(placeholder, offsets[count])
    output[where : where + len(placeholder)] = replacement
    signed_content = bytes(output[:token_start] + output[token_end:])
    signature_bytes = (
        cms_builder(signed_content)
        if cms_builder
        else cms(
            fixture,
            signed_content,
            attrs=attrs,
            direct_digest=direct_digest,
            wrong_digest=wrong_digest,
            wrong_signature=wrong_signature,
            wrong_user_id=wrong_user_id,
            signing_time=signing_time,
            gm_oids=gm_oids,
            timestamp=timestamp,
            embedded_digest=embedded_digest,
        )
    )
    signature_cms = signature_bytes.hex().encode()
    if len(signature_cms) > len(slot):
        raise RuntimeError("CMS fixture slot too small")
    output[token_start + 1 : token_end - 1] = signature_cms.ljust(len(slot), b"0")
    return bytes(output)


def modified_pdf(content: bytes) -> bytes:
    """Change a signed original byte while keeping its length and valid PDF syntax."""
    match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+(\d+)", content)
    if match is None:
        raise ValueError("synthetic PDF page box missing")
    position = match.end(1) - 1
    changed = (
        content[:position]
        + bytes([ord("1") if content[position] != ord("1") else ord("2")])
        + content[position + 1 :]
    )
    assert changed != content and len(changed) == len(content)
    return changed
