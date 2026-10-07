"""Memory-only multipart receiver with aggregate bounds before PDF decoding."""

from typing import NoReturn

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from app.core.errors import ServiceError
from app.schemas.attachment_contracts import FILE_BYTE_LIMIT, HTTP_JSON_LIMIT


def invalid(code="invalid_input", status=422) -> NoReturn:
    raise ServiceError(
        code, "A bounded metadata field and one unchanged PDF are required", status, 2
    )


async def receive(request, byte_limit):
    cap = min(byte_limit, FILE_BYTE_LIMIT)
    media, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if media != b"multipart/form-data" or not boundary or len(boundary) > 200:
        invalid()
    fields = {}
    current, header_name, header_value = bytearray(), bytearray(), bytearray()
    headers = {}
    state = {"name": "", "filename": "", "header_bytes": 0, "ended": False}

    def data(chunk, start, end):
        limit = cap if state["name"] == "files" else HTTP_JSON_LIMIT
        if len(current) + end - start > limit:
            invalid(
                "attachment_input_limit"
                if state["name"] == "metadata"
                else "attachment_file_limit",
                413,
            )
        current.extend(chunk[start:end])

    def header_field(chunk, start, end):
        state["header_bytes"] += end - start
        if state["header_bytes"] > 8192:
            invalid()
        header_name.extend(chunk[start:end])

    def header_data(chunk, start, end):
        state["header_bytes"] += end - start
        if state["header_bytes"] > 8192:
            invalid()
        header_value.extend(chunk[start:end])

    def header_end():
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        _, values = parse_options_header(headers.get(b"content-disposition", b""))
        name = values.get(b"name", b"")
        if name == b"files" and "files" in fields:
            invalid("attachment_upload_mode_not_enabled", 409)
        if name not in {b"files", b"metadata"} or name.decode() in fields:
            invalid()
        state["name"] = name.decode()
        if name == b"files":
            try:
                filename = values.get(b"filename", b"").decode("utf-8")
            except UnicodeDecodeError:
                invalid()
            if (
                not filename
                or len(filename) > 200
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
            ):
                invalid()
            state["filename"] = filename
        headers.clear()

    def part_end():
        fields[state["name"]] = bytes(current)
        current.clear()

    def end():
        state["ended"] = True

    parser = MultipartParser(
        boundary,
        {
            "on_header_field": header_field,
            "on_header_value": header_data,
            "on_header_end": header_end,
            "on_headers_finished": headers_finished,
            "on_part_data": data,
            "on_part_end": part_end,
            "on_end": end,
        },
    )
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > cap + HTTP_JSON_LIMIT + 16384:
                invalid("attachment_file_limit", 413)
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        invalid()
    if set(fields) != {"files", "metadata"} or not state["ended"]:
        invalid()
    return fields["metadata"], state["filename"], fields["files"]
