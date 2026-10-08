"""Credential-free disposable report renderer with private, bounded output files."""

import hashlib
import json
import resource
import sys
from pathlib import Path

INPUT_LIMIT = 100 * 1024 * 1024


def main():
    root = Path(sys.argv[1])
    with (root / "request.json").open("rb") as handle:
        content = handle.read(INPUT_LIMIT + 1)
    if len(content) > INPUT_LIMIT:
        return 2
    request = json.loads(content)
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (request["memory_bytes"], request["memory_bytes"]))
    from app.jobs.export_render import private_write
    from app.services.bid_review_report_renderer import render_report

    try:
        word, console = render_report(request["snapshot"])
        descriptors = {}
        for name, content in (("report.docx", word), ("report.json", console)):
            if not content or len(content) > request["max_output_bytes"]:
                raise ValueError("output limit")
            private_write(root / name, content)
            descriptors[name] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        result = {"files": descriptors}
    except MemoryError:
        result = {"error": "bid_review_report_memory_limit"}
    except Exception:
        # Never copy source text, input filenames or exception details to the parent.
        result = {"error": "bid_review_report_render_failed"}
    private_write(root / "result.json", json.dumps(result, sort_keys=True).encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
