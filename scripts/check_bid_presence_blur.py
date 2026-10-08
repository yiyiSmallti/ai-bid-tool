"""Render synthetic pages and retain a repeatable presence-blur privacy proxy.

Run from the repository root with PYTHONPATH=server .venv/bin/python
scripts/check_bid_presence_blur.py. This is a bounded regression experiment,
not an OCR guarantee; exact-pixel human clearance remains required.
"""

import json
from pathlib import Path

import pymupdf
from app.services.bid_presence_blur import derivative


def metrics(pixmap, bounds):
    x0, y0, x1, y1 = bounds
    values = pixmap.samples
    bands, edges = [], []
    for y in range(y0, y1):
        for x in range(x0, x1):
            offset = (y * pixmap.width + x) * 3
            gray = sum(values[offset : offset + 3]) / 3
            bands.append(gray)
            if x > x0:
                previous = sum(values[offset - 3 : offset]) / 3
                edges.append((gray - previous) ** 2)
    mean = sum(bands) / len(bands)
    return {
        "edge_energy": sum(edges) / len(edges),
        "variance": sum((v - mean) ** 2 for v in bands) / len(bands),
    }


def main():
    target = Path("data/work/bid-review-clef/blur-check")
    target.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    for index in range(11):
        page.insert_text((42, 110 + index * 24), "PRIVATE COMPANY Account 1234567890", fontsize=18)
    page.insert_text((42, 390), "秘密公司 身份证 1234567890", fontname="china-s", fontsize=18)
    page.draw_circle((450, 610), 65, color=(1, 0, 0), width=7)
    page.insert_text((397, 605), "PRIVATE SEAL", fontsize=13, color=(1, 0, 0))
    source = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    original = source.tobytes("png")
    jpeg, descriptor = derivative(original)
    result = pymupdf.Pixmap(jpeg)
    comparison = pymupdf.Pixmap(source, result.width, result.height)
    bounds = (12, 28, int(result.width * 0.82), int(result.height * 0.52))
    before, after = metrics(comparison, bounds), metrics(result, bounds)
    pixels = result.samples
    red = sum(pixels[i] - max(pixels[i + 1], pixels[i + 2]) > 18 for i in range(0, len(pixels), 3))
    ratio = after["edge_energy"] / before["edge_energy"]
    checks = {
        "edge_energy_ratio_below_0_02": ratio < 0.02,
        "text_band_variance_ratio_below_0_2": after["variance"] / before["variance"] < 0.2,
        "red_shape_pixels_at_least_200": red >= 200,
        "no_exif": b"Exif\x00\x00" not in jpeg,
    }
    report = {
        "profile": descriptor,
        "text_before": before,
        "text_after": after,
        "edge_energy_ratio": ratio,
        "red_shape_pixels": red,
        "checks": checks,
        "privacy_limit": "Proxy checks synthetic text detail loss only. Human reviews exact JPEG; faint/gray/partial/wrong-owner/position/seam/signature cases are unvalidated.",
    }
    (target / "synthetic-source.png").write_bytes(original)
    (target / "presence.jpg").write_bytes(jpeg)
    (target / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    assert all(checks.values()), "Blur proxy failed; inspect retained synthetic artifacts"


if __name__ == "__main__":
    main()
