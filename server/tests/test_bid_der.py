"""DER resource/canonicalization edges beneath HTTP signature acceptance."""

import pytest
from app.core.bid_der import DERError, encode, read_der
from app.services.bid_pdf_signatures import _sm2_verify, _sm3_digest
from bid_signature_fixtures import sign, sm2_key
from gmssl import sm3


@pytest.mark.parametrize(
    "malformed",
    [
        b"\x30\x80\0\0",  # indefinite BER
        b"\x04\x81\x01\0",  # nonminimal length
        b"\x02\x02\0\x01",  # nonminimal positive integer
        b"\x02\x02\xff\x80",  # nonminimal negative integer
        b"\x23\x03\x02\x01\x01",  # constructed BIT STRING
        b"\x06\x02\x80\x01",  # nonminimal OID arc
        b"\x30\x02\x05",  # truncated collection
        b"\x30\0\x01",  # trailing bytes
        b"\x31\x06\x02\x01\x02\x02\x01\x01",  # unsorted SET
        b"\x03\x02\x07\x01",  # nonzero padding bits
    ],
)
def test_noncanonical_der_rejected(malformed):
    with pytest.raises(DERError):
        read_der(malformed)


def test_der_depth_node_and_byte_bounds():
    nested = encode(5, b"")
    for _ in range(34):
        nested = encode(0x30, nested)
    for oversized in (
        nested,
        encode(0x30, b"\x05\0" * 30_001),
        encode(4, b"x" * (4 * 1024 * 1024)),
    ):
        with pytest.raises(DERError):
            read_der(oversized)


def test_pdf_padding_is_explicit_and_only_zero():
    encoded = encode(0x30, encode(2, b"\x01"))
    assert read_der(encoded + b"\0" * 64, allow_zero_padding=True).encoded == encoded
    with pytest.raises(DERError):
        read_der(encoded + b"\0" * 64)
    with pytest.raises(DERError):
        read_der(encoded + b"\0\x01", allow_zero_padding=True)


def test_sm2_coordinate_prefix_04_is_preserved():
    """The selected gmssl version lstrips XY beginning 04 in its constructor."""
    key = sm2_key(11)
    assert key.public_key.startswith("04")
    certificate = {"public_key": b"\x04" + bytes.fromhex(key.public_key)}
    message = b"synthetic gmssl leading coordinate regression"
    assert _sm2_verify(certificate, sign(key, message), message)
    assert not _sm2_verify(certificate, sign(key, message, wrong_user_id=True), message)


def test_streaming_sm3_published_abc_vector():
    assert (
        _sm3_digest(b"abc").hex()
        == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )


@pytest.mark.parametrize("length", [0, 1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 129, 1000])
def test_streaming_sm3_padding_and_chunk_boundaries(length):
    payload = bytes(index % 256 for index in range(length))
    expected = bytes.fromhex(sm3.sm3_hash(list(payload)))
    assert _sm3_digest(payload) == expected
    assert _sm3_digest(payload[:5], payload[5:17], b"", payload[17:63], payload[63:]) == expected
