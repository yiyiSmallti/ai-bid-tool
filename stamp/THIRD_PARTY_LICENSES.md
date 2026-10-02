---
kind: reference
---

# Third-party licenses

The renderer embeds `NotoSansSC-Renderer.ttf`, a build-time subset of Noto Sans SC 2.004.
Its original file SHA-256 is
`a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`.
The subset contains printable ASCII plus the fixed Chinese footer marker `已脱敏`, and fixes
the `wght` variation axis at 400. The font copyright and SIL Open Font License 1.1 are in
[`assets/OFL.txt`](assets/OFL.txt).

Rust direct dependency versions are exact-pinned in `Cargo.toml`; the build script creates
`Cargo.lock` before its first build and then uses `--locked`. License metadata can be checked
directly in the locked crates.io packages. `image`, `serde`, `serde_json`, and `sha2` permit
MIT or Apache-2.0; `fontdue` permits MIT, Apache-2.0, or Zlib.
