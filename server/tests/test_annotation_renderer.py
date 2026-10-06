"""Exercise the raw B05 Rust protocol, not Linux OS sandbox acceptance.

The test-only subclass explicitly calls Rust directly so these pixel/hash checks
remain portable to macOS. Production exposes no unsandboxed flag or environment.
"""

import asyncio
import hashlib
import json
import os
import struct
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.providers.annotation_renderer import (
    AnnotationRenderer,
    approval_binding_sha256,
    content_pixel_sha256,
    provenance_sha256,
    strip_candidate_content,
)
from app.providers.base import ProviderFailure
from app.providers.screenshot_renderer import validate_png
from app.schemas.annotation_contracts import AnnotationRenderRequest
from test_screenshot_renderer import _png_chunk, _unfilter_rgb_png

ID = UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)


class RawProtocolRenderer(AnnotationRenderer):
    def _process_arguments(self, *, describe=False):
        arguments = [os.fspath(self._validated_binary())]
        return arguments + ["--annotation-describe"] if describe else arguments


def source_png(width=8, height=6):
    import zlib

    raw = b"".join(b"\x00" + b"\x11\x22\x33" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


@pytest.fixture(scope="module")
def renderer():
    path = Path(
        os.environ.get(
            "BID_SCREENSHOT_RENDERER",
            Path(__file__).resolve().parents[2] / "data/work/bin/bid-screenshot-renderer",
        )
    )
    if not path.is_file():
        pytest.skip("build the renderer before B05 protocol acceptance")
    return RawProtocolRenderer(binary=path)


def candidate_request(renderer, png, crop=None, boxes=None):
    image = validate_png(png)
    original = {
        "name": "certificate.pdf",
        "sha256": "a" * 64,
        "size_bytes": 100,
        "page_count": 1,
        "media_type": "application/pdf",
    }
    archive = {
        **{
            key: ID
            for key in (
                "id",
                "org_id",
                "task_id",
                "task_certificate_id",
                "certificate_id",
                "certificate_revision_id",
                "certificate_file_id",
                "created_by",
            )
        },
        "original": original,
        "page": 1,
        "preview": {"name": "page.png", **image},
        "rendered_at": NOW,
        "active_selection": True,
    }
    source = {
        "kind": "certificate_page",
        "archive": archive,
        "source_png": image,
        "source_time": NOW,
        "original_sha256": "a" * 64,
        "selection_id": ID,
        "resource_revision_id": ID,
        "authorized_content": {
            "x": 0,
            "y": 0,
            "width": image["width_px"],
            "height": image["height_px"],
        },
    }
    request = AnnotationRenderRequest(
        source=source,
        plan={"crop": crop, "boxes": boxes or []},
        plan_sha256=hashlib.sha256(
            json.dumps(
                {"boxes": boxes or [], "crop": crop}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        renderer=renderer.identity(),
        content_mode="source_png",
        input_content_sha256=image["sha256"],
        provenance_sha256="0" * 64,
    )
    return request.model_copy(update={"provenance_sha256": provenance_sha256(request)})


def approved_release(renderer, request, receipt, png):
    approval = {
        **{
            key: ID
            for key in (
                "evidence_id",
                "card_id",
                "card_revision_id",
                "confirmed_by",
                "candidate_annotation_id",
                "candidate_rendition_id",
            )
        },
        "card_revision": 2,
        "confirmed_at": NOW,
        "candidate_image_sha256": receipt.image.sha256,
        "candidate_plan_sha256": request.plan_sha256,
        "candidate_content_mapping": receipt.canvas.mapping.model_dump(mode="json"),
        "content_pixel_sha256": receipt.content_pixel_sha256,
        "root_mapping_sha256": receipt.root_mapping_sha256,
        "reviewed_region": {"x": 0, "y": 0, "width": 1, "height": 1},
        "requirement": {
            "requirement_id": ID,
            "review_revision": 1,
            "review_hash": "b" * 64,
            "state": "confirmed",
            "source_binding_sha256": "c" * 64,
        },
        "decision_kind": "single_domain",
        "review_domain": "commercial",
        "co_sign": None,
        "card_content_sha256": "d" * 64,
        "evidence_binding_sha256": "e" * 64,
        "approval_binding_sha256": "0" * 64,
    }
    # Serialize normalized datetime/UUID fields before the canonical approval hash.
    from app.schemas.annotation_contracts import AnnotationApprovalBinding

    approval = AnnotationApprovalBinding.model_validate(approval)
    approval = approval.model_copy(
        update={"approval_binding_sha256": approval_binding_sha256(approval)}
    )
    marked = strip_candidate_content(png, receipt.canvas.mapping)
    release = AnnotationRenderRequest(
        source=request.source,
        plan=request.plan,
        plan_sha256=request.plan_sha256,
        renderer=renderer.identity("annotation-release-v1"),
        content_mode="marked_candidate_content",
        input_content_sha256=hashlib.sha256(marked).hexdigest(),
        approval=approval,
        provenance_sha256="0" * 64,
    )
    return marked, release.model_copy(update={"provenance_sha256": provenance_sha256(release)})


def _save_rendering_artifacts(candidate, png, receipt, released):
    artifacts = Path(__file__).resolve().parents[2] / "data/work/annotation-renderer-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "candidate.png").write_bytes(candidate)
    (artifacts / "release.png").write_bytes(png)
    (artifacts / "receipts.json").write_text(
        json.dumps(
            {
                "candidate": receipt.model_dump(mode="json"),
                "release": released.model_dump(mode="json"),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


@pytest.mark.asyncio
async def test_candidate_prediction_pixels_and_retry_bytes(renderer):
    source = source_png()
    request = candidate_request(
        renderer,
        source,
        crop={"x": 1, "y": 1, "width": 6, "height": 4},
        boxes=[{"x": 2, "y": 2, "width": 3, "height": 2}],
    )
    prediction = await renderer.predict(request)
    png, receipt = await renderer.render(source, request)
    assert receipt.canvas == prediction
    assert prediction.width_px == 1024
    marked = strip_candidate_content(png, receipt.canvas.mapping)
    width, height, pixels = _unfilter_rgb_png(marked)
    assert (width, height) == (6, 4)
    assert pixels[0] == (17, 34, 51)
    assert pixels[7] == (255, 0, 0)
    assert await renderer.render(source, request) == (png, receipt)


@pytest.mark.asyncio
async def test_release_preserves_reviewed_pixels_and_mapping(renderer):
    source = source_png()
    request = candidate_request(renderer, source, boxes=[{"x": 1, "y": 1, "width": 5, "height": 4}])
    candidate, receipt = await renderer.render(source, request)
    marked, release = approved_release(renderer, request, receipt, candidate)
    png, released = await renderer.render(marked, release)
    assert released.content_pixel_sha256 == receipt.content_pixel_sha256
    assert released.root_mapping_sha256 == receipt.root_mapping_sha256
    assert released.canvas.mapping.model_dump(
        exclude={"footer_height"}
    ) == receipt.canvas.mapping.model_dump(exclude={"footer_height"})
    assert strip_candidate_content(png, released.canvas.mapping) == marked
    assert png != candidate
    await asyncio.to_thread(_save_rendering_artifacts, candidate, png, receipt, released)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["input_content_sha256", "provenance_sha256"])
async def test_python_rejects_tampered_input_hash(renderer, field):
    source = source_png()
    request = candidate_request(renderer, source).model_copy(update={field: "0" * 64})
    with pytest.raises(ProviderFailure):
        await renderer.render(source, request)


@pytest.mark.asyncio
async def test_rust_independently_rejects_metadata_and_identity_tampering(renderer):
    source = source_png()
    request = candidate_request(renderer, source)
    for field in ["plan_sha256", "provenance_sha256", "protocol"]:
        changed = request.model_dump(mode="json")
        changed[field] = "0" * 64
        invalid = AnnotationRenderRequest.model_construct(**changed)
        with pytest.raises(ProviderFailure):
            await renderer._exchange(source, invalid)
    changed = request.model_copy(
        update={"renderer": request.renderer.model_copy(update={"font_bundle_sha256": "0" * 64})}
    )
    with pytest.raises(ProviderFailure):
        await renderer._exchange(source, changed)


@pytest.mark.asyncio
async def test_release_rejects_changed_approval_and_pixels(renderer):
    source = source_png()
    request = candidate_request(renderer, source)
    candidate, receipt = await renderer.render(source, request)
    marked, release = approved_release(renderer, request, receipt, candidate)
    changed = release.model_copy(
        update={"approval": release.approval.model_copy(update={"confirmed_by": UUID(int=2)})}
    )
    with pytest.raises(ProviderFailure):
        await renderer._exchange(marked, changed)
    with pytest.raises(ProviderFailure):
        await renderer.render(source_png(7, 6), release)


@pytest.mark.asyncio
async def test_prediction_rejects_complete_canvas_overflow_without_render(renderer):
    png = source_png(1, 8192)
    request = candidate_request(renderer, png)
    with pytest.raises(ProviderFailure) as error:
        await renderer.predict(request)
    assert error.value.code == "output_limit_exceeded" and not error.value.retryable


def test_content_digest_has_dimension_domain_separator():
    pixels = b"\x01\x02\x03" * 6
    assert content_pixel_sha256(2, 3, pixels) != content_pixel_sha256(3, 2, pixels)
    with pytest.raises(ProviderFailure):
        content_pixel_sha256(2, 3, pixels[:-1])


@pytest.mark.asyncio
async def test_archived_mupdf_resolution_metadata_is_input_only(renderer):
    source = source_png()
    resolution = _png_chunk(b"pHYs", struct.pack(">IIB", 5906, 5906, 1))
    source = source[:33] + resolution + source[33:]
    image = validate_png(source, allow_input_metadata=True)
    request = candidate_request(renderer, source_png())
    payload = request.model_dump(mode="json")
    payload["source"]["source_png"] = image
    payload["source"]["archive"]["preview"].update(image)
    payload["input_content_sha256"] = image["sha256"]
    request = AnnotationRenderRequest.model_validate(payload)
    request = request.model_copy(update={"provenance_sha256": provenance_sha256(request)})
    output, receipt = await renderer.render(source, request)
    assert validate_png(output) == receipt.image.model_dump(mode="json")
    with pytest.raises(ProviderFailure):
        validate_png(source)


@pytest.mark.asyncio
async def test_lifecycle_fake_retains_real_pixels_hashes_and_release():
    from annotation_test_renderer import FakeAnnotationRenderer

    fake = FakeAnnotationRenderer()
    source = source_png()
    request = candidate_request(fake, source)
    candidate, receipt = await fake.render(source, request)
    marked, release = approved_release(fake, request, receipt, candidate)
    output, released = await fake.render(marked, release)
    assert strip_candidate_content(output, released.canvas.mapping) == marked
    assert released.content_pixel_sha256 == receipt.content_pixel_sha256


@pytest.mark.asyncio
async def test_vendor_content_plane_excludes_upstream_footer_and_padding(renderer):
    from app.providers.annotation_renderer import root_mapping_sha256
    from app.providers.screenshot_renderer import ScreenshotRenderer

    source = source_png()
    parent, parent_receipt = await ScreenshotRenderer(binary=renderer.binary).render(
        source,
        {
            "profile": "screenshot-markup-v1",
            "plan": {"redact": [{"x": 0, "y": 0, "width": 1, "height": 1}]},
            "provenance": {
                "source_kind": "vendor_web",
                "source_sha256": "a" * 64,
                "plan_sha256": "b" * 64,
                "source_time_kind": "captured_at",
                "source_time": "2026-10-06T00:00:00Z",
                "source_id": str(ID),
                "page": 1,
            },
        },
    )
    mapping = parent_receipt["mapping"]
    request = candidate_request(renderer, source, crop={"x": 0, "y": 0, "width": 4, "height": 3})
    payload = request.model_dump(mode="json")
    archive = {
        **{
            key: str(ID)
            for key in ("id", "sandbox_run_id", "task_resource_id", "product_revision_id")
        },
        "format": "web",
        "source_field": "official_url",
        "source_url_sha256": "1" * 64,
        "final_url_sha256": "2" * 64,
        "final_origin": "https://vendor.test",
        "title": None,
        "captured_at": "2026-10-06T00:00:00Z",
        "content_sha256": "a" * 64,
        "archive": {"sha256": "a" * 64, "size_bytes": 100, "media_type": "application/zip"},
        "policy_revision": "vendor-v1",
        "incomplete": False,
        "failed_request_count": 0,
    }
    payload["source"] = {
        **{
            key: str(ID)
            for key in (
                "org_id",
                "task_id",
                "extraction_job_id",
                "asset_id",
                "parent_rendition_id",
                "selection_id",
                "resource_revision_id",
                "privacy_review_id",
            )
        },
        "kind": "vendor_rendition",
        "archive": archive,
        "page": {"kind": "web_page", "page": 1},
        "source_time_kind": "vendor_captured_at",
        "source_time": "2026-10-06T00:00:00Z",
        "original_sha256": "a" * 64,
        "source_png": parent_receipt["image"],
        "root_source_png_sha256": hashlib.sha256(source).hexdigest(),
        "authorized_content": {
            "x": mapping["content_offset_x"],
            "y": 0,
            "width": mapping["content_width"],
            "height": mapping["content_height"],
        },
        "source_crop_mapping": mapping,
        "source_profile": "screenshot-markup-v1",
        "privacy_lineage_sha256": "3" * 64,
        "root_mapping_sha256": "4" * 64,
    }
    payload["input_content_sha256"] = parent_receipt["image"]["sha256"]
    request = AnnotationRenderRequest.model_validate(payload)
    request = request.model_copy(update={"provenance_sha256": provenance_sha256(request)})
    output, receipt = await renderer.render(parent, request)
    marked = strip_candidate_content(output, receipt.canvas.mapping)
    width, height, pixels = _unfilter_rgb_png(marked)
    assert (width, height) == (4, 3)
    assert pixels[0] == (0, 0, 0)
    assert pixels[1] == (17, 34, 51)
    assert receipt.root_mapping_sha256 == root_mapping_sha256(request.source, request.plan.crop)


@pytest.mark.parametrize("kind", ["missing", "nonexecutable", "symlink"])
def test_annotation_binary_configuration_is_nonretryable(tmp_path, kind):
    path = tmp_path / "configured-renderer"
    if kind == "nonexecutable":
        path.write_text("not an executable")
    elif kind == "symlink":
        target = tmp_path / "real-file"
        target.write_text("not an executable")
        path.symlink_to(target)
    configured = AnnotationRenderer(binary=path)
    with pytest.raises(ProviderFailure) as error:
        configured.identity()
    assert error.value.code == "annotation_renderer_unavailable"
    assert error.value.retryable is False


def test_unsupported_annotation_profile_is_configuration_error(renderer):
    with pytest.raises(ProviderFailure) as error:
        renderer.identity("unknown-annotation-v2")
    assert error.value.code == "unsupported_protocol" and not error.value.retryable


@pytest.mark.asyncio
async def test_changed_renderer_identity_is_nonretryable(renderer):
    source = source_png()
    request = candidate_request(renderer, source)
    changed = request.model_copy(
        update={"renderer": request.renderer.model_copy(update={"version": "not-this-build"})}
    )
    with pytest.raises(ProviderFailure) as error:
        await renderer.predict(changed)
    assert error.value.code == "annotation_renderer_unavailable" and not error.value.retryable


@pytest.mark.asyncio
async def test_structured_legacy_binary_diagnostic_is_configuration_error(tmp_path):
    binary = tmp_path / "legacy-renderer"
    binary.write_text(
        '#!/bin/sh\ncat >/dev/null\nprintf \'%s\\n\' \'{"code":"invalid_request","message":"request metadata is invalid"}\' >&2\nexit 2\n'
    )
    binary.chmod(0o700)
    configured = RawProtocolRenderer(binary=binary)
    source = source_png()
    request = candidate_request(configured, source)
    with pytest.raises(ProviderFailure) as error:
        await configured.predict(request)
    assert error.value.code == "unsupported_protocol" and not error.value.retryable


@pytest.mark.asyncio
async def test_sandbox_configuration_exit_precedes_missing_stdout_frame(tmp_path):
    binary = tmp_path / "sandbox-rejection"
    binary.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'annotation_sandbox_unavailable: unsafe_binary' >&2\nexit 78\n"
    )
    binary.chmod(0o700)
    configured = RawProtocolRenderer(binary=binary)
    request = candidate_request(configured, source_png())
    with pytest.raises(ProviderFailure) as error:
        await configured.predict(request)
    assert error.value.code == "annotation_sandbox_unavailable" and not error.value.retryable


@pytest.mark.asyncio
async def test_production_provider_never_launches_raw_renderer_on_unsupported_host(renderer):
    import sys

    if sys.platform == "linux":
        pytest.skip("unsupported-host boundary is exercised on non-Linux hosts")
    production = AnnotationRenderer(binary=renderer.binary)
    request = candidate_request(renderer, source_png())
    with pytest.raises(ProviderFailure) as error:
        await production.predict(request)
    assert error.value.code == "annotation_sandbox_unavailable" and not error.value.retryable
