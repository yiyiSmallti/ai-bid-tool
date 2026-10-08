"""Child entry point: set OS limits before importing PyMuPDF or reading a PDF."""

import json
import os
import sys
from pathlib import Path


def apply_limits(request: dict) -> None:
    if os.name != "posix":
        return
    import resource

    limits = {
        "RLIMIT_CPU": request["cpu_seconds"],
        "RLIMIT_FSIZE": request["output_bytes"],
        "RLIMIT_CORE": 0,
    }
    # Darwin's sparse virtual address reservations are not a Linux-style heap
    # boundary. Do not advertise a memory cap that this platform cannot enforce.
    if sys.platform != "darwin":
        limits.update(RLIMIT_AS=request["memory_bytes"], RLIMIT_DATA=request["memory_bytes"])
    for name, requested in limits.items():
        kind = getattr(resource, name, None)
        if kind is None:
            continue
        soft, hard = resource.getrlimit(kind)
        cap = min(value for value in (requested, soft, hard) if value != resource.RLIM_INFINITY)
        resource.setrlimit(kind, (cap, cap))


def main(root: Path) -> int:
    os.umask(0o077)
    request = json.loads((root / "request.json").read_bytes())
    try:
        apply_limits(request)
        # Neither module is imported until the child is constrained.
        from app.core.errors import ServiceError
        from app.core.pdf_reading import read_document

        content = (root / "input.pdf").read_bytes()
        operation, arguments = request["operation"], request["arguments"]
        count = size = 0
        try:
            if operation in ("parse", "validate"):
                records = read_document(content, arguments["max_pages"], operation == "parse")
            elif operation in {"bid_validate", "bid_prepare"}:
                from app.core.pdf_reading import read_bid_document

                records = read_bid_document(
                    content, arguments["max_pages"], operation == "bid_prepare"
                )
            elif operation == "bid_signatures":
                import base64

                from app.services.bid_pdf_signatures import validate_pdf

                anchors = [
                    base64.b64decode(anchor["certificate_der_base64"], validate=True)
                    for anchor in arguments["anchors"]
                ]
                records = iter([validate_pdf(content, anchors)])
            elif operation in {"bid_docx_validate", "bid_docx_structure"}:
                from app.core.bid_docx import inspect_docx

                records = iter([inspect_docx(content, operation == "bid_docx_structure")])
            else:
                from app.core.pdf_rendering import run

                records = iter([run(operation, content, arguments)])
            with (root / "pages.jsonl").open("xb") as output:
                for record in records:
                    line = json.dumps(record, ensure_ascii=True).encode() + b"\n"
                    size += len(line)
                    if len(line) > request["record_bytes"] or size > request["output_bytes"]:
                        return 1
                    output.write(line)
                    count += 1
            result = {"count": count}
        except ServiceError as exc:
            result = {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "status": exc.status,
                    "exit_code": exc.exit_code,
                }
            }
        (root / "result.json").write_text(json.dumps(result))
        return 0
    except Exception:
        # Native exceptions may include document contents. A failed process has
        # one non-retryable mapping in the parent; no details leave this child.
        return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
