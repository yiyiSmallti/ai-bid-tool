"""Conservative offline PDF/CMS validation over unchanged original bytes.

Results contain restricted names; callers must encrypt them and construct reader
projections from an explicit allowlist. No network or provider is used here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import pymupdf
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from gmssl import sm2

from app.core.bid_der import DERError, Node, encode, read_der

VALIDATOR_VERSION = "local-pdf-cms-v2"
SM2 = "1.2.156.10197.1.301"
SM2_SIGNATURE = "1.2.156.10197.1.501"
# GM/T 0006 also names SM2 signing 1.2.156.10197.1.301.1; 点聚 signers use it.
SM2_SIGNATURES = frozenset({SM2_SIGNATURE, "1.2.156.10197.1.301.1"})
SM3 = "1.2.156.10197.1.401"
EC = "1.2.840.10045.2.1"
RSA = "1.2.840.113549.1.1.1"
CMS_DATA = "1.2.840.113549.1.7.1"
CMS_SIGNED_DATA = "1.2.840.113549.1.7.2"
GM_CMS_DATA = "1.2.156.10197.6.1.4.2.1"
GM_CMS_SIGNED_DATA = "1.2.156.10197.6.1.4.2.2"
CONTENT_TYPE = "1.2.840.113549.1.9.3"
MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
SIGNING_TIME = "1.2.840.113549.1.9.5"
TIMESTAMP = "1.2.840.113549.1.9.16.2.14"
HASHES = {
    "1.3.14.3.2.26": hashes.SHA1,
    "2.16.840.1.101.3.4.2.1": hashes.SHA256,
    "2.16.840.1.101.3.4.2.2": hashes.SHA384,
    "2.16.840.1.101.3.4.2.3": hashes.SHA512,
}
SIGNATURE_HASHES = {
    "1.2.840.113549.1.1.5": "1.3.14.3.2.26",
    "1.2.840.113549.1.1.11": "2.16.840.1.101.3.4.2.1",
    "1.2.840.113549.1.1.12": "2.16.840.1.101.3.4.2.2",
    "1.2.840.113549.1.1.13": "2.16.840.1.101.3.4.2.3",
    "1.2.840.10045.4.1": "1.3.14.3.2.26",
    "1.2.840.10045.4.3.2": "2.16.840.1.101.3.4.2.1",
    "1.2.840.10045.4.3.3": "2.16.840.1.101.3.4.2.2",
    "1.2.840.10045.4.3.4": "2.16.840.1.101.3.4.2.3",
}


def _sequence(node: Node, *, minimum: int = 0) -> tuple[Node, ...]:
    items = node.expect(0x30).children
    if len(items) < minimum:
        raise DERError("incomplete sequence")
    return items


def _algorithm(node: Node) -> str:
    items = _sequence(node, minimum=1)
    if not items or len(items) > 2:
        raise DERError("invalid algorithm identifier")
    return next(iter(items)).oid()


def _pdf_time(value: str) -> datetime | None:
    match = re.fullmatch(r"D:(\d{14})(Z|[+-]\d{2}'?\d{2}'?)", value)
    if not match:
        return None
    offset = match[2] or "Z"
    try:
        parsed = datetime.strptime(
            match[1] + ("+0000" if offset == "Z" else offset.replace("'", "")), "%Y%m%d%H%M%S%z"
        )
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _time(node: Node) -> datetime:
    value = node.value.decode("ascii")
    if node.tag == 23 and re.fullmatch(r"\d{12}Z", value):
        year = int(value[:2])
        year += 2000 if year < 50 else 1900
        value = str(year) + value[2:]
    elif node.tag != 24 or not re.fullmatch(r"\d{14}Z", value):
        raise DERError("unsupported ASN.1 time")
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise DERError("invalid ASN.1 time") from exc


def _name(node: Node) -> str:
    labels = {
        "2.5.4.3": "CN",
        "2.5.4.6": "C",
        "2.5.4.7": "L",
        "2.5.4.8": "ST",
        "2.5.4.10": "O",
        "2.5.4.11": "OU",
    }
    rdns = []
    for rdn in node.expect(0x30).children:
        pieces = []
        for attribute in rdn.expect(0x31).children:
            pair = _sequence(attribute, minimum=2)
            if len(pair) != 2:
                raise DERError("invalid distinguished name")
            key = pair[0].oid()
            item = pair[1]
            if item.tag in (12, 19, 20, 22):
                text = item.value.decode("utf-8" if item.tag == 12 else "latin-1")
            elif item.tag == 30:
                text = item.value.decode("utf-16-be")
            else:
                raise DERError("unsupported name string")
            pieces.append(f"{labels.get(key, key)}={text}")
        rdns.append("+".join(pieces))
    name = ",".join(rdns)
    if len(name) > 2000:
        raise DERError("certificate name limit")
    return name


def parse_certificate(content: bytes) -> dict[str, Any]:
    """Parse a DER/PEM X.509 certificate, including SM2 unavailable in OpenSSL builds."""
    if content.startswith(b"-----BEGIN CERTIFICATE-----"):
        match = re.fullmatch(
            rb"-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----\s*",
            content,
        )
        if not match:
            raise DERError("invalid certificate PEM")
        content = base64.b64decode(re.sub(rb"\s", b"", match[1]), validate=True)
    outer = _sequence(read_der(content), minimum=3)
    if len(outer) != 3:
        raise DERError("invalid certificate")
    tbs = list(_sequence(outer[0], minimum=6))
    if tbs[0].tag == 0xA0:
        if len(tbs[0].children) != 1 or tbs[0].children[0].integer() not in (0, 1, 2):
            raise DERError("unsupported certificate version")
        tbs.pop(0)
    serial = tbs[0].integer()
    if serial <= 0 or len(tbs[0].value) > 21:
        raise DERError("invalid certificate serial")
    signature_algorithm = _algorithm(outer[1])
    if tbs[1].encoded != outer[1].encoded:
        raise DERError("certificate algorithm mismatch")
    validity = _sequence(tbs[3], minimum=2)
    if len(validity) != 2:
        raise DERError("invalid certificate validity")
    before, after = map(_time, validity)
    if before > after:
        raise DERError("invalid certificate validity interval")
    spki = _sequence(tbs[5], minimum=2)
    if len(spki) != 2 or not spki[1].expect(3).value or spki[1].value[0] != 0:
        raise DERError("unsupported public-key encoding")
    public_algorithm = _algorithm(spki[0])
    parameters = spki[0].children[1:]
    public_key = spki[1].value[1:]
    is_sm2 = public_algorithm == SM2 or (
        public_algorithm == EC and len(parameters) == 1 and parameters[0].oid() == SM2
    )
    if is_sm2:
        if len(public_key) != 65 or public_key[0] != 4:
            raise DERError("unsupported SM2 point")
        x, y = int.from_bytes(public_key[1:33], "big"), int.from_bytes(public_key[33:], "big")
        table = sm2.default_ecc_table
        p, a, b = (int(table[key], 16) for key in ("p", "a", "b"))
        if x >= p or y >= p or (y * y - x * x * x - a * x - b) % p:
            raise DERError("SM2 point is not on curve")
        public_algorithm = SM2
    elif public_algorithm not in (RSA, EC):
        raise DERError("unsupported certificate public key")
    else:
        x509.load_der_x509_certificate(content).public_key()
    is_ca, path_length, key_cert_sign, subject_key_id = False, None, None, None
    extended_key_usage: list[str] | None = None
    unsupported_critical = []
    seen_extensions = set()
    for optional in tbs[6:]:
        if optional.tag in (0x81, 0x82):
            continue
        if optional.tag != 0xA3 or len(optional.children) != 1:
            raise DERError("unsupported certificate field")
        for extension in optional.children[0].expect(0x30).children:
            fields = list(_sequence(extension, minimum=2))
            oid = fields.pop(0).oid()
            if oid in seen_extensions:
                raise DERError("duplicate certificate extension")
            seen_extensions.add(oid)
            critical = False
            if fields[0].tag == 1:
                critical = fields.pop(0).value == b"\xff"
            if len(fields) != 1:
                raise DERError("invalid certificate extension")
            value = fields[0].expect(4).value
            if oid == "2.5.29.19":
                constraints = list(read_der(value).expect(0x30).children)
                if constraints and constraints[0].tag == 1:
                    is_ca = constraints.pop(0).value == b"\xff"
                if constraints:
                    path_length = constraints.pop(0).integer()
                    if path_length < 0 or not is_ca:
                        raise DERError("invalid path length")
                if constraints:
                    raise DERError("invalid basic constraints")
            elif oid == "2.5.29.15":
                bits = read_der(value).expect(3).value
                key_cert_sign = bool(len(bits) > 1 and bits[1] & 4)
            elif oid == "2.5.29.14":
                subject_key_id = read_der(value).expect(4).value.hex()
            elif oid == "2.5.29.37":
                purposes = read_der(value).expect(0x30).children
                if not 1 <= len(purposes) <= 32:
                    raise DERError("invalid extended key usage")
                extended_key_usage = [purpose.oid() for purpose in purposes]
            elif critical:
                # Name constraints, policy constraints and other unimplemented rules
                # cannot be ignored when establishing trust.
                unsupported_critical.append(oid)
    signature = outer[2].expect(3).value
    if not signature or signature[0] != 0:
        raise DERError("unsupported certificate signature bits")
    return {
        "der": content,
        "fingerprint_sha256": hashlib.sha256(content).hexdigest(),
        "subject": _name(tbs[4]),
        "issuer": _name(tbs[2]),
        "subject_der": tbs[4].encoded,
        "issuer_der": tbs[2].encoded,
        "serial_number": str(serial),
        "not_before": before.isoformat(),
        "not_after": after.isoformat(),
        "is_ca": is_ca,
        "path_length": path_length,
        "key_cert_sign": key_cert_sign,
        "subject_key_id": subject_key_id,
        "unsupported_critical_extensions": unsupported_critical,
        "extended_key_usage": extended_key_usage,
        "public_key_algorithm": public_algorithm,
        "public_key": public_key,
        "tbs": outer[0].encoded,
        "signature": signature[1:],
        "signature_algorithm": signature_algorithm,
    }


def _sm3_digest(*chunks: bytes) -> bytes:
    """SM3 through OpenSSL; pure-Python SM3 takes seconds per megabyte of PDF."""
    hasher = hashes.Hash(hashes.SM3())
    for chunk in chunks:
        hasher.update(chunk)
    return hasher.finalize()


def _digest(oid: str, content: bytes) -> bytes:
    if oid == SM3:
        return _sm3_digest(content)
    if oid not in HASHES:
        raise DERError("unsupported digest algorithm")
    hasher = hashes.Hash(HASHES[oid]())
    hasher.update(content)
    return hasher.finalize()


def _sm2_verify(
    cert: dict[str, Any], signature: bytes, data: bytes, *, direct: bool = False
) -> bool:
    values = _sequence(read_der(signature), minimum=2)
    if len(values) != 2:
        raise DERError("invalid SM2 signature")
    r, s = (n.integer() for n in values)
    order = int(sm2.default_ecc_table["n"], 16)
    if not (0 < r < order and 0 < s < order):
        return False
    # gmssl 3.2.2 strips a character set with lstrip('04'), which corrupts
    # unprefixed coordinates beginning 04. Assign the validated XY explicitly.
    verifier = sm2.CryptSM2(public_key="", private_key="")
    verifier.public_key = cert["public_key"][1:].hex()
    if direct:
        message_digest = data
    else:
        default_id = b"1234567812345678"
        table = verifier.ecc_table
        za_input = (
            (len(default_id) * 8).to_bytes(2, "big")
            + default_id
            + bytes.fromhex(table["a"] + table["b"] + table["g"] + verifier.public_key)
        )
        message_digest = _sm3_digest(_sm3_digest(za_input), data)
    return bool(verifier.verify(f"{r:064x}{s:064x}", message_digest))


def _verify(
    cert: dict[str, Any],
    signature: bytes,
    data: bytes,
    algorithm: str,
    digest: str | None = None,
    *,
    direct_sm2: bool = False,
) -> bool:
    if algorithm in SM2_SIGNATURES:
        if cert["public_key_algorithm"] != SM2 or digest not in (None, SM3):
            raise DERError("unsupported SM2 algorithm combination")
        return _sm2_verify(cert, signature, data, direct=direct_sm2)
    digest_oid = SIGNATURE_HASHES.get(algorithm, digest if algorithm == RSA else None)
    if digest_oid not in HASHES or (digest is not None and digest_oid != digest):
        raise DERError("unsupported signature algorithm")
    public_key = x509.load_der_x509_certificate(cert["der"]).public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey) and algorithm in (
            {RSA} | set(k for k in SIGNATURE_HASHES if k.startswith("1.2.840.113549."))
        ):
            public_key.verify(signature, data, padding.PKCS1v15(), HASHES[digest_oid]())
        elif isinstance(public_key, ec.EllipticCurvePublicKey) and algorithm.startswith(
            "1.2.840.10045.4."
        ):
            public_key.verify(signature, data, ec.ECDSA(HASHES[digest_oid]()))
        else:
            raise DERError("unsupported key/signature combination")
        return True
    except InvalidSignature:
        return False


def _certificate_view(cert: dict[str, Any]) -> dict[str, Any]:
    return {
        key: cert[key]
        for key in (
            "fingerprint_sha256",
            "subject",
            "issuer",
            "serial_number",
            "not_before",
            "not_after",
            "is_ca",
            "public_key_algorithm",
            "signature_algorithm",
        )
    }


def _certificate_validity(cert: dict[str, Any], at: datetime | None) -> str:
    if at is None:
        return "unknown"
    if at < datetime.fromisoformat(cert["not_before"]):
        return "not_yet_valid"
    if at > datetime.fromisoformat(cert["not_after"]):
        return "expired"
    return "valid"


def _anchors(material: list[Any]) -> tuple[list[dict[str, Any]], set[str]]:
    certs, roots = [], set()
    if len(material) > 128:
        raise DERError("trust material limit")
    for item in material:
        if isinstance(item, dict) and item.get("enabled", True) is not True:
            continue
        if isinstance(item, bytes):
            data = item
        elif isinstance(item, dict):
            data = item.get("der")
            if data is None:
                data = base64.b64decode(item["certificate_der_base64"], validate=True)
        else:
            raise DERError("invalid trust material")
        cert = parse_certificate(data)
        certs.append(cert)
        if (
            not cert["is_ca"]
            or cert["key_cert_sign"] is False
            or cert["unsupported_critical_extensions"]
        ):
            continue
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind == "root" or (kind != "intermediate" and cert["subject_der"] == cert["issuer_der"]):
            if _verify(cert, cert["signature"], cert["tbs"], cert["signature_algorithm"]):
                roots.add(cert["fingerprint_sha256"])
    return certs, roots


# Purposes accepted for document signatures when a certificate restricts its use:
# any purpose, email protection (accepted by PDF signers and used by Chinese
# provincial CAs), RFC 9336 document signing, Adobe and Microsoft document signing.
SIGNING_PURPOSES = frozenset(
    {
        "2.5.29.37.0",
        "1.3.6.1.5.5.7.3.4",
        "1.3.6.1.5.5.7.3.36",
        "1.2.840.113583.1.1.5",
        "1.3.6.1.4.1.311.10.3.12",
    }
)


def _chain(
    leaf: dict[str, Any], embedded: list[dict[str, Any]], anchors: list[Any], at: datetime | None
) -> tuple[str, list[str]]:
    local, roots = _anchors(anchors)
    pool = {c["fingerprint_sha256"]: c for c in embedded + local}
    if leaf["unsupported_critical_extensions"]:
        return "unsupported", []
    purposes = leaf.get("extended_key_usage")
    if purposes is not None and SIGNING_PURPOSES.isdisjoint(purposes):
        return "not_for_signing", []
    if at is None:
        return "unknown", []
    work = 0
    verified_links: dict[tuple[str, str], bool] = {}

    def walk(cert: dict[str, Any], visited: set[str], subordinate_cas: int) -> list[str] | None:
        nonlocal work
        work += 1
        if work > 512:
            return None
        fp = cert["fingerprint_sha256"]
        if fp in visited or len(visited) >= 12:
            return None
        if _certificate_validity(cert, at) not in ("valid", "unknown"):
            return None
        if fp in roots:
            return [fp]
        for issuer in pool.values():
            work += 1
            if work > 512:
                return None
            if (
                cert["issuer_der"] != issuer["subject_der"]
                or not issuer["is_ca"]
                or issuer["key_cert_sign"] is False
                or issuer["unsupported_critical_extensions"]
            ):
                continue
            if issuer["path_length"] is not None and subordinate_cas > issuer["path_length"]:
                continue
            try:
                pair = (fp, issuer["fingerprint_sha256"])
                if pair not in verified_links:
                    verified_links[pair] = _verify(
                        issuer, cert["signature"], cert["tbs"], cert["signature_algorithm"]
                    )
                matched = verified_links[pair]
            except (ValueError, NotImplementedError, UnsupportedAlgorithm):
                matched = False
            if matched:
                route = walk(
                    issuer,
                    visited | {fp},
                    subordinate_cas
                    + int(issuer["is_ca"] and issuer["subject_der"] != issuer["issuer_der"]),
                )
                if route:
                    return [fp] + route
        return None

    chain = walk(leaf, set(), 0)
    return ("trusted", chain) if chain else ("unknown", [])


def _attributes(node: Node) -> dict[str, Node]:
    encoded = [item.encoded for item in node.children]
    if encoded != sorted(encoded):
        raise DERError("noncanonical signed attribute order")
    result = {}
    for attribute in node.children:
        pair = _sequence(attribute, minimum=2)
        if len(pair) != 2:
            raise DERError("invalid CMS attribute")
        oid = pair[0].oid()
        values = pair[1].expect(0x31).children
        if oid in result or len(values) != 1:
            raise DERError("ambiguous CMS attribute")
        result[oid] = values[0]
    return result


def _cms(
    content: bytes,
    signed_bytes: bytes,
    anchors: list[Any],
    pdf_claimed_time: datetime | None = None,
) -> list[dict[str, Any]]:
    root = _sequence(read_der(content, allow_zero_padding=True), minimum=2)
    if (
        len(root) != 2
        or root[0].oid() not in (CMS_SIGNED_DATA, GM_CMS_SIGNED_DATA)
        or root[1].tag != 0xA0
        or len(root[1].children) != 1
    ):
        raise DERError("unsupported CMS content")
    sd = _sequence(root[1].children[0], minimum=4)
    sd[0].integer()
    digest_oids = {_algorithm(n) for n in sd[1].expect(0x31).children}
    encapsulated = _sequence(sd[2], minimum=1)
    if encapsulated[0].oid() not in (CMS_DATA, GM_CMS_DATA):
        raise DERError("unsupported CMS content type")
    embedded_digest = None
    if len(encapsulated) == 2 and encapsulated[0].oid() == GM_CMS_DATA:
        # 点聚 GM/T 0010 signers embed SM3(ByteRange bytes) as eContent and sign
        # that digest with the standard ZA-bound SM2 signature.
        wrapper = encapsulated[1]
        if wrapper.tag != 0xA0 or len(wrapper.children) != 1:
            raise DERError("unsupported CMS embedded content")
        embedded_digest = wrapper.children[0].expect(4).value
    elif len(encapsulated) != 1:
        raise DERError("unsupported CMS embedded content")
    certs = []
    for item in sd[3:-1]:
        if item.tag == 0xA0:
            if len(item.children) > 64:
                raise DERError("CMS certificate limit")
            certs += [parse_certificate(c.encoded) for c in item.children]
        elif item.tag != 0xA1:
            raise DERError("unsupported CMS material")
    signers = sd[-1].expect(0x31).children
    if not 1 <= len(signers) <= 16:
        raise DERError("CMS signer limit")
    results = []
    for signer in signers:
        fields = list(_sequence(signer, minimum=5))
        fields.pop(0).integer()
        sid = fields.pop(0)
        if sid.tag == 0x30:
            identity = _sequence(sid, minimum=2)
            if len(identity) != 2:
                raise DERError("invalid CMS signer identifier")
            matches = [
                c
                for c in certs
                if c["issuer_der"] == identity[0].encoded
                and c["serial_number"] == str(identity[1].integer())
            ]
        elif sid.tag == 0x80:
            matches = [c for c in certs if c["subject_key_id"] == sid.value.hex()]
        else:
            raise DERError("unsupported CMS signer identifier")
        if len(matches) != 1:
            raise DERError("missing or ambiguous signer certificate")
        cert = matches[0]
        digest_oid = _algorithm(fields.pop(0))
        if digest_oid not in digest_oids:
            raise DERError("CMS digest mismatch")
        attributes = fields.pop(0) if fields and fields[0].tag == 0xA0 else None
        algorithm = _algorithm(fields.pop(0))
        signature = fields.pop(0).expect(4).value
        timestamp = "absent"
        if fields:
            if len(fields) != 1 or fields[0].tag != 0xA1:
                raise DERError("unsupported CMS signer fields")
            unsigned_attrs = _attributes(fields[0])
            if TIMESTAMP in unsigned_attrs:
                timestamp = "unsupported"
        at = pdf_claimed_time
        content_digest = _digest(digest_oid, signed_bytes)
        digest_status = "valid"
        variant = "signed_content"
        if attributes and embedded_digest is not None:
            raise DERError("unsupported CMS embedded content with signed attributes")
        if embedded_digest is not None:
            digest_status = "valid" if embedded_digest == content_digest else "invalid"
            to_verify = embedded_digest
            variant = "gm_embedded_content_digest"
        elif attributes:
            attrs = _attributes(attributes)
            if (
                MESSAGE_DIGEST not in attrs
                or CONTENT_TYPE not in attrs
                or attrs[CONTENT_TYPE].oid() != encapsulated[0].oid()
            ):
                raise DERError("missing CMS content binding")
            digest_status = (
                "valid" if attrs[MESSAGE_DIGEST].expect(4).value == content_digest else "invalid"
            )
            if SIGNING_TIME in attrs:
                at = _time(attrs[SIGNING_TIME])
            to_verify = encode(0x31, attributes.value)
            variant = "signed_attributes_der_set"
        else:
            to_verify = signed_bytes
        valid = _verify(cert, signature, to_verify, algorithm, digest_oid)
        if algorithm in SM2_SIGNATURES:
            variant += "_sm2_default_za"
            # Approved direct SM3(content) variant is supported only without attrs.
            # Signed attributes always require the standard ZA-bound DER SET.
            if not valid and attributes is None and embedded_digest is None:
                valid = _verify(
                    cert, signature, content_digest, algorithm, digest_oid, direct_sm2=True
                )
                if valid:
                    variant = "direct_sm3_content_digest"
        try:
            trust, chain = _chain(cert, certs, anchors, at)
        except (ValueError, TypeError, KeyError, NotImplementedError, UnsupportedAlgorithm):
            trust, chain = "unsupported", []
        results.append(
            {
                "crypto_status": "valid" if valid and digest_status == "valid" else "invalid",
                "signature_value_status": "valid" if valid else "invalid",
                "content_digest_status": digest_status,
                "digest_algorithm": digest_oid,
                "signature_algorithm": algorithm,
                "verification_variant": variant if valid else None,
                "certificate": _certificate_view(cert),
                "certificate_validity_status": _certificate_validity(cert, at),
                "claimed_signing_time": at.isoformat() if at else None,
                "trusted_timestamp": None,
                "timestamp_status": timestamp,
                "revocation_status": "unknown",
                "trust_status": trust,
                "trust_chain_sha256": chain,
            }
        )
    return results


def validate_pdf(content: bytes, anchors: list[Any]) -> dict[str, Any]:
    """Validate signatures found by the PDF parser against their exact original byte gap."""
    result: dict[str, Any] = {
        "validator_version": VALIDATOR_VERSION,
        "document_sha256": hashlib.sha256(content).hexdigest(),
        "validation_network": "disabled",
        "status": "not_applicable",
        "signatures": [],
        "final_revision": {
            "status": "unsigned",
            "covered_by_signature_indices": [],
            "modified_after_last_signature": False,
        },
    }
    if not content.startswith(b"%PDF-") or len(content) > 100 * 1024 * 1024:
        raise ValueError("invalid PDF or byte limit")
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        if doc.is_encrypted:
            raise ValueError("encrypted PDF signatures unsupported")
        fields: dict[int, list[str]] = {}
        signature_xrefs = set()
        if doc.xref_length() > 250_000:
            raise ValueError("PDF object limit")
        for xref in range(1, doc.xref_length()):
            if doc.xref_get_key(xref, "FT") == ("name", "/Sig"):
                value_type, value = doc.xref_get_key(xref, "V")
                if value_type == "xref":
                    signature_xref = int(value.split()[0])
                    name_type, name = doc.xref_get_key(xref, "T")
                    fields.setdefault(signature_xref, []).append(
                        name if name_type == "string" else ""
                    )
                    signature_xrefs.add(signature_xref)
            if doc.xref_get_key(xref, "Type") == ("name", "/Sig"):
                signature_xrefs.add(xref)
        if len(signature_xrefs) > 64:
            raise ValueError("PDF signature limit")
        for xref in sorted(signature_xrefs):
            item: dict[str, Any] = {
                "signature_index": len(result["signatures"]) + 1,
                "signature_xref": xref,
                "field_name": fields.get(xref, [""])[0],
                "field_names": fields.get(xref, []),
                "field_name_sha256": hashlib.sha256(fields.get(xref, [""])[0].encode()).hexdigest(),
                "coverage_status": "invalid",
                "crypto_status": "unknown",
                "trust_status": "unknown",
                "certificate_validity_status": "unknown",
                "revocation_status": "unknown",
                "timestamp_status": "absent",
                "claimed_signing_time": None,
                "trusted_timestamp": None,
                "modified_after_signing": None,
                "final_revision_covered": False,
                "reason_codes": ["offline_revocation_unknown", "claimed_time_not_trusted"],
                "byte_range": [],
            }
            try:
                if any(len(name) > 2000 for name in item["field_names"]):
                    raise DERError("signature field name limit")
                typ, byte_range = doc.xref_get_key(xref, "ByteRange")
                if typ != "array" or not re.fullmatch(
                    r"\[\s*\d+\s+\d+\s+\d+\s+\d+\s*\]", byte_range
                ):
                    raise DERError("invalid_byte_range")
                start, size, second, tail = map(int, re.findall(r"\d+", byte_range))
                item["byte_range"] = [start, size, second, tail]
                end = second + tail
                if start != 0 or size <= 0 or second <= size or tail <= 0 or end > len(content):
                    raise DERError("invalid_byte_range")
                gap = content[size:second]
                if len(gap) > 8 * 1024 * 1024 + 4096:
                    raise DERError("unsupported PDF Contents size")
                if not re.fullmatch(rb"<[0-9a-fA-F\s]+>", gap):
                    raise DERError("contents_gap_mismatch")
                raw_cms = bytes.fromhex(re.sub(rb"\s", b"", gap[1:-1]).decode("ascii"))
                # Only the actual object containing this exact Contents token may
                # claim the gap. This defeats unrelated unsigned exclusions.
                matches = list(
                    re.finditer(
                        rb"(?m)(?<!\d)" + str(xref).encode() + rb"\s+\d+\s+obj\b", content[:size]
                    )
                )
                if not matches:
                    raise DERError("signature_original_object_missing")
                object_start = matches[-1].start()
                object_end = content.find(b"endobj", second, end)
                if object_end < 0 or b"endobj" in content[object_start:size]:
                    raise DERError("contents_not_in_signature_object")
                prefix = content[object_start:size]
                if (
                    not re.search(rb"/Contents\s*$", prefix)
                    or len(re.findall(rb"/ByteRange\b", prefix + content[second:object_end])) != 1
                ):
                    raise DERError("contents_not_in_signature_object")
                original_object = prefix + content[second:object_end]
                original_range = re.search(rb"/ByteRange\s*\[([^\]]+)\]", original_object)
                if (
                    original_range is None
                    or not re.fullmatch(rb"\s*\d+\s+\d+\s+\d+\s+\d+\s*", original_range[1])
                    or list(map(int, re.findall(rb"\d+", original_range[1]))) != item["byte_range"]
                ):
                    raise DERError("original_byte_range_mismatch")
                contents_type, _contents_value = doc.xref_get_key(xref, "Contents")
                if contents_type == "string":
                    # MuPDF returns binary PDF strings as UTF-8 text; use a
                    # hex-normalized object instead for an exact byte comparison.
                    normalized = doc.xref_object(xref, compressed=False).encode()
                    normalized_contents = re.search(rb"/Contents\s*<([A-Fa-f0-9\s]+)>", normalized)
                    if (
                        normalized_contents is None
                        or bytes.fromhex(re.sub(rb"\s", b"", normalized_contents[1]).decode())
                        != raw_cms
                    ):
                        raise DERError("current_contents_mismatch")
                else:
                    raise DERError("unsupported PDF Contents encoding")
                if not re.search(rb"%%EOF\s*$", content[:end]):
                    raise DERError("unsigned_revision_tail")
                with pymupdf.open(stream=content[:end], filetype="pdf") as revision_doc:
                    if revision_doc.is_repaired or revision_doc.version_count > 64:
                        raise DERError("unsupported PDF revision structure")
                    if xref >= revision_doc.xref_length() or revision_doc.xref_get_key(
                        xref, "Type"
                    ) != ("name", "/Sig"):
                        raise DERError("signature_not_in_original_revision")
                    revision_range = revision_doc.xref_get_key(xref, "ByteRange")
                    if revision_range != (typ, byte_range):
                        raise DERError("signature_revision_byte_range_mismatch")
                    revision_contents = re.search(
                        rb"/Contents\s*<([A-Fa-f0-9\s]+)>",
                        revision_doc.xref_object(xref, compressed=False).encode(),
                    )
                    if (
                        revision_contents is None
                        or bytes.fromhex(re.sub(rb"\s", b"", revision_contents[1]).decode())
                        != raw_cms
                    ):
                        raise DERError("signature_revision_contents_mismatch")
                    item["revision_index"] = revision_doc.version_count
                    original_time_type, original_time_value = revision_doc.xref_get_key(xref, "M")
                    original_filter = revision_doc.xref_get_key(xref, "Filter")
                    original_subfilter = revision_doc.xref_get_key(xref, "SubFilter")
                item.update(
                    {
                        "coverage_status": "whole_revision",
                        "revision_length": end,
                        "revision_sha256": hashlib.sha256(content[:end]).hexdigest(),
                        "modified_after_signing": end != len(content),
                        "final_revision_covered": end == len(content),
                    }
                )
                filter_value = doc.xref_get_key(xref, "Filter")
                subfilter = doc.xref_get_key(xref, "SubFilter")

                def supported_profile(profile_filter: str, profile_subfilter: str) -> bool:
                    return (
                        profile_filter in ("/Adobe.PPKLite", "/Adobe.PPKMS", "/Entrust.PPKEF")
                        and profile_subfilter in ("/adbe.pkcs7.detached", "/ETSI.CAdES.detached")
                    ) or (
                        profile_filter == "/DJ.GMPkiLite"
                        and profile_subfilter == "/GM.sm2cms.detached"
                    )

                supported_profile_value = supported_profile(
                    filter_value[1], subfilter[1]
                ) and supported_profile(original_filter[1], original_subfilter[1])
                if not supported_profile_value:
                    item["crypto_status"] = "unsupported"
                    item["reason_codes"].append("unsupported_pdf_signature_profile")
                else:
                    try:
                        signers = _cms(
                            raw_cms,
                            content[:size] + content[second:end],
                            anchors,
                            _pdf_time(original_time_value)
                            if original_time_type == "string"
                            else None,
                        )
                    except (
                        ValueError,
                        TypeError,
                        IndexError,
                        KeyError,
                        NotImplementedError,
                        UnsupportedAlgorithm,
                    ):
                        # A CMS layout this validator cannot read proves nothing either
                        # way; only checked byte-range/digest/signature failures are invalid.
                        signers = []
                        item["crypto_status"] = "unsupported"
                        item["reason_codes"].append("unsupported_cms_structure")
                if supported_profile_value and signers:
                    item["cms_signers"] = signers
                    # Multi-signer CMS requires every signer to pass each property.
                    item.update(signers[0])
                    for key in ("crypto_status", "trust_status", "certificate_validity_status"):
                        values = {s[key] for s in signers}
                        item[key] = (
                            "invalid"
                            if "invalid" in values
                            else next(iter(values))
                            if len(values) == 1
                            else "unknown"
                        )
                    if any(s["timestamp_status"] == "unsupported" for s in signers):
                        item["timestamp_status"] = "unsupported"
                        item["reason_codes"].append("unsupported_timestamp_proof")
            except (
                ValueError,
                TypeError,
                IndexError,
                KeyError,
                NotImplementedError,
                UnsupportedAlgorithm,
            ) as exc:
                item["crypto_status"] = "unsupported" if "unsupported" in str(exc) else "invalid"
                # Keep machine codes fixed: never expose exception text from names.
                item["reason_codes"].append("malformed_or_unsupported_signature")
            result["signatures"].append(item)
    if result["signatures"]:
        signatures = result["signatures"]
        indices = [s["signature_index"] for s in signatures if s["final_revision_covered"]]
        invalid = any(
            s["crypto_status"] == "invalid"
            or s["coverage_status"] == "invalid"
            or s["certificate_validity_status"] in ("expired", "not_yet_valid")
            for s in signatures
        )
        unsupported = any(
            s["crypto_status"] == "unsupported" or s["timestamp_status"] == "unsupported"
            for s in signatures
        )
        result["status"] = "invalid" if invalid else "unsupported" if unsupported else "unknown"
        signed_lengths = [s["revision_length"] for s in signatures if s.get("revision_length")]
        result["final_revision"] = {
            "status": "invalid"
            if invalid
            else "unsupported"
            if unsupported
            else "unknown"
            if indices
            else "modified_after_signing",
            "covered_by_signature_indices": indices,
            "modified_after_last_signature": bool(
                signed_lengths and max(signed_lengths) < len(content)
            ),
            "document_length": len(content),
            "document_sha256": result["document_sha256"],
        }
        valid_lengths = [
            s["revision_length"]
            for s in signatures
            if s.get("revision_length") and s["crypto_status"] == "valid"
        ]
        for item in signatures:
            item["signed_revision"] = item.get("revision_index")
            item["signed_revision_sha256"] = item.get("revision_sha256")
            later_valid = [n for n in valid_lengths if n > item.get("revision_length", 0)]
            item["post_signing_changes"] = (
                # Appended revisions that a later valid signature covers up to the end
                # of the file are reported apart from unsigned modifications.
                "covered_by_later_valid_signature"
                if item["modified_after_signing"] and max(later_valid, default=0) == len(content)
                else "incremental_or_appended_bytes"
                if item["modified_after_signing"]
                else "none"
                if item["modified_after_signing"] is False
                else "unknown"
            )
    elif b"/ByteRange" in content:
        # A removed/hidden historical signature or an unsupported indirect layout
        # cannot be silently classified as an unsigned, inapplicable document.
        result["status"] = "unsupported"
        result["final_revision"]["status"] = "unsupported"
        result["reason_codes"] = ["unresolved_signature_dictionary"]
    if len(json.dumps(result, ensure_ascii=True).encode()) > 1024 * 1024:
        raise ValueError("signature output limit")
    return result
