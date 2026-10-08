"""Bounded memory-only multipart input; no plaintext upload spool or implicit roles."""

from typing import NoReturn

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from app.core.errors import ServiceError
from app.schemas.bid_review import FILE_BYTE_LIMIT, FILE_LIMIT, SUBMISSION_BYTE_LIMIT

JSON_LIMIT = 128 * 1024


def invalid(code="invalid_input", status=422) -> NoReturn:
    raise ServiceError(code, "Invalid or oversized submission upload", status, 2)


async def receive(request, deployment_limit):
    file_cap = min(FILE_BYTE_LIMIT, deployment_limit)
    total_cap = min(SUBMISSION_BYTE_LIMIT, deployment_limit)
    media, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if media != b"multipart/form-data" or not boundary or len(boundary) > 200:
        invalid()
    current, header_name, header_value = bytearray(), bytearray(), bytearray()
    headers, files = {}, []
    state = {
        "name": "",
        "filename": "",
        "media_type": "",
        "header_bytes": 0,
        "ended": False,
        "metadata": None,
        "file_bytes": 0,
    }

    def part_begin():
        state["header_bytes"] = 0
        headers.clear()

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
        key = bytes(header_name).lower()
        if key in headers:
            invalid()
        headers[key] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        disposition, values = parse_options_header(headers.get(b"content-disposition", b""))
        name = values.get(b"name", b"")
        if disposition != b"form-data" or name not in {b"files", b"metadata"}:
            invalid()
        state["name"] = name.decode()
        if name == b"metadata":
            if state["metadata"] is not None or b"filename" in values:
                invalid()
        else:
            if len(files) >= FILE_LIMIT:
                invalid("bid_file_limit", 413)
            try:
                filename = values.get(b"filename", b"").decode("utf-8")
                content_type = headers.get(b"content-type", b"").decode("ascii")
            except UnicodeError:
                invalid()
            if (
                not filename
                or len(filename) > 200
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)
            ):
                invalid()
            state["filename"], state["media_type"] = filename, content_type

    def data(chunk, start, end):
        cap = file_cap if state["name"] == "files" else JSON_LIMIT
        if len(current) + end - start > cap:
            invalid("bid_upload_limit", 413)
        if state["name"] == "files":
            state["file_bytes"] += end - start
            if state["file_bytes"] > total_cap:
                invalid("bid_upload_limit", 413)
        current.extend(chunk[start:end])

    def part_end():
        if state["name"] == "metadata":
            state["metadata"] = bytes(current)
        else:
            files.append((state["filename"], state["media_type"], bytes(current)))
        current.clear()

    def end():
        state["ended"] = True

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": part_begin,
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
            if total > total_cap + JSON_LIMIT + (FILE_LIMIT + 1) * 8192:
                invalid("bid_upload_limit", 413)
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        invalid()
    if state["metadata"] is None or not 2 <= len(files) <= FILE_LIMIT or not state["ended"]:
        invalid()
    return bytes(state["metadata"]), files
