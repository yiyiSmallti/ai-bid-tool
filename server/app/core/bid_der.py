"""Small bounded DER reader for offline signature validation (no BER coercion)."""

from __future__ import annotations

from dataclasses import dataclass

MAX_DER_BYTES = 4 * 1024 * 1024
MAX_DER_NODES = 30_000
MAX_DER_DEPTH = 32


class DERError(ValueError):
    """Untrusted encoding is malformed, noncanonical or beyond supported bounds."""


@dataclass(frozen=True)
class Node:
    tag: int
    value: bytes
    encoded: bytes
    children: tuple[Node, ...] = ()

    def expect(self, tag: int) -> Node:
        if self.tag != tag:
            raise DERError("unexpected DER tag")
        return self

    def integer(self) -> int:
        self.expect(2)
        return int.from_bytes(self.value, "big", signed=True)

    def oid(self) -> str:
        self.expect(6)
        parts: list[int] = []
        current = 0
        start = True
        for byte in self.value:
            if start and byte == 0x80:
                raise DERError("noncanonical OID arc")
            current = (current << 7) | (byte & 127)
            if current.bit_length() > 64:
                raise DERError("OID arc too large")
            start = not bool(byte & 128)
            if start:
                parts.append(current)
                current = 0
        if not start or not parts:
            raise DERError("truncated OID")
        first = parts.pop(0)
        prefix = [0, first] if first < 40 else [1, first - 40] if first < 80 else [2, first - 80]
        return ".".join(map(str, prefix + parts))


def encode(tag: int, value: bytes) -> bytes:
    length = len(value)
    if length < 128:
        return bytes([tag, length]) + value
    octets = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([tag, 128 | len(octets)]) + octets + value


def read_der(content: bytes, *, allow_zero_padding: bool = False) -> Node:
    """Read exactly one canonical, bounded value; only PDF Contents may have zero padding."""
    if not content or len(content) > MAX_DER_BYTES:
        raise DERError("DER byte limit")
    count = 0

    def read(offset: int, stop: int, depth: int) -> tuple[Node, int]:
        nonlocal count
        count += 1
        if count > MAX_DER_NODES or depth > MAX_DER_DEPTH or offset + 2 > stop:
            raise DERError("DER structure limit")
        begin = offset
        tag, first_length = content[offset : offset + 2]
        offset += 2
        if tag & 31 == 31 or tag in (0, 0x20):
            raise DERError("unsupported DER tag")
        if first_length & 128:
            size = first_length & 127
            if size == 0 or size > 4 or offset + size > stop or content[offset] == 0:
                raise DERError("noncanonical DER length")
            length = int.from_bytes(content[offset : offset + size], "big")
            offset += size
            if length < 128:
                raise DERError("nonminimal DER length")
        else:
            length = first_length
        end = offset + length
        if end > stop:
            raise DERError("truncated DER value")
        value = content[offset:end]
        children = []
        if tag & 32:
            if tag & 192 == 0 and tag not in (0x30, 0x31):
                raise DERError("constructed primitive DER value")
            cursor = offset
            while cursor < end:
                child, cursor = read(cursor, end, depth + 1)
                children.append(child)
            if tag == 0x31 and [n.encoded for n in children] != sorted(n.encoded for n in children):
                raise DERError("noncanonical DER SET order")
        elif tag & 192 == 0:
            if tag in (16, 17):
                raise DERError("primitive collection")
            if tag == 2 and (
                not value
                or (len(value) > 1 and value[0] == 0 and value[1] < 128)
                or (len(value) > 1 and value[0] == 255 and value[1] >= 128)
            ):
                raise DERError("noncanonical INTEGER")
            if tag == 1 and value not in (b"\x00", b"\xff"):
                raise DERError("noncanonical BOOLEAN")
            if tag == 5 and value:
                raise DERError("noncanonical NULL")
            if tag == 3 and (
                not value
                or value[0] > 7
                or (len(value) == 1 and value[0])
                or (len(value) > 1 and value[0] and value[-1] & ((1 << value[0]) - 1))
            ):
                raise DERError("noncanonical BIT STRING")
        node = Node(tag, value, content[begin:end], tuple(children))
        if tag == 6:
            node.oid()
        return node, end

    result, end = read(0, len(content), 0)
    if end != len(content) and (not allow_zero_padding or any(content[end:])):
        raise DERError("trailing DER bytes")
    return result
