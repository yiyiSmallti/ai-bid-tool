"""Verify actual local Chinese OCR with synthetic raster content and no network."""

import argparse
import asyncio
import json
from pathlib import Path

import pymupdf
from app.providers.local_ocr import LocalOCR


async def verify(data_dir: Path, output: Path):
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=200)
        page.insert_text((30, 70), "合成测试 内存不少于64 GB", fontname="china-s", fontsize=26)
        image = page.get_pixmap(matrix=pymupdf.Matrix(3, 3)).tobytes("png")
    result = await LocalOCR("chi_sim+eng", str(data_dir)).recognize(image, 1)
    compact = "".join(result.text.split())
    assert "合成测试" in compact and "64" in compact and "GB" in compact, (
        "Synthetic Chinese text was not recognized"
    )
    evidence = {
        "ok": True,
        "synthetic_only": True,
        "recognized": result.text,
        "boxes": len(result.boxes),
        "usage": result.usage.model_dump(),
    }
    evidence["limitations"] = (
        "OCR may misrecognize characters; inspect the original raster before accepting citations."
    )
    await asyncio.to_thread(output.write_text, json.dumps(evidence, ensure_ascii=False, indent=2))
    print("Actual local Chinese OCR passed; evidence:", output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a new output file; existing evidence is retained")
    asyncio.run(verify(args.data_dir, args.output))


if __name__ == "__main__":
    main()
