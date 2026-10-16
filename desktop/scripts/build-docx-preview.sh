#!/bin/sh
# Builds the export page-preview renderer into the directory given as $1: the
# bid-docx-preview binary and the two scripts it loads from beside itself.
set -eu

desktop_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out=$1

jszip_version=3.10.1
jszip_sha256=acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e
docx_preview_version=0.3.5
docx_preview_sha256=cf0201bc615152efd395ca61f88780a5d1ad12eddaa583d45ab0c4e405b98fdc

mkdir -p "$out"
curl -fsSL -o "$out/jszip.min.js" "https://cdn.jsdelivr.net/npm/jszip@$jszip_version/dist/jszip.min.js"
curl -fsSL -o "$out/docx-preview.min.js" \
    "https://cdn.jsdelivr.net/npm/docx-preview@$docx_preview_version/dist/docx-preview.min.js"
(cd "$out" && shasum -a 256 -c - <<SUMS
$jszip_sha256  jszip.min.js
$docx_preview_sha256  docx-preview.min.js
SUMS
)
xcrun swiftc -O -o "$out/bid-docx-preview" "$desktop_dir/preview/docx-preview.swift"
