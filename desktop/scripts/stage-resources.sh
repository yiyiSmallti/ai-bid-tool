#!/bin/sh
# Stages what the app runs into resources/darwin-arm64, which `mygo build`
# copies into Contents/Resources: a standalone Python with the server and its
# locked dependencies installed, the migration files read from
# the server directory, PostgreSQL, the screenshot renderer, the export
# page-preview renderer and the OCR language data.
set -eu

desktop_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repository_dir=$(dirname -- "$desktop_dir")
work_dir="$repository_dir/data/work/desktop-stage"
out="$desktop_dir/resources/darwin-arm64"

python_version=3.12.13
# The language data the container installs from Debian's tesseract-ocr packages.
tessdata_tag=4.1.0
chi_sim_sha256=a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730
eng_sha256=7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2

rm -rf "$out"
mkdir -p "$work_dir" "$out/bin" "$out/tessdata" "$out/server/server"

# A python-build-standalone interpreter finds its library relative to itself,
# so the copy runs from wherever the app is installed.
UV_PYTHON_INSTALL_DIR="$work_dir/python" uv python install "$python_version"
cp -R "$work_dir/python/cpython-$python_version-macos-aarch64-none" "$out/python"
python="$out/python/bin/python3"
# The interpreter is private to the app, so packages install into it directly.
rm -f "$out/python/lib/python3.12/EXTERNALLY-MANAGED"

uv export --project "$repository_dir" --frozen --no-dev --no-emit-project \
    --output-file "$work_dir/requirements.txt" >/dev/null
uv pip install --python "$python" --no-deps --require-hashes -r "$work_dir/requirements.txt"
uv pip install --python "$python" --no-deps "$repository_dir"
# Entry-point scripts name the staging path in their shebang; the app runs
# `python -m` instead.
grep -l -I "^#!$out/" "$out/python/bin/"* | while IFS= read -r script; do rm -f "$script"; done
# MyGo copies no hidden files into the app. Wheels repaired by delocate keep
# their libraries in .dylibs, so those move to dylibs, and the modules that
# load them are pointed there and signed again. Other hidden files are not
# needed at run time; removing them keeps the staged tree what the app holds.
find "$out" -name .dylibs -type d -prune | while IFS= read -r libs; do
    package=$(dirname -- "$libs")
    mv "$libs" "$package/dylibs"
    find "$package" -type f \( -name '*.so' -o -name '*.dylib' \) | while IFS= read -r module; do
        changed=
        for old in $(otool -l "$module" |
            awk '/cmd LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD)_DYLIB$/ { getline; getline; print $2 }' |
            grep '/\.dylibs/'); do
            install_name_tool -change "$old" "$(printf '%s' "$old" | sed 's|/\.dylibs/|/dylibs/|')" \
                "$module" 2>/dev/null
            changed=1
        done
        [ -z "$changed" ] || codesign --force --sign - "$module"
    done
done
find "$out" -name '.*' -prune -exec rm -rf {} +
# The bundle is read-only once signed: unchecked-hash bytecode stays valid
# after the copy changes modification times. Without -f, compileall keeps the
# timestamp bytecode its own worker processes cache while importing.
find "$out/python" -name __pycache__ -type d -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 "$python" -m compileall -f -q -j 0 --invalidation-mode unchecked-hash \
    "$out/python/lib/python3.12"

# init-db reads alembic.ini and the migrations relative to the server directory.
cp "$repository_dir/alembic.ini" "$out/server/"
cp -R "$repository_dir/server/migrations" "$out/server/server/"
find "$out/server" -name __pycache__ -type d -prune -exec rm -rf {} +

"$desktop_dir/scripts/build-postgres.sh" "$out/pgsql"

"$repository_dir/scripts/build_screenshot_renderer.sh"
install -m 0755 "$repository_dir/data/work/bin/bid-screenshot-renderer" "$out/bin/"

"$desktop_dir/scripts/build-docx-preview.sh" "$out/preview"

for language in chi_sim eng; do
    curl -fsSL -o "$out/tessdata/$language.traineddata" \
        "https://github.com/tesseract-ocr/tessdata_fast/raw/$tessdata_tag/$language.traineddata"
done
(cd "$out/tessdata" && shasum -a 256 -c - <<EOF
$chi_sim_sha256  chi_sim.traineddata
$eng_sha256  eng.traineddata
EOF
)

# Every library the staged code loads must ship with it or with macOS.
foreign=$(find "$out" -type f -perm -u+x -o -type f \( -name '*.so' -o -name '*.dylib' \) |
    while IFS= read -r file; do
        file -b "$file" | grep -q Mach-O || continue
        # Dependencies only: a library's own install name is not loaded.
        otool -l "$file" |
            awk '/cmd LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD)_DYLIB$/ { getline; getline; print $2 }' |
            grep -v -E '^(/usr/lib/|/System/Library/|@rpath/|@loader_path/|@executable_path/)' |
            sed "s|^|$file: |"
    done)
if [ -n "$foreign" ]; then
    printf 'libraries outside the bundle:\n%s\n' "$foreign" >&2
    exit 1
fi
# The server imports, with the compiled PostgreSQL driver, outside any shell
# environment.
env -i PYTHONDONTWRITEBYTECODE=1 "$python" -c 'import app.api.main, app.jobs.worker
from psycopg import pq
assert pq.__impl__ == "binary", pq.__impl__'
du -sh "$out"
