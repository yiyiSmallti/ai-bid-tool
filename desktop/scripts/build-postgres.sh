#!/bin/sh
# Builds the PostgreSQL server and client tools the app runs, from the pinned
# source release, into a relocatable tree: bin, lib and share keep their
# relative layout, which PostgreSQL resolves from its own executable, and
# libpq is found through @rpath, so the tree runs from wherever it is copied.
# Usage: build-postgres.sh OUTPUT_DIR. The tree is cached under data/work and
# rebuilt only when the release or this script changes.
set -eu

out=${1:?usage: build-postgres.sh OUTPUT_DIR}
desktop_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repository_dir=$(dirname -- "$desktop_dir")
work_dir="$repository_dir/data/work/desktop-stage/postgres"

version=16.15
source_sha256=c1575341fa7bd40f5274ea465b34390f4dc64cdd0770af327005caaeb9f6b7ed
# The app's LSMinimumSystemVersion.
export MACOSX_DEPLOYMENT_TARGET=12.0
# Only these tools run: the backend starts and stops the cluster, migrations and
# backups use the client tools, and the backup how-to restores with them.
tools="postgres initdb pg_ctl psql createdb dropdb pg_dump pg_restore pg_controldata"

cache="$work_dir/pgsql-$version"
stamp="$version $(shasum -a 256 "$0" | cut -d' ' -f1)"
if [ -f "$cache.stamp" ] && [ "$(cat "$cache.stamp")" = "$stamp" ] && [ -d "$cache" ]; then
    rm -rf "$out"
    mkdir -p "$(dirname -- "$out")"
    cp -R "$cache" "$out"
    exit 0
fi

archive="$work_dir/postgresql-$version.tar.bz2"
mkdir -p "$work_dir"
if [ ! -f "$archive" ]; then
    curl -fsSL -o "$archive.partial" \
        "https://ftp.postgresql.org/pub/source/v$version/postgresql-$version.tar.bz2"
    mv "$archive.partial" "$archive"
fi
echo "$source_sha256  $archive" | shasum -a 256 -c - >/dev/null

build="$work_dir/build-$version"
rm -rf "$build"
mkdir -p "$build"
tar -xjf "$archive" -C "$build"
src="$build/postgresql-$version"
# The configured prefix only names paths relative to each other: the files are
# installed under DESTDIR and the binaries find their libraries and data from
# their own location. A neutral prefix keeps the builder's paths out of them;
# without "pgsql" in it, modules and data go to lib/postgresql and
# share/postgresql, apart from libpq.
prefix=/aibid
destdir="$build/install"
# The cluster uses the C locale, a private Unix socket and no client prompts, so
# ICU, OpenSSL and readline are not needed; zlib comes with macOS.
(
    cd "$src"
    ./configure --quiet --prefix="$prefix" --without-icu --without-readline \
        --without-openssl --without-llvm --disable-nls --disable-debug
    make -s -j "$(sysctl -n hw.ncpu)"
    make -s install DESTDIR="$destdir"
) >"$build/build.log" 2>&1 || {
    tail -n 40 "$build/build.log" >&2
    exit 1
}

installed="$destdir$prefix"
tree="$build/tree"
mkdir -p "$tree/bin"
for tool in $tools; do
    cp "$installed/bin/$tool" "$tree/bin/"
done
mkdir -p "$tree/lib"
cp -R "$installed/lib/postgresql" "$tree/lib/"
cp "$installed/lib/libpq.5.dylib" "$tree/lib/"
rm -rf "$tree/lib/postgresql/pgxs"
mkdir -p "$tree/share"
cp -R "$installed/share/postgresql" "$tree/share/"

# Point every reference to an installed library at @rpath, and give each image
# an rpath to lib relative to itself: bin/* and lib/postgresql/* load libpq.
macho_files() {
    find "$tree" -type f | while IFS= read -r file; do
        file -b "$file" | grep -q Mach-O && printf '%s\n' "$file"
    done
}
macho_files | while IFS= read -r file; do
    for old in $(otool -l "$file" |
        awk '/cmd LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD)_DYLIB$/ { getline; getline; print $2 }' |
        grep "^$prefix/lib/"); do
        install_name_tool -change "$old" "@rpath/$(basename -- "$old")" "$file"
    done
    for old in $(otool -l "$file" | awk '/cmd LC_RPATH$/ { getline; getline; print $2 }'); do
        install_name_tool -delete_rpath "$old" "$file"
    done
    case $file in
    "$tree/bin/"*) install_name_tool -add_rpath @loader_path/../lib "$file" ;;
    "$tree/lib/postgresql/"*) install_name_tool -add_rpath @loader_path/.. "$file" ;;
    esac
done
install_name_tool -id @rpath/libpq.5.dylib "$tree/lib/libpq.5.dylib"
# Changing load commands invalidates the linker's signature; arm64 runs only
# signed code, so each image is signed ad hoc again. Release signing replaces it.
macho_files | while IFS= read -r file; do
    codesign --force --sign - "$file" 2>/dev/null
done

# Every library must come with the tree or with macOS.
foreign=$(macho_files | while IFS= read -r file; do
    otool -l "$file" |
        awk '/cmd LC_(LOAD|LOAD_WEAK|REEXPORT|LAZY_LOAD)_DYLIB$/ { getline; getline; print $2 }' |
        grep -v -E '^(/usr/lib/|/System/Library/|@rpath/)' | sed "s|^|$file: |"
done)
if [ -n "$foreign" ]; then
    printf 'libraries outside the tree:\n%s\n' "$foreign" >&2
    exit 1
fi

# Run the tree from a path other than the one it was built in: create a
# cluster, start it on a private socket and load a procedural-language module.
check="$build/check"
mkdir -p "$check"
mv "$tree" "$check/pgsql"
pg="$check/pgsql/bin"
socket=$(mktemp -d /tmp/aibid-pgcheck.XXXXXX)
env -i PATH=/usr/bin:/bin "$pg/initdb" -D "$check/cluster" -U bid_owner -E UTF8 --locale=C \
    --auth-local=trust --auth-host=reject >"$check/initdb.log"
env -i PATH=/usr/bin:/bin "$pg/pg_ctl" -D "$check/cluster" -l "$check/postgres.log" -w \
    -o "-c listen_addresses= -k $socket" start >/dev/null
status=0
env -i PATH=/usr/bin:/bin "$pg/psql" -h "$socket" -U bid_owner -d postgres -v ON_ERROR_STOP=1 -qtA -c \
    "DO \$\$ BEGIN PERFORM 1; END \$\$; SELECT convert('é', 'UTF8', 'LATIN1'), to_tsvector('simple', 'probe')" \
    >/dev/null || status=$?
env -i PATH=/usr/bin:/bin "$pg/pg_ctl" -D "$check/cluster" -m fast -w stop >/dev/null
rm -rf "$socket"
[ "$status" -eq 0 ]

rm -rf "$cache" "$cache.stamp"
mv "$check/pgsql" "$cache"
printf '%s\n' "$stamp" >"$cache.stamp"
rm -rf "$build"
rm -rf "$out"
mkdir -p "$(dirname -- "$out")"
cp -R "$cache" "$out"
