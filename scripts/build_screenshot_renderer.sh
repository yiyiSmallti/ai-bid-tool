#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work_dir="$repository_dir/data/work"
tool_dir="$work_dir/tooling/rust"
target_dir="$work_dir/build/screenshot-renderer"
output_dir="$work_dir/bin"

export CARGO_HOME="$tool_dir/cargo-home"
export RUSTUP_HOME="$tool_dir/rustup-home"
export CARGO_TARGET_DIR="$target_dir"
export PATH="$CARGO_HOME/bin:$PATH"

mkdir -p "$CARGO_HOME" "$RUSTUP_HOME" "$CARGO_TARGET_DIR" "$output_dir"
if [ ! -f "$repository_dir/stamp/Cargo.lock" ]; then
    cargo generate-lockfile --manifest-path "$repository_dir/stamp/Cargo.toml"
fi
cargo fmt --manifest-path "$repository_dir/stamp/Cargo.toml" -- --check
cargo check --locked --manifest-path "$repository_dir/stamp/Cargo.toml"
cargo clippy --locked --manifest-path "$repository_dir/stamp/Cargo.toml" -- -D warnings
cargo test --locked --manifest-path "$repository_dir/stamp/Cargo.toml"
cargo build --locked --release --manifest-path "$repository_dir/stamp/Cargo.toml"
install -m 0755 "$CARGO_TARGET_DIR/release/bid-screenshot-renderer" \
    "$output_dir/bid-screenshot-renderer"
printf '%s\n' "$output_dir/bid-screenshot-renderer"
