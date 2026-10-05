"""Disposable CPU-only DOCX process; receives private files, never credentials."""

import json
import os
import resource
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1])
    request = json.loads((root / "request.json").read_text())
    from app.services.export_renderer import (
        AuthorizedExportInputs,
        ExportRenderError,
        RenderLimits,
        render_export_docx,
    )

    try:
        # The parent monitors RSS from process creation, including imports. Linux
        # additionally enforces address space for every allocation in rendering.
        # macOS reserves sparse virtual regions, so it uses the parent's RSS limit.
        if sys.platform != "darwin":
            resource.setrlimit(
                resource.RLIMIT_AS, (request["memory_bytes"], request["memory_bytes"])
            )
        candidate = render_export_docx(
            request["manifest"],
            AuthorizedExportInputs(
                root / "template.docx", {int(n): root / f"page-{n}.png" for n in request["pages"]}
            ),
            limits=RenderLimits(**request["limits"]),
            confidential=request.get("confidential", {}),
        )
        descriptor = os.open(root / "candidate.docx", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(candidate.content)
            output.flush()
            os.fsync(output.fileno())
        result = {
            "sha256": candidate.sha256,
            "size_bytes": candidate.size_bytes,
            "renderer_profile": candidate.renderer_profile,
            "manifest_hash": candidate.manifest_hash,
        }
    except ExportRenderError as exc:
        result = {"error": {"code": exc.code, "exit_code": 2}}
    except MemoryError:
        result = {"error": {"code": "export_memory_limit", "exit_code": 2}}
    except (OSError, ValueError, KeyError, TypeError):
        # No exception text or input content crosses the subprocess boundary.
        result = {"error": {"code": "export_render_failed", "exit_code": 4}}
    descriptor = os.open(root / "result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(result, output, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
